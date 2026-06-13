"""Confidence gates for deterministic analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AnalysisThresholds:
    min_comparison_laps: int = 4
    min_median_corner_loss_s: float = 0.05
    robust_noise_multiplier: float = 1.5
    dominant_cause_min_lap_fraction: float = 0.60
    single_dominant_margin_s: float = 0.03
    lap_loss_min_s: float = 0.05
    brake_point_threshold_pct: float = 0.005
    min_speed_threshold_mps: float = 1.0
    throttle_reapplication_threshold_pct: float = 0.005
    coast_duration_threshold_s: float = 0.10

    def as_dict(self) -> dict[str, float | int]:
        return {
            "min_comparison_laps": self.min_comparison_laps,
            "min_median_corner_loss_s": self.min_median_corner_loss_s,
            "robust_noise_multiplier": self.robust_noise_multiplier,
            "dominant_cause_min_lap_fraction": self.dominant_cause_min_lap_fraction,
            "single_dominant_margin_s": self.single_dominant_margin_s,
            "lap_loss_min_s": self.lap_loss_min_s,
            "brake_point_threshold_pct": self.brake_point_threshold_pct,
            "min_speed_threshold_mps": self.min_speed_threshold_mps,
            "throttle_reapplication_threshold_pct": self.throttle_reapplication_threshold_pct,
            "coast_duration_threshold_s": self.coast_duration_threshold_s,
        }


def robust_noise(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    array = np.asarray(values, dtype=float)
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return 1.4826 * mad


def sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(np.std(np.asarray(values, dtype=float), ddof=1))
