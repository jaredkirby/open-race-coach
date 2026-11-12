# laps.jsonl schema

Schema version: 1
Owner: recorder finalization
Write timing: derived from finalized ticks after `ticks.parquet` is written.
Compatibility: analyzers must refuse target sessions with unsupported `lap_schema_version`.

Each line is one JSON object.

| Field | Required | Nullable | Type | Unit / enum |
|---|---:|---:|---|---|
| `lap_id` | yes | no | string | `{recorded_session_id}:lap:{lap}` |
| `lap` | yes | no | int | 1-indexed lap |
| `lap_time_s` | yes | no | float | seconds |
| `lap_time_source` | yes | no | string | `sim`, `derived_from_ticks` |
| `sector_times_s` | yes | yes | array[float] | seconds; never fabricated from thirds |
| `valid` | yes | no | bool | analysis eligibility |
| `invalid_reason` | yes | yes | string | null or controlled enum |
| `tick_range` | yes | no | array[int, int] | zero-based half-open row range in `ticks.parquet` |

Invalid reasons: `sim_invalidated`, `bad_lap_progress`, `missing_tick_range`, `non_running_vehicle_state`, `teleport_or_reset`, `metadata_boundary`, `unknown_invalidity`.

For recorder-derived eligibility, capture must observe the Lap at or before `0.01` Lap Progress and must then observe either the next-lap transition or `1.0` Lap Progress. A first or final fragment without that evidence uses `invalid_reason=metadata_boundary`.

Invalid laps remain stored for audit but are never Reference Laps, Comparison Laps, noise inputs, consistency inputs, or personal-best candidates.
