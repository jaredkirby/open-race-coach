# analysis.yaml schema

Schema version: 1
Owner: deterministic analyzer plus coach refinement status updates
Write timing: created in a new Analysis Run directory. Failed deterministic runs write only `analysis.yaml`; complete runs write all deterministic artifacts.
Compatibility: readers must refuse unsupported `analysis_schema_version`; Personal Best search skips unsupported historical candidates.

Required fields: `analysis_schema_version`, `corner_segments_schema_version`, `selected_delta_schema_version`, `coach_response_schema_version`, `recorded_session_path`, `recorded_session_id`, `reference_mode`, `reference_lap_id`, `reference_lap_source_path`, `analyzer_version`, `effective_thresholds`, `run_status`, `run_error`, `coach_refinement_mode`, `coach_refinement_status`, `coach_refinement_error`, `analysis_status`, `created_at`, `updated_at`, `analysis_run_path`.

Nullable fields: `corner_segments_schema_version` and `selected_delta_schema_version` are null only when `run_status=failed`; `coach_response_schema_version` is null until `coach_response.json` exists and is also null for failed deterministic runs; `reference_lap_id` and `reference_lap_source_path` are null when no Reference Lap exists or the run failed; `run_error`, `coach_refinement_mode`, and `coach_refinement_error` are nullable; `analysis_status` is null only when `run_status=failed`.

Enums:

- `reference_mode`: `best`, `personal`
- `run_status`: `complete`, `failed`
- `run_error`: null or `unsupported_schema`, `incomplete_recorded_session`, `missing_required_artifact`, `artifact_read_failed`, `artifact_write_failed`, `invalid_artifact_contract`, `analysis_exception`
- `coach_refinement_mode`: null, `api`, `chatgpt`
- `coach_refinement_status`: `not_requested`, `complete`, `awaiting_response`, `invalid_response`, `failed`
- `analysis_status`: `reportable`, `consistent`, `inconsistent`, `insufficient_data`, `no_single_dominant_issue`

Field details and units:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `analysis_schema_version` | yes | no | int | this schema version |
| `corner_segments_schema_version` | yes | conditional | int | `corner_segments.json` schema version |
| `selected_delta_schema_version` | yes | conditional | int | `selected_delta.json` schema version |
| `coach_response_schema_version` | yes | yes | int | `coach_response.json` schema version |
| `recorded_session_path` | yes | no | string | path captured at Analysis Run creation; audit metadata, not identity |
| `recorded_session_id` | yes | no | string | Recorded Session identity |
| `reference_mode` | yes | no | string | enum |
| `reference_lap_id` | yes | yes | string | selected Reference Lap identity |
| `reference_lap_source_path` | yes | yes | string | path to the Recorded Session that supplied the Reference Lap |
| `analyzer_version` | yes | no | string | analyzer implementation version |
| `effective_thresholds` | yes | no | object | actual threshold values used for this run |
| `run_status` | yes | no | string | enum |
| `run_error` | yes | yes | string | controlled enum when failed |
| `coach_refinement_mode` | yes | yes | string | null until a refinement mode owns the run |
| `coach_refinement_status` | yes | no | string | enum |
| `coach_refinement_error` | yes | yes | string | short validation/provider error |
| `analysis_status` | yes | conditional | string | coaching outcome enum |
| `created_at` | yes | no | string | timezone-aware ISO 8601 timestamp |
| `updated_at` | yes | no | string | timezone-aware ISO 8601 timestamp |
| `analysis_run_path` | yes | no | string | path to this Analysis Run directory |

`effective_thresholds` must record the actual threshold values used for reproducibility: `min_comparison_laps`, `min_median_corner_loss_s`, `robust_noise_multiplier`, `dominant_cause_min_lap_fraction`, `single_dominant_margin_s`, Lap Loss Cause thresholds, and `segmentation` thresholds. Threshold units are seconds for `*_s` fields, fractions for lap fractions and input thresholds, m/s for speed thresholds, edge counts for segmentation edge counts, and percentile points for `curvature_percentile`.

When `run_status=failed`, the run is diagnostic-only: no deterministic artifacts other than `analysis.yaml` are canonical, artifact schema-version fields are null, `analysis_status` is null, and `coach_refinement_status` remains `not_requested`. When `run_status=complete`, `corner_segments_schema_version` and `selected_delta_schema_version` are non-null, `corner_segments.json`, `selected_delta.json`, and `coach_report.md` exist, and `analysis_status` is non-null even when the outcome is `insufficient_data`.
