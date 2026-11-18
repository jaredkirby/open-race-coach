# Local fuel and telemetry supervision prototype

The local rule engine reads one explicitly selected continuous AMS2 archive,
requires fresh advancing evidence, and journals fuel decisions without a model
or network connection. It does not operate the simulator or alter capture.
This is an offline-tested prototype, not a deployed Race Chief or an audible
fuel warning. See [the artifact contract](../schema/race_watch.md).

## Fuel plan and approval

The chief can prepare a [reviewable estimate from saved fuel evidence](fuel-budget-evidence.md).
This bounded offline path retains slow/invalid laps and unsupported fragments;
it does not replace the live source and cockpit approval gates below.

The chief prepares a private JSON plan. This is a synthetic example, not a
recommendation for an arbitrary car:

```json
{
  "schema_version": 1,
  "context": {
    "car": "Example car", "track": "Example track",
    "variation": "Short", "session_state": 1
  },
  "litres_per_lap": 0.5,
  "timed_laps": 6,
  "reserve_laps": 2,
  "return_threshold_litres": 2.0,
  "estimate_source": "Replace with reviewed estimate and uncertainty margin"
}
```

Car/track labels match exactly. The conservative rate includes an explicit
reviewed uncertainty margin; the engine does not estimate consumption from an
arbitrary pair of readings. The load budget includes outlap, timed laps, inlap
and reserve by default. Optional `out_laps`/`in_laps` explicitly change those
allowances without reducing the required return reserve. For example, a synthetic
0.5 L/lap plan for five laps, zero outlaps, one inlap and two reserve laps requires
4 L. This is arithmetic, not a car-specific recommendation or consumption model.

Arming requires two fresh advancing observations, the intended context,
sufficient actual fuel and a fresh cockpit comparison within 0.2 litres.
Solo approval supports practice/test only. Race and Time Trial require a checked
viewed slot plus explicit chief confirmation that it represents the player's
controlled car. New collector summaries retain slot index/count, active status
and matching car/race/pit fields without names. Missing or inconsistent fields
cannot establish this binding. Spectating and unobserved slot reuse remain limits.

A stationary race grid/pre-start can be approved before fuel starts burning:
session 5, race state 1, game state 2/4, pit mode 0, speed at most 1 m/s and checked
slot evidence. Approval carries into racing; radio fuel notices stay inhibited
before the start. An observed active→not-started transition revokes approval.
Constant Time Trial fuel does not establish a burn rate or unlimited-fuel setting.

Stale/future telemetry, counter resets, missing coverage, refuelling, changed
context/capacity, inactive/terminal race state, or an observation gap cancel
arming. Clock checks compare UTC against monotonic elapsed/capture time using
fixed baselines as well as adjacent intervals; small discrepancies cannot build
up unnoticed. A source-clock discontinuity also cancels pending fuel/recovery
speech at its final output check. Recovery from a clock fault requires new
advancing readings and cockpit approval. This is tested with synthetic clock
faults; actual Windows sleep/resume and logon remain separate acceptance checks.

A brief `unstable_copy` interval has a narrow recovery path: an already
approved episode becomes `suspended`, with no actionable fuel estimate or call.
Before the earlier of three seconds from detection or five seconds from the last
admitted source timestamp, two fresh advancing readings must pass the existing
source, context, clock, capacity, lap and refuelling checks. Repeated polls cannot
renew this deadline. Any other fault requires new approval. This applies only
before a return notice has been issued; failed/expired calls are not replayed.
The [contract](../schema/race_watch.md#bounded-copy-contention-recovery) defines
the exact boundaries and known unobserved-reset/participant limitations.

Approval is bound to the selected archive and must postdate the latest
invalidation. Restart always requires new approval. A return notice occurs once
per armed episode; it is suppressed in pit mode, expires quickly and must be
checked again before any later radio delivery.

## Shadow observation

The chief can inspect current behavior without audio or simulator changes:

```powershell
uv run python -m simcoach.cli.race_watch --archive "<exact-archive>" `
  --plan "<private-plan.json>" --duration 60
```

Without approval this remains unarmed. A chief who has actually reconciled
loaded fuel and solo player context may also pass all of `--cockpit-litres`,
`--verified-at` (UTC ISO timestamp, at most 30 seconds old),
`--confirm-solo-player`, and `--approval-note`. These options record an assertion;
they do not perform the cockpit check. Rejected approval appears in status and
the journal. The driver should not manage this command.

For explicit viewed-slot approval, replace `--confirm-solo-player` with
`--confirm-player-view --participant-index <confirmed-slot>` in either the shadow
watcher or `visit fuel`. Do not infer the slot from a participant name or assume
zero. Replay-only approval may include `player_view_confirmed: true` and
`participant_index`; it remains a synthetic stimulus. The service forwards these
fields with the original intent hash. Existing running collectors must publish
the new summary before this path is available; updating source files does not
upgrade an already running process. No automatic collector restart is performed.

Each invocation uses a new private directory under `.scratch/race-chief/` with
a transactional observation/decision journal and status. A shared output root
locks cooperating watchers for the same archive; different output roots are not
a global ownership boundary. A stop file in the printed run directory requests
normal closure. Ctrl+C is recorded as interrupted. Exit 0 describes successful
observation/closure, **not** healthy telemetry, fuel approval or ready-to-drive
status. Inspect `supervision_health`, `fuel_state`, `reason` and `approval`.
Also inspect `recovery` and `supervision_loss`: a prior accepted approval does
not mean supervision is currently active. Loss and recovery are journaled and
visible in host/radio status. This shadow CLI never speaks. The optional
[fuel-radio loss package](fuel-radio.md#monitoring-loss-warning) can dispatch
a warning through the visit's existing owner when recovery fails. A matching
optional restored phrase can follow verified reapproval and fresh source checks.
Actual audible delivery and driver understanding remain separate acceptance gates.

## Offline replay

```powershell
uv run python -m simcoach.cli.race_watch --replay "<private-replay.jsonl>" `
  --plan "<private-test-plan.json>"
```

Replay uses original historical timestamps as its virtual clock and never plays
audio. A replay-only approval is labelled as a test stimulus. A reconstructed
heartbeat envelope is not an actual recorded heartbeat, and a deliberately
raised threshold is not evidence of a real low-fuel incident. Keep input hashes,
source samples, failed runs and test provenance alongside results.

The [radio prototype](radio-prototype.md) separately generates voice/DSP files
and gates output. Capture, driver-clock and canonical recording orchestration,
real headset delivery, latency under simulator load, selected-device recovery,
and full visit startup/shutdown remain integration work. A notice in this
journal proves neither successful playback nor that the driver heard it.
