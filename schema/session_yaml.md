# session.yaml schema

Schema version: 1
Owner: recorder
Write timing: created as incomplete at Recorded Session start, then atomically replaced at finalization.
Compatibility: analysis refuses `complete: false` or unsupported session-owned artifact versions.

Required fields: `session_schema_version`, `tick_schema_version`, `lap_schema_version`, `recorded_session_id`, `sim`, `track_raw`, `track`, `car_raw`, `car`, `session_type`, `started_at`, `ended_at`, `tick_rate_hz`, `track_length_m`, `lap_dist_m_source`, `adapter_version`, `validity_method`, `complete`, `failure_reason`, `notes`.

Nullable fields: `ended_at` while recording, `track_length_m`, `failure_reason`.

Enums:

- `sim`: `ams2`, `acc`, `ac`
- `session_type`: `practice`, `qualifying`, `race`, `hotlap`
- `lap_dist_m_source`: `sim`, `derived_from_track_length`, `unavailable`
- `validity_method`: `sim_flag_plus_inferred`, `inferred`, `unknown_plus_inferred`
- `failure_reason`: null when complete, else `metadata_unavailable`, `artifact_write_failed`, `recording_interrupted`, `no_ticks_collected`, `directory_collision`, `session_boundary_finalization_failed`, `unknown_failure`

Field details and units:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `session_schema_version` | yes | no | int | this schema version |
| `tick_schema_version` | yes | no | int | `ticks.parquet` schema version |
| `lap_schema_version` | yes | no | int | `laps.jsonl` schema version |
| `recorded_session_id` | yes | no | string | lowercase ULID |
| `sim` | yes | no | string | enum |
| `track_raw` | yes | no | string | simulator display label |
| `track` | yes | no | string | normalized Matching Key |
| `car_raw` | yes | no | string | simulator display label |
| `car` | yes | no | string | normalized Matching Key |
| `session_type` | yes | no | string | enum |
| `started_at` | yes | no | string | timezone-aware ISO 8601 timestamp |
| `ended_at` | yes | yes | string | timezone-aware ISO 8601 timestamp |
| `tick_rate_hz` | yes | no | int | hertz |
| `track_length_m` | yes | yes | float | meters |
| `lap_dist_m_source` | yes | no | string | enum |
| `adapter_version` | yes | no | string | adapter implementation version |
| `validity_method` | yes | no | string | enum |
| `complete` | yes | no | bool | artifact completeness |
| `failure_reason` | yes | yes | string | enum |
| `notes` | yes | no | string | freeform operator notes |

All durable timestamps are timezone-aware ISO 8601 strings with offsets. `track` and `car` are deterministic Matching Keys derived from Simulator Labels by lowercasing, trimming, replacing non-alphanumeric runs with `_`, and stripping leading/trailing `_`.
