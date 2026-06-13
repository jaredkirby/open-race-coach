# corner_segments.json schema

Schema version: 1
Owner: deterministic analyzer
Write timing: every complete deterministic Analysis Run, including insufficient-data runs.
Compatibility: readers must refuse unsupported `corner_segments_schema_version`.

Required top-level fields: `corner_segments_schema_version`, `recorded_session_id`, `reference_lap_id`, `segments`, `empty_reason`.

Nullable top-level fields: `reference_lap_id` and `empty_reason`.

Top-level field details:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `corner_segments_schema_version` | yes | no | int | this schema version |
| `recorded_session_id` | yes | no | string | Recorded Session identity |
| `reference_lap_id` | yes | yes | string | Reference Lap identity, null when no valid reference exists |
| `segments` | yes | no | array | ordered Corner Segment objects |
| `empty_reason` | yes | yes | string | null when `segments` is non-empty |

Enums:

- `empty_reason`: null, `no_valid_reference_lap`, `reference_position_unusable`, `reference_curvature_unusable`, `no_segments_after_thresholding`

`segments` is an ordered array. Segment fields:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `corner_segment_id` | yes | no | string | `C1`, `C2`, ... |
| `start_edge_idx` | yes | no | int | fixed 1000-bin Lap Progress edge index |
| `end_edge_idx` | yes | no | int | fixed 1000-bin Lap Progress edge index, half-open end |
| `start_lap_dist_pct` | yes | no | float | Lap Progress fraction, 0.0..1.0 |
| `end_lap_dist_pct` | yes | no | float | Lap Progress fraction, 0.0..1.0 |
| `start_lap_dist_m` | yes | yes | float | meters from lap start |
| `end_lap_dist_m` | yes | yes | float | meters from lap start |
| `geometry` | yes | no | object | aggregate geometry metrics |

`geometry` fields are `arc_length_m` in meters, `mean_abs_curvature` in radians per meter, and `max_abs_curvature` in radians per meter.

Corner Segment windows are half-open, non-wrapping, aligned to the fixed 1000-bin Lap Progress grid, and numbered by increasing `start_lap_dist_pct` as `C1`, `C2`, ...

`empty_reason` is null when `segments` is non-empty. When no valid reference or usable segmentation exists, `segments` is empty and `empty_reason` is one of `no_valid_reference_lap`, `reference_position_unusable`, `reference_curvature_unusable`, or `no_segments_after_thresholding`.
