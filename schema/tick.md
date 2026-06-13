# ticks.parquet schema

Schema version: 1
Owner: recorder finalization
Write timing: written atomically during Recorded Session finalization before `session.yaml.complete` becomes true.
Compatibility: analyzers must refuse target sessions with unsupported `tick_schema_version`.

One row is a `NormalizedTick`.

| Field | Required | Nullable | Type | Unit / enum |
|---|---:|---:|---|---|
| `t` | yes | no | float64 | seconds Capture Time from recorder start |
| `lap` | yes | no | int32 | 1-indexed current lap |
| `lap_dist_pct` | yes | no | float32 | 0.0..1.0 Lap Progress |
| `lap_dist_m` | yes | yes | float32 | meters |
| `speed` | yes | no | float32 | m/s |
| `throttle` | yes | no | float32 | 0.0..1.0 |
| `brake` | yes | no | float32 | 0.0..1.0 |
| `steering` | yes | no | float32 | -1.0..1.0, negative left |
| `gear` | yes | no | int8 | -1 reverse, 0 neutral, 1+ forward |
| `rpm` | yes | no | float32 | rev/min |
| `vehicle_state` | yes | no | string | `running`, `pit`, `paused`, `menu`, `replay`, `unknown` |
| `pos_x` | yes | no | float32 | meters, normalized ground-plane X |
| `pos_y` | yes | no | float32 | meters, normalized ground-plane Y |
| `pos_z` | yes | no | float32 | meters, vertical/elevation |

Invariants: `t` is strictly monotonic; `lap_dist_pct` is bounded 0.0..1.0; v0 ingest downsamples to 60 Hz; analysis must resample valid laps onto the fixed 1000-bin Lap Progress grid before comparisons.
