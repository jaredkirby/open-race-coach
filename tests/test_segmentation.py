from __future__ import annotations

import math

import numpy as np

from simcoach.analysis.segmentation import GRID_EDGES, SegmentThresholds, derive_corner_segments


def test_derive_corner_segments_from_synthetic_curve() -> None:
    x = np.asarray([100.0 * math.cos(2.0 * math.pi * pct) for pct in GRID_EDGES])
    y = np.asarray([100.0 * math.sin(2.0 * math.pi * pct) for pct in GRID_EDGES])
    reference = {
        "pos_x": x,
        "pos_y": y,
        "lap_dist_m": GRID_EDGES * 4309.0,
    }

    result = derive_corner_segments(
        reference,
        lap_dist_m_source="sim",
        thresholds=SegmentThresholds(
            smoothing_edges=5,
            curvature_percentile=60.0,
            min_segment_edges=4,
            merge_gap_edges=3,
        ),
    )

    assert result["corner_segments_schema_version"] == 1
    assert result["empty_reason"] is None
    assert result["segments"]
    first = result["segments"][0]
    assert first["corner_segment_id"] == "C1"
    assert first["start_edge_idx"] < first["end_edge_idx"]
    assert first["start_lap_dist_m"] is not None
    assert first["geometry"]["max_abs_curvature"] > 0.0
