# Explicit Driver Visit clock

The local `VisitClock` component records a Driver Visit only after explicit
confirmation that the driver is present. It separates observed activity from the
declared reason for stationary/menu time. It does not detect a person in the seat,
run a background service or replace the existing [driver clock](driver-clock.md).

An embedding coordinator creates a fresh private directory and passes UTC plus
`time.perf_counter()` timestamps. Feed fresh activity observations regularly;
intervals over one second default to unknown. It should start only after the driver
or Race Chief explicitly confirms presence, and call `end()` promptly on departure.
No active visit is restored after restart. Returning requires a new explicit start.

The archive-backed visit host uses `ActivityEvidence` to separate source progress
from repeated polling. It requires two advancing supported observations from one
verified archive identity and context. Source/context changes, sequence regressions,
invalid data and clock discontinuities make activity unknown until a usable pair
returns. Fresh menus, pauses and replays can establish non-driving independently
of capture admission or fuel approval. Simulator activity still does not establish
physical presence or which participant the driver controls.

The collector publishes status roughly once per second. Between publications the
host may hold an admitted classification, labeled `held`, only until the original
source timestamp plus two seconds and its corresponding monotonic deadline. Polls
never extend either expiry; a new heartbeat without source sequence progress is
unknown. `advancing` denotes a new source observation, not continuous measurement
of the entire interval. Driving/non-driving partitions are therefore bounded
activity estimates within explicitly declared presence, not exact seat sensors.
Host observation gaps over one second remain wholly unknown.

Current diagnosis appears in host `activity_source`; each clock interval retains
optional `source_evidence` for its current endpoint, including original source
time, sequence, expiry and whether it was advancing, held or unknown. This makes
the estimate auditable without copying raw telemetry or context labels. It does
not reclassify earlier intervals or prove coverage between source observations.

```python
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from simcoach.live.visit_clock import VisitClock

# Only after explicit confirmation of presence; directory must not exist.
visit = VisitClock(Path(".scratch/visits/example"), presence_confirmed=True,
                   at=datetime.now(UTC), elapsed=perf_counter())
# The coordinator supplies a fresh activity classification, or "unknown".
visit.update("not_driving", at=datetime.now(UTC), elapsed=perf_counter())
visit.annotate("setup", at=datetime.now(UTC), elapsed=perf_counter())
# Continue observing; on explicit departure:
visit.end(at=datetime.now(UTC), elapsed=perf_counter())
```

Annotations are prospective. Supported reasons are intentional rest, setup,
assistant waiting, other waiting and unspecified. Annotation without a fresh
activity argument marks its closing interval unknown; passing an old classification
would falsely imply observation. The host may pass a still-valid, explicitly
labeled held estimate with its `source_evidence`. Clear a no-longer-applicable reason with
`annotate("unspecified", ...)`. A reason does not turn driving or a telemetry gap
into waiting. Reason totals subdivide only observed `not_driving` time, and must
never be added to activity totals.

New clocks also clear the previous reason to `unspecified` after a positive
interval classified as driving. This prevents a setup, wait or rest declaration
from carrying across a stint into the next stop. A single driving endpoint, pit
movement, transition, unknown interval or clock fault does not clear it. A fresh
explicit annotation takes precedence at its timestamp. The journal records the
automatic change with `cause=driving_resumed`; past intervals remain unchanged.
Started events and summaries identify this policy as `reason_policy=until_driving`.
Older ledgers without that field retained reasons until explicitly changed and
must not be interpreted using the new policy. This does not establish the cause
of a later stop or measure all assistant-caused delay: unspecified remains
unspecified until a new declaration, and missing observations remain unknown.

Before the host starts, the Chief can append a prospective reason to an explicit
arrival. The outcome preserves that declared purpose separately from the
unknown pre-host activity. The declaration ends at a new reason, host claim or
pre-host departure. See [arrival and pre-host timing](driver-visit-prototype.md).

Inspect `status.json` for recorded totals, `events.sqlite3` for the append journal
and terminal `summary.json` after departure or interruption. `close()` is an
interrupted observer at the last measured timestamp, not a claim that the driver
left. A crash without a summary leaves an incomplete visit; preserve its artifacts
and report subsequent time as unmeasured. Do not use a running simulator, an old
heartbeat or the last stored activity to fill in missing occupancy.

Accept terminal publication only when the summary and terminal status agree.
An interrupted publication can leave a summary with an older running checkpoint;
preserve both for review. In-process failures latch `publication_failed`, and a
later `close()` does not relabel them complete or retry the end event. Explicit
departure remains recorded even when publishing its result fails.

The implementation is tested with synthetic clocks and activity observations.
That is not live validation of presence, activity classification or end-to-end
visit coordination. Keep records local and preserve failed artifacts. The full
contract is [Driver Visit time ledger](../schema/visit_clock.md).
