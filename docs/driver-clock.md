# AMS2 driver clock

With AMS2 running and Project CARS 2 shared memory enabled, start the clock
when you sit down for a test. Start it before menus, setup, or validation
prompts if you want that time included.

Keep [continuous telemetry](continuous-telemetry.md) running across driver
breaks and recording boundaries. Stop the seat-time clock when leaving the
rig, and start a fresh clock when returning.

```powershell
uv run python -m simcoach.cli.driver_clock
```

Copy the run directory printed by the command. Open its `clock.json` for a
live total and stop the clock when you leave the rig. Press Ctrl+C in its
terminal or use a second terminal.

```powershell
uv run python -m simcoach.cli.driver_clock --stop "<printed run directory>"
```

Elapsed wall time is measured with a monotonic clock. The driver defines
the observation window by starting and stopping the command. The simulator
cannot detect whether a person is physically in the seat. Every elapsed
interval goes into one of these buckets:

| Bucket | Evidence |
| --- | --- |
| `driving` | Active on-track Vehicle State, speed above 1 m/s |
| `pit_moving` | Active pit Vehicle State, speed above 1 m/s. This can include movement under simulator control. |
| `not_driving` | Stationary or moving at most 1 m/s in an active state, or menus, pause, or replay |
| `transition` | Adjacent usable samples report different activity states |
| `unknown` | Missing, stale, or unsupported telemetry, or a sampling gap over one second |

The observer polls at about 5 Hz and treats two seconds without a changing
shared-memory sequence as stale. Use the live total as an estimate and
report unknown time separately. `controls_min_max` shows raw AMS2 ranges
while moving on track. This helps spot stuck channels. Checking exact
calibration requires controlled input samples.

Each run has its own ignored directory under `.scratch/live-validation/`.
The clock updates `clock.json` atomically about once a second and appends
elapsed increments to `timeline.jsonl`. After a forced stop, preserve the
last checkpoint and treat a remaining `running` status as incomplete.

Keep copies of the outputs you evaluate, including failed, misleading, or
insufficient results. For example, attach a validator log or the
`coach_report.md` from a specific Analysis Run:

```powershell
uv run python -m simcoach.cli.driver_clock --review "<printed run directory>" `
  --output "<UTF-8 output file>" --verdict fail --note "What went wrong"
```

Each review creates a new JSON file with the text, source path, SHA-256 of
the source bytes, review time, verdict, and note. The note can document
what to change or recheck on the next attempt. Acceptable verdicts are
`pass`, `fail`, `incomplete`, and `unreviewed`. The clock and reviews
stay local. Treat copied logs and reports as private.

`not_driving` currently combines intentional rest, menus and possible
avoidable waiting. Do not label that whole bucket product friction.
Separating those reasons, time to readiness, time to a useful debrief,
and repeated physical validation requests is planned in the
[race-chief direction](race-chief-direction.md). The clock is an estimate
over explicit visit boundaries, not an occupancy sensor or a measure of
time before it started.

To reduce future driver interruptions, arm observers before handing over
the controls, use a single normal stint for lap crossings and position
checks, and reserve deliberate invalidation and stationary pedal holds
for Live Simulator Validation. Compare the driving and waiting totals
with the preserved result across attempts.
