# selected_delta.json schema

Schema version: 1
Owner: deterministic analyzer
Write timing: every complete deterministic Analysis Run after confidence classification.
Compatibility: readers must refuse unsupported `selected_delta_schema_version`.

Required top-level fields: `selected_delta_schema_version`, `analysis_status`, `comparison_lap_count`, `reference_lap_id`, `selected_delta`, `corner_summaries`.

Nullable top-level fields: `reference_lap_id`; `selected_delta` when `analysis_status` is non-reportable.

When `analysis_status=reportable`, required fields also include `selected_delta` with `corner_segment_id`, `dominant_cause`, `comparison_lap_count`, `median_corner_loss_s`, `robust_noise_s`, `cause_metric`, `lap_dist_m_source`, and `runner_up_margin_s`.

When non-reportable, required fields also include `reason`; `selected_delta` is null.

Enums:

- `analysis_status`: `reportable`, `consistent`, `inconsistent`, `insufficient_data`, `no_single_dominant_issue`
- `dominant_cause`: `brake_point`, `min_speed`, `throttle_reapplication`, `coast_duration`
- `corner_summaries[].classification`: `reportable_candidate`, `consistent`, `inconsistent`, `insufficient_data`
- `lap_dist_m_source`: `sim`, `derived_from_track_length`, `unavailable`
- `cause_metric.metric`: `brake_point`, `min_speed`, `throttle_reapplication`, `coast_duration`
- `cause_metric.unit`: `lap_dist_pct`, `m/s`, `s`

Top-level field details:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `selected_delta_schema_version` | yes | no | int | this schema version |
| `analysis_status` | yes | no | string | enum |
| `comparison_lap_count` | yes | no | int | number of valid Comparison Laps |
| `reference_lap_id` | yes | yes | string | Reference Lap identity |
| `selected_delta` | yes | conditional | object | selected Reportable Delta or null |
| `reason` | conditional | no | string | controlled non-reportable reason |
| `corner_summaries` | yes | no | array | aggregate corner-level status evidence |

`selected_delta` field details:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `corner_segment_id` | yes | no | string | selected Corner Segment ID |
| `dominant_cause` | yes | no | string | enum |
| `comparison_lap_count` | yes | no | int | number of valid Comparison Laps |
| `median_corner_loss_s` | yes | no | float | seconds |
| `robust_noise_s` | yes | no | float | seconds |
| `cause_metric` | yes | no | object | aggregate selected-cause effect |
| `lap_dist_m_source` | yes | no | string | enum |
| `runner_up_margin_s` | yes | yes | float | seconds; null when no second reportable candidate exists |

`cause_metric` stores `metric`, `unit`, `reference_value`, `comparison_median`, `signed_delta`, and positive `bad_direction_delta`. `brake_point` and `throttle_reapplication` use `lap_dist_pct`; `min_speed` uses `m/s`; `coast_duration` uses seconds. `signed_delta` is Comparison median minus Reference value. `bad_direction_delta` is always positive when Comparison Laps are worse in the selected cause's direction.

`corner_summaries` are aggregate-only and must not include per-lap rows. Each summary contains `corner_segment_id`, `classification`, `median_corner_loss_s`, `robust_noise_s`, nullable `dominant_cause`, nullable `dominant_cause_lap_fraction`, and `reason`. `median_corner_loss_s` and `robust_noise_s` are seconds; `dominant_cause_lap_fraction` is a 0.0..1.0 fraction.

For `analysis_status=no_single_dominant_issue`, `corner_summaries` contains reportable candidates sorted by descending median loss. For `consistent`, it contains up to five strongest consistent corners by absolute median loss. For `inconsistent`, it contains only inconsistent corners. For `insufficient_data`, it contains no entries when no usable Corner Segments exist; otherwise it contains aggregate corner-level insufficiency reasons.
