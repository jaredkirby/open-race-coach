"""Deterministic Analysis Run orchestration."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from simcoach.analysis.confidence import AnalysisThresholds
from simcoach.analysis.deltas import (
    analyze_deltas,
    assert_supported_complete_session,
    comparison_laps,
    non_reportable_selected_delta,
    read_session_artifacts,
    resample_lap,
    select_reference_lap,
)
from simcoach.analysis.segmentation import SegmentThresholds, derive_corner_segments
from simcoach.ingest.session import atomic_write_text, atomic_write_yaml
from simcoach.utils.llm_logger import get_logger

LOGGER = get_logger(__name__)
ANALYZER_VERSION = "0.1.0"


def analyze_session(
    session_dir: Path,
    *,
    reference_mode: Literal["best", "personal"] = "best",
    sessions_root: Path | None = None,
) -> Path:
    LOGGER.info(
        "[START] analyze_session | session_dir=%s reference_mode=%s",
        session_dir,
        reference_mode,
    )
    session, ticks, laps = read_session_artifacts(session_dir)
    assert_supported_complete_session(session)

    created_at = datetime.now().astimezone()
    run_dir = make_analysis_run_dir(session_dir, created_at, reference_mode)
    run_dir.mkdir(parents=True)

    thresholds = AnalysisThresholds()
    segment_thresholds = SegmentThresholds()
    try:
        reference = select_reference_lap(
            session_dir,
            session,
            ticks,
            laps,
            reference_mode=reference_mode,
            sessions_root=sessions_root,
        )

        if reference is None:
            corner_segments = {
                "corner_segments_schema_version": 1,
                "recorded_session_id": session["recorded_session_id"],
                "reference_lap_id": None,
                "segments": [],
                "empty_reason": "no_valid_reference_lap",
            }
            selected_delta = non_reportable_selected_delta(
                "insufficient_data",
                "no_valid_reference_lap",
                [],
                None,
                [],
            )
        else:
            reference_resampled = resample_lap(reference.ticks, reference.lap_record)
            corner_segments = derive_corner_segments(
                reference_resampled,
                lap_dist_m_source=session["lap_dist_m_source"],
                thresholds=segment_thresholds,
            )
            corner_segments["recorded_session_id"] = session["recorded_session_id"]
            corner_segments["reference_lap_id"] = reference.lap_record["lap_id"]
            comparisons = comparison_laps(session_dir, ticks, laps, reference)
            selected_delta = analyze_deltas(
                reference,
                comparisons,
                ticks,
                corner_segments["segments"],
                thresholds=thresholds,
                lap_dist_m_source=session["lap_dist_m_source"],
            )

        report = render_coach_report(
            session,
            run_dir,
            laps,
            reference_mode,
            selected_delta,
        )
        write_json(run_dir / "corner_segments.json", corner_segments)
        write_json(run_dir / "selected_delta.json", selected_delta)
        atomic_write_text(run_dir / "coach_report.md", report)
        analysis_yaml = build_analysis_yaml(
            session_dir,
            session,
            run_dir,
            created_at,
            reference_mode,
            reference,
            selected_delta,
            thresholds,
            segment_thresholds,
        )
        atomic_write_yaml(run_dir / "analysis.yaml", analysis_yaml)
        atomic_write_text(session_dir / "coach_report.md", report)
    except Exception as exc:
        failed_yaml = build_failed_analysis_yaml(
            session_dir,
            session,
            run_dir,
            created_at,
            reference_mode,
            thresholds,
            segment_thresholds,
            classify_run_error(exc),
        )
        atomic_write_yaml(run_dir / "analysis.yaml", failed_yaml)
        for artifact in (
            "corner_segments.json",
            "selected_delta.json",
            "coach_report.md",
            "coach_prompt.md",
            "coach_response.json",
        ):
            (run_dir / artifact).unlink(missing_ok=True)
        LOGGER.exception("[ERROR] analyze_session | failed run_dir=%s error=%s", run_dir, exc)
        raise
    LOGGER.info(
        "[END] analyze_session | run_dir=%s status=%s",
        run_dir,
        selected_delta["analysis_status"],
    )
    print_summary(run_dir, selected_delta)
    return run_dir


def make_analysis_run_dir(
    session_dir: Path,
    created_at: datetime,
    reference_mode: str,
) -> Path:
    run_dir = (
        session_dir / "analysis" / f"{created_at.strftime('%Y-%m-%d_%H%M%S')}_{reference_mode}"
    )
    if run_dir.exists():
        raise FileExistsError(f"Analysis Run directory already exists: {run_dir}")
    return run_dir


def build_analysis_yaml(
    session_dir: Path,
    session: dict[str, Any],
    run_dir: Path,
    created_at: datetime,
    reference_mode: str,
    reference: Any,
    selected_delta: dict[str, Any],
    thresholds: AnalysisThresholds,
    segment_thresholds: SegmentThresholds,
) -> dict[str, Any]:
    return {
        "analysis_schema_version": 1,
        "corner_segments_schema_version": 1,
        "selected_delta_schema_version": 1,
        "coach_response_schema_version": None,
        "recorded_session_path": str(session_dir),
        "recorded_session_id": session["recorded_session_id"],
        "reference_mode": reference_mode,
        "reference_lap_id": reference.lap_record["lap_id"] if reference else None,
        "reference_lap_source_path": str(reference.session_dir) if reference else None,
        "analyzer_version": ANALYZER_VERSION,
        "effective_thresholds": {
            **thresholds.as_dict(),
            "segmentation": segment_thresholds.as_dict(),
        },
        "run_status": "complete",
        "run_error": None,
        "coach_refinement_mode": None,
        "coach_refinement_status": "not_requested",
        "coach_refinement_error": None,
        "analysis_status": selected_delta["analysis_status"],
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "analysis_run_path": str(run_dir),
    }


def build_failed_analysis_yaml(
    session_dir: Path,
    session: dict[str, Any],
    run_dir: Path,
    created_at: datetime,
    reference_mode: str,
    thresholds: AnalysisThresholds,
    segment_thresholds: SegmentThresholds,
    run_error: str,
) -> dict[str, Any]:
    return {
        "analysis_schema_version": 1,
        "corner_segments_schema_version": None,
        "selected_delta_schema_version": None,
        "coach_response_schema_version": None,
        "recorded_session_path": str(session_dir),
        "recorded_session_id": session["recorded_session_id"],
        "reference_mode": reference_mode,
        "reference_lap_id": None,
        "reference_lap_source_path": None,
        "analyzer_version": ANALYZER_VERSION,
        "effective_thresholds": {
            **thresholds.as_dict(),
            "segmentation": segment_thresholds.as_dict(),
        },
        "run_status": "failed",
        "run_error": run_error,
        "coach_refinement_mode": None,
        "coach_refinement_status": "not_requested",
        "coach_refinement_error": None,
        "analysis_status": None,
        "created_at": created_at.isoformat(),
        "updated_at": datetime.now().astimezone().isoformat(),
        "analysis_run_path": str(run_dir),
    }


def classify_run_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "missing_required_artifact"
    if isinstance(exc, KeyError | TypeError | ValueError):
        return "invalid_artifact_contract"
    return "analysis_exception"


def render_coach_report(
    session: dict[str, Any],
    run_dir: Path,
    laps: list[dict[str, Any]],
    reference_mode: str,
    selected_delta: dict[str, Any],
) -> str:
    valid_laps = [lap for lap in laps if lap.get("valid") is True]
    best = min((lap["lap_time_s"] for lap in valid_laps), default=None)
    reference_lap_id = selected_delta.get("reference_lap_id")
    title = (
        f"# Coach Report - {session['track_raw']} / {session['car_raw']} / "
        f"{session['started_at'][:10]}"
    )
    summary = (
        f"**Session:** {len(laps)} laps, {len(valid_laps)} valid. Best: {format_time(best)}. "
        f"Reference Mode: {reference_mode}. Reference Lap: {reference_lap_id or 'none'}."
    )
    lines = [
        title,
        f"**Analysis Run:** `{run_dir}`.",
        summary,
        "",
        "## The one thing",
    ]
    status = selected_delta["analysis_status"]
    if status == "reportable":
        delta = selected_delta["selected_delta"]
        lines.append(instruction_for_delta(delta))
    else:
        lines.append(non_reportable_message(status, selected_delta.get("reason", "unknown_reason")))

    lines.extend(["", "## Why (the data)"])
    if status == "reportable":
        delta = selected_delta["selected_delta"]
        metric = delta["cause_metric"]
        lines.append(
            f"{delta['corner_segment_id']}: median loss {delta['median_corner_loss_s']:.3f}s "
            f"over {delta['comparison_lap_count']} comparison laps. Dominant cause: "
            f"{delta['dominant_cause']} "
            f"({metric['bad_direction_delta']} {metric['unit']} bad-direction delta)."
        )
    else:
        lines.append(
            f"No Coaching Instruction was selected because `{status}`: "
            f"{selected_delta.get('reason', 'unknown_reason')}."
        )

    lines.extend(["", "## Consistency check"])
    summaries = selected_delta.get("corner_summaries") or []
    if not summaries:
        lines.append("No usable Corner Segment summaries were available.")
    else:
        for summary in summaries[:8]:
            lines.append(
                f"- {summary['corner_segment_id']}: {summary['classification']}; "
                f"median loss {summary['median_corner_loss_s']:.3f}s; "
                f"noise {summary['robust_noise_s']:.3f}s; {summary['reason']}."
            )
    lines.append("")
    return "\n".join(lines)


def instruction_for_delta(delta: dict[str, Any]) -> str:
    cause = delta["dominant_cause"]
    segment_id = delta["corner_segment_id"]
    if cause == "brake_point":
        action = "brake later"
    elif cause == "min_speed":
        action = "carry more minimum speed"
    elif cause == "throttle_reapplication":
        action = "return to throttle earlier"
    else:
        action = "reduce coasting"
    return (
        f"{action} in {segment_id}. The repeatable loss is {delta['median_corner_loss_s']:.3f}s, "
        f"and the deterministic cause gate selected `{cause}`."
    )


def non_reportable_message(status: str, reason: str) -> str:
    return (
        f"No data-supported Coaching Instruction. Deterministic status is `{status}` (`{reason}`)."
    )


def format_time(value: float | None) -> str:
    if value is None:
        return "none"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes}:{seconds:06.3f}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def print_summary(run_dir: Path, selected_delta: dict[str, Any]) -> None:
    status = selected_delta["analysis_status"]
    if status == "reportable":
        delta = selected_delta["selected_delta"]
        print(
            f"{run_dir} | reportable {delta['corner_segment_id']} "
            f"{delta['dominant_cause']} loss={delta['median_corner_loss_s']:.3f}s"
        )
    else:
        print(f"{run_dir} | {status} {selected_delta.get('reason', '')}")
