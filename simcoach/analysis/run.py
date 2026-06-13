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
        f"**Session:** {len(laps)} laps, {len(valid_laps)} valid. "
        f"Best valid lap: {format_time(best)}. "
        f"{reference_summary(reference_mode, reference_lap_id)}"
    )
    lines = [
        title,
        summary,
        "",
        "## The one thing",
    ]
    status = selected_delta["analysis_status"]
    if status == "reportable":
        delta = selected_delta["selected_delta"]
        lines.append(instruction_for_delta(delta, session, selected_delta.get("reference_lap_id")))
    else:
        lines.append(non_reportable_message(selected_delta, len(valid_laps)))

    lines.extend(["", "## Why (the data)"])
    if status == "reportable":
        delta = selected_delta["selected_delta"]
        lines.extend(reportable_evidence_lines(delta, session))
    else:
        lines.append(non_reportable_data_message(selected_delta, len(valid_laps)))

    lines.extend(["", "## Checked areas"])
    summaries = selected_delta.get("corner_summaries") or []
    if not summaries:
        lines.append("No usable Corner Segment summaries were available.")
    else:
        for summary in summaries[:8]:
            cause = summary.get("dominant_cause")
            fraction = format_percent(summary.get("dominant_cause_lap_fraction"))
            classification = classification_label(summary["classification"])
            cause_text = (
                f"; {metric_label(str(cause)).lower()} gap on {fraction} of comparison laps"
                if cause and fraction
                else ""
            )
            lines.append(
                f"- {segment_display_name(summary['corner_segment_id'])}: {classification}; "
                f"{format_segment_range(summary.get('segment_range'), session)}; "
                f"median loss {summary['median_corner_loss_s']:.3f}s; "
                f"variation estimate {summary['robust_noise_s']:.3f}s{cause_text}; "
                f"{reason_label(summary['reason'])}."
            )
    lines.append("")
    return "\n".join(lines)


def instruction_for_delta(
    delta: dict[str, Any],
    session: dict[str, Any],
    reference_lap_id: object,
) -> str:
    return (
        f"Next stint: {instruction_target(delta, session)} "
        f"Basis: median of {delta['comparison_lap_count']} comparison laps versus "
        f"{format_reference_lap(reference_lap_id) or 'the Reference Lap'}; "
        f"repeatable loss {delta['median_corner_loss_s']:.3f}s."
    )


def instruction_target(delta: dict[str, Any], session: dict[str, Any]) -> str:
    metric = delta["cause_metric"]
    bad_delta = float(metric["bad_direction_delta"])
    location = format_instruction_location(delta.get("segment_range"), session)
    if metric["unit"] == "m/s":
        return (
            f"use {location} as the focus and try to keep the car rolling about "
            f"{format_speed_delta(bad_delta)} faster at the slowest point than your typical "
            f"comparison lap. The data identifies the speed loss, not the exact input change."
        )
    if metric["metric"] == "brake_point":
        return f"brake about {format_lap_delta(bad_delta, session)} later at {location}."
    if metric["metric"] == "throttle_reapplication":
        return (
            f"return to throttle about {format_lap_delta(bad_delta, session)} earlier "
            f"at {location}."
        )
    if metric["metric"] == "coast_duration":
        return f"trim about {bad_delta:.3f}s of extra coasting at {location}."
    return f"close a {format_lap_delta(bad_delta, session)} gap at {location}."


def non_reportable_message(selected_delta: dict[str, Any], valid_lap_count: int) -> str:
    reason = selected_delta.get("reason", "unknown_reason")
    comparison_count = int(selected_delta.get("comparison_lap_count", 0))
    minimum = AnalysisThresholds().min_comparison_laps
    if reason == "fewer_than_minimum_comparison_laps":
        needed = max(0, minimum - comparison_count)
        return (
            "No data-supported Coaching Instruction. "
            f"There were {valid_lap_count} valid laps, but after selecting the reference lap "
            f"only {comparison_count} comparison lap(s) remained; Open Race Coach needs at least "
            f"{minimum}. Record {needed} more valid comparison lap(s) before trusting a coach tip."
        )
    return f"No data-supported Coaching Instruction. {reason_label(reason)}."


def non_reportable_data_message(selected_delta: dict[str, Any], valid_lap_count: int) -> str:
    reason = selected_delta.get("reason", "unknown_reason")
    comparison_count = int(selected_delta.get("comparison_lap_count", 0))
    minimum = AnalysisThresholds().min_comparison_laps
    if reason == "fewer_than_minimum_comparison_laps":
        return (
            f"Evidence was too thin: {valid_lap_count} valid lap(s), {comparison_count} "
            f"comparison lap(s) after reference selection, minimum required {minimum}."
        )
    return f"No instruction was selected: {reason_label(reason)}."


def reportable_evidence_lines(delta: dict[str, Any], session: dict[str, Any]) -> list[str]:
    metric = delta["cause_metric"]
    fraction = format_percent(delta.get("dominant_cause_lap_fraction"))
    segment_name = segment_display_name(delta["corner_segment_id"])
    return [
        (
            f"Selected area: {segment_name}, "
            f"{format_segment_range(delta.get('segment_range'), session)}."
        ),
        (
            f"Loss: {delta['median_corner_loss_s']:.3f}s median over "
            f"{delta['comparison_lap_count']} comparison laps."
        ),
        f"Measured gap: {format_cause_metric(metric, session)}",
        consistency_line(delta, fraction),
    ]


def consistency_line(delta: dict[str, Any], fraction: str | None) -> str:
    if fraction is None:
        return "Confidence: repeatability count was not recorded for this area."
    return (
        f"Confidence: the {metric_label(delta['dominant_cause']).lower()} gap showed up on "
        f"{fraction} of comparison laps and cleared the repeatability threshold."
    )


def format_cause_metric(metric: dict[str, Any], session: dict[str, Any]) -> str:
    unit = metric["unit"]
    reference = float(metric["reference_value"])
    comparison = float(metric["comparison_median"])
    bad_delta = float(metric["bad_direction_delta"])
    name = str(metric["metric"])
    if unit == "m/s":
        return (
            f"{metric_label(name)} was {format_speed(reference)} on the reference lap and "
            f"{format_speed(comparison)} on your typical comparison lap, a "
            f"{format_speed_delta(bad_delta)} deficit."
        )
    if unit == "lap_dist_pct":
        return (
            f"{metric_label(name)} was {format_lap_point(reference, session)} on the reference lap "
            f"and {format_lap_point(comparison, session)} on your typical comparison lap, "
            f"{format_lap_delta(bad_delta, session)} worse."
        )
    return (
        f"{metric_label(name)} was {reference:.3f}s on the reference lap and "
        f"{comparison:.3f}s on your typical comparison lap, {bad_delta:.3f}s worse."
    )


def metric_label(metric: str) -> str:
    return {
        "brake_point": "Brake point",
        "min_speed": "Minimum speed",
        "throttle_reapplication": "Throttle reapplication",
        "coast_duration": "Coast duration",
    }.get(metric, metric)


def format_segment_range(range_data: dict[str, Any] | None, session: dict[str, Any]) -> str:
    if not range_data:
        return "segment range unavailable"
    start_pct = float(range_data["start_lap_dist_pct"])
    end_pct = float(range_data["end_lap_dist_pct"])
    start_m = range_data.get("start_lap_dist_m")
    end_m = range_data.get("end_lap_dist_m")
    source = range_data.get("lap_dist_m_source")
    if source in {"sim", "derived_from_track_length"} and start_m is not None and end_m is not None:
        start_text = format_distance(float(start_m), source)
        end_text = format_distance(float(end_m), source)
        return f"{start_text}-{end_text} from lap start"
    return f"{start_pct:.1%}-{end_pct:.1%} lap distance"


def format_instruction_location(range_data: dict[str, Any] | None, session: dict[str, Any]) -> str:
    range_text = format_segment_range(range_data, session)
    if is_broad_segment(range_data):
        return f"the broad corner segment at {range_text}"
    return f"the corner segment at {range_text}"


def is_broad_segment(range_data: dict[str, Any] | None) -> bool:
    if not range_data:
        return False
    span = float(range_data["end_lap_dist_pct"]) - float(range_data["start_lap_dist_pct"])
    return span >= 0.35


def format_lap_point(value: float, session: dict[str, Any]) -> str:
    pct_text = f"{value:.1%} lap distance"
    meters = lap_pct_to_meters(value, session)
    if meters is None:
        return pct_text
    source = session.get("lap_dist_m_source")
    return f"{pct_text} ({format_distance(meters, source)})"


def format_lap_delta(value: float, session: dict[str, Any]) -> str:
    pct_points = value * 100.0
    meters = lap_pct_to_meters(value, session)
    if meters is None:
        return f"{pct_points:.1f} percentage points of lap distance"
    source = session.get("lap_dist_m_source")
    return f"{pct_points:.1f} percentage points ({format_distance(meters, source)})"


def lap_pct_to_meters(value: float, session: dict[str, Any]) -> float | None:
    if session.get("lap_dist_m_source") not in {"sim", "derived_from_track_length"}:
        return None
    track_length = session.get("track_length_m")
    if track_length is None:
        return None
    return abs(value) * float(track_length)


def format_distance(value: float, source: object) -> str:
    if source == "derived_from_track_length":
        rounded = round(value / 10.0) * 10
        return f"about {rounded:.0f} m"
    return f"{value:.0f} m"


def format_speed(value_mps: float) -> str:
    return f"{value_mps:.1f} m/s ({value_mps * 3.6:.1f} km/h)"


def format_speed_delta(value_mps: float) -> str:
    return f"{value_mps:.1f} m/s ({value_mps * 3.6:.1f} km/h)"


def format_percent(value: object) -> str | None:
    if value is None:
        return None
    return f"{float(value):.0%}"


def classification_label(classification: str) -> str:
    return {
        "reportable_candidate": "selected issue",
        "consistent": "no meaningful repeatable loss",
        "inconsistent": "loss was not repeatable enough",
        "insufficient_data": "not enough comparable data",
    }.get(classification, classification.replace("_", " "))


def reason_label(reason: str) -> str:
    return {
        "passes_loss_noise_and_cause_gates": (
            "gap is large enough and repeatable enough to act on"
        ),
        "no_significant_repeatable_loss": "no significant repeatable loss was found",
        "significant_loss_without_dominant_repeatable_cause": (
            "there was time loss, but no single repeatable cause dominated"
        ),
        "insufficient_comparable_data": "there was not enough comparable data",
        "fewer_than_minimum_comparison_laps": (
            "not enough comparison laps survived reference selection"
        ),
        "no_usable_corner_segments": "the reference lap did not produce usable corner segments",
        "no_valid_reference_lap": "there was no valid reference lap",
        "all_corners_lacked_comparable_data": "all corner segments lacked comparable data",
        "top_candidates_inside_single_dominant_margin": (
            "the top candidate issues were too close to choose only one"
        ),
        "unknown_reason": "the analyzer did not record a specific reason",
    }.get(reason, reason.replace("_", " "))


def format_time(value: float | None) -> str:
    if value is None:
        return "none"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes}:{seconds:06.3f}"


def reference_summary(reference_mode: str, reference_lap_id: object) -> str:
    lap_text = format_reference_lap(reference_lap_id)
    if reference_mode == "best":
        if lap_text:
            return f"Reference: session-best {lap_text}."
        return "Reference: no valid session-best lap."
    if reference_mode == "personal":
        if lap_text:
            return f"Reference: personal-best {lap_text}."
        return "Reference: no valid personal-best lap."
    if lap_text:
        return f"Reference: {lap_text}."
    return "Reference: none."


def format_reference_lap(reference_lap_id: object) -> str | None:
    if not reference_lap_id:
        return None
    text = str(reference_lap_id)
    marker = ":lap:"
    if marker in text:
        lap = text.rsplit(marker, 1)[1]
        if lap:
            return f"lap {lap}"
    return "selected lap"


def segment_display_name(segment_id: object) -> str:
    text = str(segment_id)
    if text.startswith("C") and text[1:].isdigit():
        return f"detected corner segment {int(text[1:])}"
    return f"detected corner segment {text}"


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
