"""Corner segmentation from normalized position traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

GRID_BINS = 1000
GRID_EDGES = np.linspace(0.0, 1.0, GRID_BINS + 1)


@dataclass(frozen=True, slots=True)
class SegmentThresholds:
    smoothing_edges: int = 9
    curvature_percentile: float = 72.0
    min_segment_edges: int = 8
    merge_gap_edges: int = 6

    def as_dict(self) -> dict[str, float | int]:
        return {
            "smoothing_edges": self.smoothing_edges,
            "curvature_percentile": self.curvature_percentile,
            "min_segment_edges": self.min_segment_edges,
            "merge_gap_edges": self.merge_gap_edges,
        }


def derive_corner_segments(
    resampled_reference: dict[str, np.ndarray],
    *,
    lap_dist_m_source: str,
    thresholds: SegmentThresholds | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or SegmentThresholds()
    x = resampled_reference["pos_x"]
    y = resampled_reference["pos_y"]
    lap_dist_m = resampled_reference.get("lap_dist_m")
    if len(x) != GRID_BINS + 1 or len(y) != GRID_BINS + 1:
        raise ValueError("resampled reference must use the fixed 1000-bin grid")

    dx = np.diff(x)
    dy = np.diff(y)
    distance = np.sqrt(dx**2 + dy**2)
    moving = distance > 1e-6
    if int(moving.sum()) < 20:
        return _empty_segments("reference_position_unusable")

    heading = np.unwrap(np.arctan2(dy, dx))
    heading_change = np.abs(np.diff(heading, prepend=heading[0]))
    curvature = np.divide(
        heading_change,
        np.maximum(distance, 1e-6),
        out=np.zeros_like(heading_change),
        where=moving,
    )
    smoothed = _moving_average(curvature, thresholds.smoothing_edges)
    non_zero = smoothed[smoothed > 0.0]
    if len(non_zero) == 0:
        return _empty_segments("reference_curvature_unusable")

    cutoff = float(np.percentile(non_zero, thresholds.curvature_percentile))
    if cutoff <= 0.0:
        return _empty_segments("reference_curvature_unusable")

    raw_ranges = _boolean_ranges(smoothed >= cutoff)
    merged = _merge_ranges(raw_ranges, thresholds.merge_gap_edges)
    filtered = [
        (start, end)
        for start, end in merged
        if end - start >= thresholds.min_segment_edges and start < GRID_BINS
    ]
    if not filtered:
        return _empty_segments("no_segments_after_thresholding")

    segments = []
    for index, (start, end) in enumerate(filtered, start=1):
        end = min(end, GRID_BINS)
        segment_distance = float(distance[start:end].sum())
        segment_curvature = smoothed[start:end]
        start_m = None
        end_m = None
        if lap_dist_m_source != "unavailable" and lap_dist_m is not None:
            start_m = _finite_or_none(lap_dist_m[start])
            end_m = _finite_or_none(lap_dist_m[end])
        segments.append(
            {
                "corner_segment_id": f"C{index}",
                "start_edge_idx": int(start),
                "end_edge_idx": int(end),
                "start_lap_dist_pct": float(GRID_EDGES[start]),
                "end_lap_dist_pct": float(GRID_EDGES[end]),
                "start_lap_dist_m": start_m,
                "end_lap_dist_m": end_m,
                "geometry": {
                    "arc_length_m": segment_distance,
                    "mean_abs_curvature": float(np.mean(segment_curvature)),
                    "max_abs_curvature": float(np.max(segment_curvature)),
                },
            }
        )
    return {
        "corner_segments_schema_version": 1,
        "segments": segments,
        "empty_reason": None,
    }


def _empty_segments(reason: str) -> dict[str, Any]:
    return {
        "corner_segments_schema_version": 1,
        "segments": [],
        "empty_reason": reason,
    }


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window == 1:
        return values
    kernel = np.ones(window) / window
    return np.convolve(values, kernel, mode="same")


def _boolean_ranges(mask: np.ndarray) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(mask):
        if active and start is None:
            start = index
        elif not active and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(mask)))
    return ranges


def _merge_ranges(ranges: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start - last_end <= max_gap:
            merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def _finite_or_none(value: float) -> float | None:
    if not np.isfinite(value):
        return None
    return float(value)
