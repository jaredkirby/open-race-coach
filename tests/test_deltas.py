from __future__ import annotations

from pathlib import Path

import pandas as pd

import simcoach.analysis.deltas as deltas_module
from simcoach.analysis.confidence import AnalysisThresholds, robust_noise, sample_stddev
from simcoach.analysis.deltas import (
    ReferenceLap,
    analyze_deltas,
    assign_cause,
    cause_metric,
    corner_summaries_for_status,
    resample_lap,
)
from simcoach.analysis.segmentation import derive_corner_segments
from simcoach.ingest.session import derive_lap_records
from tests.test_analysis import build_synthetic_reportable_ticks


def test_robust_noise_uses_mad_scaled_sigma() -> None:
    assert robust_noise([1.0, 1.0, 1.0, 10.0]) == 0.0


def test_sample_stddev_uses_sample_dispersion_for_debug_logging() -> None:
    assert sample_stddev([1.0]) == 0.0
    assert round(sample_stddev([1.0, 2.0, 3.0]), 6) == 1.0


def test_assign_cause_uses_bad_direction_and_threshold_ratio() -> None:
    thresholds = AnalysisThresholds()
    reference = {
        "brake_point": 0.50,
        "min_speed": 50.0,
        "throttle_reapplication": 0.60,
        "coast_duration": 0.2,
    }
    comparison = {
        "brake_point": 0.49,
        "min_speed": 46.0,
        "throttle_reapplication": 0.61,
        "coast_duration": 0.25,
    }

    assert assign_cause(0.2, reference, comparison, thresholds) == "min_speed"


def test_cause_metric_has_fixed_sign_for_lower_min_speed() -> None:
    metric = cause_metric(
        "min_speed",
        {
            "brake_point": 0.50,
            "min_speed": 50.0,
            "throttle_reapplication": 0.60,
            "coast_duration": 0.2,
        },
        [
            {
                "brake_point": 0.50,
                "min_speed": 48.0,
                "throttle_reapplication": 0.60,
                "coast_duration": 0.2,
            },
            {
                "brake_point": 0.50,
                "min_speed": 46.0,
                "throttle_reapplication": 0.60,
                "coast_duration": 0.2,
            },
        ],
        "unavailable",
    )

    assert metric["signed_delta"] == -3.0
    assert metric["bad_direction_delta"] == 3.0
    assert metric["unit"] == "m/s"


def test_consistent_status_keeps_five_strongest_consistent_summaries() -> None:
    summaries = [
        summary(f"C{index}", "consistent", loss)
        for index, loss in enumerate([0.01, -0.05, 0.02, 0.07, -0.03, 0.04], start=1)
    ]
    summaries.append(summary("C7", "inconsistent", 0.50))

    result = corner_summaries_for_status("consistent", summaries)

    assert [item["corner_segment_id"] for item in result] == ["C4", "C2", "C6", "C5", "C3"]
    assert all(item["classification"] == "consistent" for item in result)


def test_inconsistent_status_keeps_only_inconsistent_summaries() -> None:
    summaries = [
        summary("C1", "consistent", 0.01),
        summary("C2", "inconsistent", 0.08),
        summary("C3", "reportable_candidate", 0.20),
        summary("C4", "inconsistent", 0.06),
    ]

    result = corner_summaries_for_status("inconsistent", summaries)

    assert [item["corner_segment_id"] for item in result] == ["C2", "C4"]


def test_no_single_dominant_status_keeps_reportable_candidates_by_loss() -> None:
    summaries = [
        summary("C1", "reportable_candidate", 0.08),
        summary("C2", "consistent", 0.01),
        summary("C3", "reportable_candidate", 0.11),
        summary("C4", "inconsistent", 0.09),
    ]

    result = corner_summaries_for_status("no_single_dominant_issue", summaries)

    assert [item["corner_segment_id"] for item in result] == ["C3", "C1"]


def test_analysis_resamples_each_lap_once(monkeypatch) -> None:
    ticks = pd.DataFrame(
        [tick.as_record() for tick in build_synthetic_reportable_ticks()]
    )
    laps = derive_lap_records(ticks, "01test")
    reference = ReferenceLap(laps[0], Path("session"), ticks)
    comparison_records = laps[1:]
    reference_resampled = resample_lap(ticks, laps[0])
    segments = derive_corner_segments(
        reference_resampled,
        lap_dist_m_source="sim",
    )["segments"]
    call_count = 0

    def counting_resample(frame, lap):
        nonlocal call_count
        call_count += 1
        return resample_lap(frame, lap)

    monkeypatch.setattr(deltas_module, "resample_lap", counting_resample)

    analyze_deltas(
        reference,
        comparison_records,
        ticks,
        segments,
        reference_resampled=reference_resampled,
        lap_dist_m_source="sim",
    )

    assert call_count == len(comparison_records)


def summary(corner_id: str, classification: str, loss: float) -> dict[str, object]:
    return {
        "corner_segment_id": corner_id,
        "classification": classification,
        "median_corner_loss_s": loss,
        "robust_noise_s": 0.01,
        "dominant_cause": None,
        "dominant_cause_lap_fraction": None,
        "reason": "test",
    }
