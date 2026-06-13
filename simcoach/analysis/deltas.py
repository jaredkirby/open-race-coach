"""Reference selection, resampling, and deterministic lap delta analysis."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml

from simcoach.analysis.confidence import AnalysisThresholds, robust_noise, sample_stddev
from simcoach.analysis.segmentation import GRID_EDGES
from simcoach.utils.llm_logger import get_logger

Cause = Literal["brake_point", "min_speed", "throttle_reapplication", "coast_duration"]
CAUSE_ORDER: tuple[Cause, ...] = (
    "brake_point",
    "min_speed",
    "throttle_reapplication",
    "coast_duration",
)
LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReferenceLap:
    lap_record: dict[str, Any]
    session_dir: Path
    ticks: pd.DataFrame


def read_session_artifacts(
    session_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    session_yaml_path = session_dir / "session.yaml"
    ticks_path = session_dir / "ticks.parquet"
    laps_path = session_dir / "laps.jsonl"
    if not session_yaml_path.exists() or not ticks_path.exists() or not laps_path.exists():
        raise FileNotFoundError(
            "Recorded Session is missing session.yaml, ticks.parquet, or laps.jsonl"
        )
    session = yaml.safe_load(session_yaml_path.read_text(encoding="utf-8"))
    ticks = pd.read_parquet(ticks_path)
    laps = [
        json.loads(line)
        for line in laps_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return session, ticks, laps


def assert_supported_complete_session(session: dict[str, Any]) -> None:
    if session.get("complete") is not True:
        raise ValueError("Recorded Session is incomplete")
    for key in ("session_schema_version", "tick_schema_version", "lap_schema_version"):
        if session.get(key) != 1:
            raise ValueError(f"unsupported {key}: {session.get(key)!r}")


def select_reference_lap(
    session_dir: Path,
    session: dict[str, Any],
    ticks: pd.DataFrame,
    laps: list[dict[str, Any]],
    *,
    reference_mode: Literal["best", "personal"],
    sessions_root: Path | None = None,
) -> ReferenceLap | None:
    if reference_mode == "best":
        record = _fastest_valid_lap(laps)
        return ReferenceLap(record, session_dir, ticks) if record else None
    return _select_personal_reference(
        session_dir, session, ticks, laps, sessions_root=sessions_root
    )


def comparison_laps(
    target_session_dir: Path,
    target_ticks: pd.DataFrame,
    target_laps: list[dict[str, Any]],
    reference: ReferenceLap,
) -> list[dict[str, Any]]:
    comparisons = [lap for lap in target_laps if lap.get("valid") is True]
    if reference.session_dir.resolve() == target_session_dir.resolve():
        comparisons = [
            lap for lap in comparisons if lap["lap_id"] != reference.lap_record["lap_id"]
        ]
    return comparisons


def resample_lap(ticks: pd.DataFrame, lap_record: dict[str, Any]) -> dict[str, np.ndarray]:
    start, end = lap_record["tick_range"]
    lap_ticks = ticks.iloc[start:end].copy()
    if len(lap_ticks) < 2:
        raise ValueError(f"lap {lap_record['lap_id']} has too few ticks")
    lap_ticks = lap_ticks.sort_values("lap_dist_pct")
    pct = lap_ticks["lap_dist_pct"].to_numpy(dtype=float)
    unique_pct, unique_indexes = np.unique(pct, return_index=True)
    if len(unique_pct) < 2:
        raise ValueError(f"lap {lap_record['lap_id']} has unusable Lap Progress")
    lap_ticks = lap_ticks.iloc[unique_indexes]
    pct = unique_pct

    result: dict[str, np.ndarray] = {}
    elapsed = lap_ticks["t"].to_numpy(dtype=float) - float(lap_ticks["t"].iloc[0])
    result["elapsed"] = np.interp(GRID_EDGES, pct, elapsed)
    for column in ("speed", "throttle", "brake", "steering", "pos_x", "pos_y", "pos_z"):
        result[column] = np.interp(GRID_EDGES, pct, lap_ticks[column].to_numpy(dtype=float))
    if "lap_dist_m" in lap_ticks and not lap_ticks["lap_dist_m"].isna().all():
        lap_dist_m = lap_ticks["lap_dist_m"].ffill().bfill()
        result["lap_dist_m"] = np.interp(
            GRID_EDGES,
            pct,
            lap_dist_m.to_numpy(dtype=float),
        )
    else:
        result["lap_dist_m"] = np.full_like(GRID_EDGES, np.nan, dtype=float)
    return result


def analyze_deltas(
    reference: ReferenceLap,
    comparison_records: list[dict[str, Any]],
    target_ticks: pd.DataFrame,
    corner_segments: list[dict[str, Any]],
    *,
    thresholds: AnalysisThresholds | None = None,
    lap_dist_m_source: str,
) -> dict[str, Any]:
    thresholds = thresholds or AnalysisThresholds()
    if len(comparison_records) < thresholds.min_comparison_laps:
        return non_reportable_selected_delta(
            "insufficient_data",
            "fewer_than_minimum_comparison_laps",
            comparison_records,
            reference.lap_record,
            [],
        )
    if not corner_segments:
        return non_reportable_selected_delta(
            "insufficient_data",
            "no_usable_corner_segments",
            comparison_records,
            reference.lap_record,
            [],
        )

    reference_resampled = resample_lap(reference.ticks, reference.lap_record)
    reference_metrics = {
        segment["corner_segment_id"]: segment_metrics(reference_resampled, segment)
        for segment in corner_segments
    }
    corner_summaries: list[dict[str, Any]] = []
    reportable_candidates: list[dict[str, Any]] = []

    for segment in corner_segments:
        segment_id = segment["corner_segment_id"]
        start = int(segment["start_edge_idx"])
        end = int(segment["end_edge_idx"])
        reference_metric = reference_metrics[segment_id]
        losses: list[float] = []
        assigned_causes: list[Cause | Literal["unclassified"]] = []
        comparison_metrics: list[dict[str, float | None]] = []
        for lap in comparison_records:
            resampled = resample_lap(target_ticks, lap)
            loss = segment_time(resampled, start, end) - segment_time(
                reference_resampled, start, end
            )
            metrics = segment_metrics(resampled, segment)
            losses.append(float(loss))
            comparison_metrics.append(metrics)
            assigned_causes.append(assign_cause(loss, reference_metric, metrics, thresholds))

        median_loss = float(np.median(losses))
        noise = robust_noise(losses)
        stddev = sample_stddev(losses)
        cause_counter = Counter(cause for cause in assigned_causes if cause != "unclassified")
        dominant_cause: Cause | None = None
        dominant_fraction = 0.0
        if cause_counter:
            dominant_cause = max(
                CAUSE_ORDER, key=lambda cause: (cause_counter[cause], -CAUSE_ORDER.index(cause))
            )
            dominant_fraction = cause_counter[dominant_cause] / len(comparison_records)

        classification = classify_corner(
            median_loss,
            noise,
            dominant_cause,
            dominant_fraction,
            thresholds,
        )
        summary = {
            "corner_segment_id": segment_id,
            "classification": classification,
            "median_corner_loss_s": round(median_loss, 6),
            "robust_noise_s": round(noise, 6),
            "dominant_cause": dominant_cause,
            "dominant_cause_lap_fraction": round(dominant_fraction, 6) if dominant_cause else None,
            "reason": reason_for_classification(classification),
        }
        LOGGER.info(
            "[STATE] corner_noise | segment=%s comparison_laps=%s median_loss_s=%.6f "
            "robust_noise_s=%.6f stddev_s=%.6f",
            segment_id,
            len(comparison_records),
            median_loss,
            noise,
            stddev,
        )
        corner_summaries.append(summary)
        if classification == "reportable_candidate" and dominant_cause is not None:
            candidate = {
                "corner_segment_id": segment_id,
                "dominant_cause": dominant_cause,
                "comparison_lap_count": len(comparison_records),
                "median_corner_loss_s": round(median_loss, 6),
                "robust_noise_s": round(noise, 6),
                "cause_metric": cause_metric(
                    dominant_cause,
                    reference_metric,
                    comparison_metrics,
                    lap_dist_m_source,
                ),
                "lap_dist_m_source": lap_dist_m_source,
                "runner_up_margin_s": None,
            }
            reportable_candidates.append(candidate)

    reportable_candidates.sort(
        key=lambda candidate: candidate["median_corner_loss_s"], reverse=True
    )
    if reportable_candidates:
        if len(reportable_candidates) == 1:
            selected = reportable_candidates[0]
        else:
            margin = (
                reportable_candidates[0]["median_corner_loss_s"]
                - reportable_candidates[1]["median_corner_loss_s"]
            )
            reportable_candidates[0]["runner_up_margin_s"] = round(float(margin), 6)
            if margin < thresholds.single_dominant_margin_s:
                return non_reportable_selected_delta(
                    "no_single_dominant_issue",
                    "top_candidates_inside_single_dominant_margin",
                    comparison_records,
                    reference.lap_record,
                    corner_summaries_for_status(
                        "no_single_dominant_issue",
                        corner_summaries,
                    ),
                )
            selected = reportable_candidates[0]
        return {
            "selected_delta_schema_version": 1,
            "analysis_status": "reportable",
            "comparison_lap_count": len(comparison_records),
            "reference_lap_id": reference.lap_record["lap_id"],
            "selected_delta": selected,
            "corner_summaries": corner_summaries,
        }

    if any(summary["classification"] == "inconsistent" for summary in corner_summaries):
        status = "inconsistent"
        reason = "significant_loss_without_dominant_repeatable_cause"
    elif all(summary["classification"] == "insufficient_data" for summary in corner_summaries):
        status = "insufficient_data"
        reason = "all_corners_lacked_comparable_data"
    else:
        status = "consistent"
        reason = "no_significant_repeatable_loss"
    return non_reportable_selected_delta(
        status,
        reason,
        comparison_records,
        reference.lap_record,
        corner_summaries_for_status(status, corner_summaries),
    )


def corner_summaries_for_status(
    status: str,
    corner_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if status == "consistent":
        consistent = [
            summary for summary in corner_summaries if summary["classification"] == "consistent"
        ]
        return sorted(
            consistent,
            key=lambda summary: abs(float(summary["median_corner_loss_s"])),
            reverse=True,
        )[:5]
    if status == "inconsistent":
        return [
            summary for summary in corner_summaries if summary["classification"] == "inconsistent"
        ]
    if status == "no_single_dominant_issue":
        return sorted(
            [
                summary
                for summary in corner_summaries
                if summary["classification"] == "reportable_candidate"
            ],
            key=lambda summary: float(summary["median_corner_loss_s"]),
            reverse=True,
        )
    return corner_summaries


def non_reportable_selected_delta(
    status: str,
    reason: str,
    comparison_records: list[dict[str, Any]],
    reference_lap: dict[str, Any] | None,
    corner_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "selected_delta_schema_version": 1,
        "analysis_status": status,
        "reason": reason,
        "comparison_lap_count": len(comparison_records),
        "reference_lap_id": reference_lap["lap_id"] if reference_lap else None,
        "selected_delta": None,
        "corner_summaries": corner_summaries,
    }


def segment_time(resampled: dict[str, np.ndarray], start_edge_idx: int, end_edge_idx: int) -> float:
    elapsed = resampled["elapsed"]
    return float(elapsed[end_edge_idx] - elapsed[start_edge_idx])


def segment_metrics(
    resampled: dict[str, np.ndarray], segment: dict[str, Any]
) -> dict[str, float | None]:
    start = int(segment["start_edge_idx"])
    end = int(segment["end_edge_idx"])
    elapsed = resampled["elapsed"]
    speed = resampled["speed"][start:end]
    brake = resampled["brake"][start:end]
    throttle = resampled["throttle"][start:end]
    local_elapsed = elapsed[start:end]
    return {
        "brake_point": sustained_point(brake, local_elapsed, 0.1, 0.10, start),
        "min_speed": float(np.min(speed)) if len(speed) else None,
        "throttle_reapplication": throttle_point_after_min_speed(
            throttle,
            speed,
            local_elapsed,
            start,
        ),
        "coast_duration": coast_duration(throttle, brake, local_elapsed),
    }


def sustained_point(
    values: np.ndarray,
    elapsed: np.ndarray,
    threshold: float,
    sustain_s: float,
    start_edge_idx: int,
) -> float | None:
    if len(values) < 2:
        return None
    for index, value in enumerate(values):
        if value < threshold:
            continue
        start_time = elapsed[index]
        end_index = index
        while end_index < len(values) and values[end_index] >= threshold:
            if float(elapsed[end_index] - start_time) >= sustain_s:
                return float(GRID_EDGES[start_edge_idx + index])
            end_index += 1
    return None


def throttle_point_after_min_speed(
    throttle: np.ndarray,
    speed: np.ndarray,
    elapsed: np.ndarray,
    start_edge_idx: int,
) -> float | None:
    if len(throttle) < 2 or len(speed) < 2:
        return None
    min_speed_idx = int(np.argmin(speed))
    point = sustained_point(
        throttle[min_speed_idx:], elapsed[min_speed_idx:], 0.5, 0.20, start_edge_idx + min_speed_idx
    )
    return point


def coast_duration(throttle: np.ndarray, brake: np.ndarray, elapsed: np.ndarray) -> float:
    if len(elapsed) < 2:
        return 0.0
    duration = 0.0
    coast_mask = (throttle < 0.1) & (brake < 0.1)
    for index in range(len(elapsed) - 1):
        if coast_mask[index]:
            duration += float(elapsed[index + 1] - elapsed[index])
    return duration


def assign_cause(
    loss: float,
    reference_metric: dict[str, float | None],
    comparison_metric: dict[str, float | None],
    thresholds: AnalysisThresholds,
) -> Cause | Literal["unclassified"]:
    if loss < thresholds.lap_loss_min_s:
        return "unclassified"
    severity: dict[Cause, float] = {}
    brake_ref = reference_metric["brake_point"]
    brake_comp = comparison_metric["brake_point"]
    if brake_ref is not None and brake_comp is not None:
        bad_delta = brake_ref - brake_comp
        if bad_delta >= thresholds.brake_point_threshold_pct:
            severity["brake_point"] = bad_delta / thresholds.brake_point_threshold_pct

    min_ref = reference_metric["min_speed"]
    min_comp = comparison_metric["min_speed"]
    if min_ref is not None and min_comp is not None:
        bad_delta = min_ref - min_comp
        if bad_delta >= thresholds.min_speed_threshold_mps:
            severity["min_speed"] = bad_delta / thresholds.min_speed_threshold_mps

    throttle_ref = reference_metric["throttle_reapplication"]
    throttle_comp = comparison_metric["throttle_reapplication"]
    if throttle_ref is not None and throttle_comp is not None:
        bad_delta = throttle_comp - throttle_ref
        if bad_delta >= thresholds.throttle_reapplication_threshold_pct:
            severity["throttle_reapplication"] = (
                bad_delta / thresholds.throttle_reapplication_threshold_pct
            )

    coast_ref = reference_metric["coast_duration"]
    coast_comp = comparison_metric["coast_duration"]
    if coast_ref is not None and coast_comp is not None:
        bad_delta = coast_comp - coast_ref
        if bad_delta >= thresholds.coast_duration_threshold_s:
            severity["coast_duration"] = bad_delta / thresholds.coast_duration_threshold_s

    if not severity:
        return "unclassified"
    return max(
        CAUSE_ORDER, key=lambda cause: (severity.get(cause, -1.0), -CAUSE_ORDER.index(cause))
    )


def classify_corner(
    median_loss: float,
    noise: float,
    dominant_cause: Cause | None,
    dominant_fraction: float,
    thresholds: AnalysisThresholds,
) -> str:
    if median_loss < thresholds.min_median_corner_loss_s:
        return "consistent"
    if noise > 0.0 and abs(median_loss) <= thresholds.robust_noise_multiplier * noise:
        return "consistent"
    if dominant_cause is None or dominant_fraction < thresholds.dominant_cause_min_lap_fraction:
        return "inconsistent"
    return "reportable_candidate"


def reason_for_classification(classification: str) -> str:
    return {
        "consistent": "no_significant_repeatable_loss",
        "inconsistent": "significant_loss_without_dominant_repeatable_cause",
        "reportable_candidate": "passes_loss_noise_and_cause_gates",
        "insufficient_data": "insufficient_comparable_data",
    }[classification]


def cause_metric(
    cause: Cause,
    reference_metric: dict[str, float | None],
    comparison_metrics: list[dict[str, float | None]],
    lap_dist_m_source: str,
) -> dict[str, Any]:
    values = [metrics[cause] for metrics in comparison_metrics if metrics[cause] is not None]
    if not values or reference_metric[cause] is None:
        raise ValueError(f"cannot build cause metric for missing {cause}")
    reference_value = float(reference_metric[cause])
    comparison_median = float(np.median(np.asarray(values, dtype=float)))
    if cause == "brake_point":
        signed_delta = comparison_median - reference_value
        bad_direction_delta = reference_value - comparison_median
        unit = "lap_dist_pct"
    elif cause == "min_speed":
        signed_delta = comparison_median - reference_value
        bad_direction_delta = reference_value - comparison_median
        unit = "m/s"
    elif cause == "throttle_reapplication":
        signed_delta = comparison_median - reference_value
        bad_direction_delta = comparison_median - reference_value
        unit = "lap_dist_pct"
    else:
        signed_delta = comparison_median - reference_value
        bad_direction_delta = comparison_median - reference_value
        unit = "s"
    return {
        "metric": cause,
        "unit": unit,
        "reference_value": round(reference_value, 6),
        "comparison_median": round(comparison_median, 6),
        "signed_delta": round(signed_delta, 6),
        "bad_direction_delta": round(max(0.0, bad_direction_delta), 6),
        "lap_dist_m_source": lap_dist_m_source,
    }


def _fastest_valid_lap(laps: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [lap for lap in laps if lap.get("valid") is True]
    if not valid:
        return None
    return min(valid, key=lambda lap: (float(lap["lap_time_s"]), int(lap["lap"])))


def _select_personal_reference(
    session_dir: Path,
    session: dict[str, Any],
    ticks: pd.DataFrame,
    laps: list[dict[str, Any]],
    *,
    sessions_root: Path | None,
) -> ReferenceLap | None:
    root = sessions_root or session_dir.parent
    candidates: list[ReferenceLap] = []
    seen_ids: dict[str, Path] = {}
    target_id = session["recorded_session_id"]
    for candidate_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            candidate_session, candidate_ticks, candidate_laps = read_session_artifacts(
                candidate_dir
            )
            assert_supported_complete_session(candidate_session)
        except Exception as exc:
            LOGGER.info(
                "[SKIP] personal_reference_candidate path=%s reason=%s",
                candidate_dir,
                exc,
            )
            continue
        if candidate_session["recorded_session_id"] != target_id:
            prior = seen_ids.get(candidate_session["recorded_session_id"])
            if prior is not None:
                LOGGER.info(
                    "[SKIP] duplicate_recorded_session_id id=%s path=%s prior=%s",
                    candidate_session["recorded_session_id"],
                    candidate_dir,
                    prior,
                )
                continue
            seen_ids[candidate_session["recorded_session_id"]] = candidate_dir
        if not _matches_personal_reference_target(session, candidate_session):
            continue
        lap = _fastest_valid_lap(candidate_laps)
        if lap:
            candidates.append(ReferenceLap(lap, candidate_dir, candidate_ticks))
    if not any(
        candidate.session_dir.resolve() == session_dir.resolve() for candidate in candidates
    ):
        lap = _fastest_valid_lap(laps)
        if lap:
            candidates.append(ReferenceLap(lap, session_dir, ticks))
    if not candidates:
        return None
    return min(
        candidates,
        key=_personal_reference_sort_key,
    )


def _personal_reference_sort_key(candidate: ReferenceLap) -> tuple[float, str, str, int]:
    metadata = _reference_session_sort_metadata(candidate.session_dir)
    return (
        float(candidate.lap_record["lap_time_s"]),
        metadata["started_at"],
        metadata["recorded_session_id"],
        int(candidate.lap_record["lap"]),
    )


def _reference_session_sort_metadata(session_dir: Path) -> dict[str, str]:
    session = yaml.safe_load((session_dir / "session.yaml").read_text(encoding="utf-8"))
    return {
        "started_at": str(session.get("started_at", "")),
        "recorded_session_id": str(session.get("recorded_session_id", "")),
    }


def _matches_personal_reference_target(
    target_session: dict[str, Any],
    candidate_session: dict[str, Any],
) -> bool:
    return all(
        target_session[key] == candidate_session.get(key)
        for key in (
            "sim",
            "track",
            "car",
        )
    )
