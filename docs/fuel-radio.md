# Connecting fuel supervision to radio

`simcoach.live.fuel_radio` connects one existing `Supervisor` to one radio
dispatcher. This is a reusable host adapter with offline tests, not an enabled
live watcher by itself. The [Driver Visit host](driver-visit-prototype.md) can
explicitly enable it with a selected output and starts muted. That integration
still requires a coordinated hardware listening check. The
[shadow watch command](race-watch-prototype.md) is unchanged
and still emits no audio. Headset routing, intelligibility and hardware timing
remain unverified.

## Host API

The VisitAudio host now reports a hash of the exact validated output-selection
bytes it loaded, alongside whether it selected the Windows transport or used an
injected factory. A readiness review can bind a hearing check to that configuration
and service lifetime; changing a file later cannot silently relabel a running
host's selection. This is configuration evidence, not proof of a connected device
or audible delivery. Older hosts without this field remain unverified for this
check. See the [service contract](../schema/operations_service.md).

The chief prepares the reviewed short phrase `Pit this lap.` using the
[radio renderer](radio-prototype.md). `FuelPhrase.load(render_directory)` checks
the declared phrase, WAV hash, format, frame count and duration. It binds the
manifest and WAV hashes for later rechecks. This verifies artifact identity;
it does not establish how the voice sounds or that its words were heard.

The host constructs:

```python
radio = FuelRadio(
    supervisor,
    private_radio_journal,
    FuelPhrase.load(private_phrase_directory),
    ArchiveStatusSource(explicit_archive_directory),
    transport,  # Fake transport for tests; explicit hardware opt-in for listening.
)
```

The host retains responsibility for calling `supervisor.observe(...)` with
fresh archive data, obtaining the actual cockpit approval, and calling
`radio.tick()` after observations and frequently while audio is active.
Neither constructor nor `tick()` arms the Supervisor. The injected transport
uses the radio protocol's final `can_start` guard; an arbitrary transport that
ignores that contract is unsupported. The adapter itself never creates a
hardware transport, changes a device, or starts/stops capture.

The host must isolate construction errors so unavailable radio does not stop
capture. Runtime radio errors become degraded snapshots. No radio error changes
fuel approval or commands a collector, recorder, simulator or driver clock.
The host must display and preserve degraded outcomes and retain its independent
capture/observation loops; catching an error is not an audible fallback.

## Fresh evidence and message lifetime

The dispatcher consumes each fuel notice once per armed episode. Transmission
identity includes both the arm and notice IDs; the deduplication key binds the
armed episode. Restarting with the same journal never replays its consumed
messages. A new Supervisor starts unarmed, and a new approved episode has a new
identity. A previously failed or suppressed notice is not retried automatically.

Brief copy contention may suspend an approved episode before any return notice,
under the Supervisor's bounded recovery policy. Radio snapshots report
`fuel_state=suspended` and the current supervision reason; no return audio can
start from suspended evidence. Instability after a return notice fully disarms,
so recovery cannot resurrect a consumed call. `supervision_loss` preserves the
loss/resolution even after fresh data returns.

### Monitoring-loss warning

An optional second reviewed package contains exactly `Fuel monitoring unavailable.`
Load it with `FuelPhrase.load(path, expected_text=LOSS_PHRASE)` and pass it as
`loss_phrase` to the same `FuelRadio` owner. The Driver Visit CLI exposes this as
`--loss-phrase <directory>` alongside its explicit audio enable/output options.
It creates no second dispatcher, audio transport or queue. Without this package,
`loss_warning_configured` is false and loss reporting remains status-only.

Temporary suspended recovery remains silent. If the episode fully disarms and
requires approval, the warning becomes eligible after 0.5 seconds, with a fixed
five-second deadline from that committed decision. Its priority is 90, below
the fuel-return call's 100; an invalid fuel call is stopped by its existing guard.
Polling cannot extend the deadline. One occurrence per loss ID is consumed,
including suppression, failure and expiry; unmute/restart cannot replay it.

Evidence is the journaled local loss of monitoring, not a fuel estimate from an
unavailable source. The Supervisor must still have a fresh observation, the same
unresolved loss and unarmed state. Recovery or fresh approval cancels pending or
playing loss speech. Checks repeat after native preparation and throughout
playback; the complete phrase must fit the remaining lifetime. Phrase/manifest
hashes, selected output, priority mute and dispatcher journaling still apply.
Fake transport completion proves neither acoustic delivery nor understanding.

This warning does not say why telemetry failed, how much fuel remains, or that
the driver must pit. A stalled/dead supervisor cannot renew evidence for speech;
an independent process-death watchdog remains open.
Cut-off speech after explicit reapproval remains an output to review, not proof
the driver understood the changed state. Headset validation is still NOT RUN.

### Monitoring-restored confirmation

The optional third package contains exactly `Fuel monitoring restored.`. Pass
`recovery_phrase=FuelPhrase.load(path, expected_text=RECOVERY_PHRASE)` or add
`--recovery-phrase <directory>` to the visit start options. It requires the loss
package and the existing explicit audio opt-in. This is one owner/transport,
not an independently speaking agent.

Only a matching loss that was reported played/acknowledged, or whose transport
confirmed starting under this owner, can justify confirmation. This supports
clarifying a cut-short warning without claiming it was heard. A suppressed,
expired-before-start, failed-before-start or never-submitted loss does not create
recovery chatter. The recovery rule's start confirmation is owned by the current
process. After reopening, only the durable played/acknowledged record establishes
that prerequisite. Recorded software timing does not authorize replay. A Supervisor
restart still requires its own new explicit approval.

Confirmation binds the loss ID, restored arm ID and committed resolution time.
It expires five seconds after resolution and has priority 80, below loss (90)
and urgent fuel return (100). Local state must be armed/return-due and healthy,
with fresh observations. An independent source read must confirm identity,
context, capacity, active game/race and advancing counters without refuelling.
All conditions and original expiry are rechecked after I/O, after native
preparation and during playback. Relapse, stale data, mute, changed audio,
insufficient remaining lifetime or expiry cancel the occurrence. No retries or
replays occur after interruption/suppression, including when unmuting.

`recovery_delivery_state` is separate from loss/fuel delivery and can degrade
delivery health for the corresponding current arm. This phrase reports fuel
monitoring only, not fuel sufficiency, capture coverage or whole-rig readiness.
Rendered files, synthetic transport results and source guards do not prove
audible delivery, driver understanding or acoustic timing; those remain NOT RUN.

The collector's continuity generation persists after a detected mapping break.
Fuel approval needs a fresh cockpit check after the break. The dispatcher
also compares the current generation against a fresh source read before
starting a fuel or restoration call, even if it missed the unavailable
heartbeat. Brief copy contention can still use bounded recovery when the
generation stays the same.

### Fuel-return source checks

Before output, a read-only predicate checks the current notice/arm, original
expiry, bound phrase hashes and a fresh read of the explicit archive. The new
reading must retain source identity, car/track/session and capacity; remain
active and out of pit mode; show valid progress without reset, refuelling or
gaps; and still require the fuel return. It can advance beyond the Supervisor's
last reading within those constraints. This predicate never calls `observe`,
disarms, approves or writes to the Supervisor journal.

File reads may take time, so freshness is checked again after reading. The
transport repeats the guard after device preparation, immediately before output.
The original notice deadline is never extended. There must also be enough
remaining time for the complete phrase plus a configurable start margin
(default 0.1 seconds). This is an admission rule, not a measured device latency
guarantee. Continued invalidity, mute or expiry can still interrupt playback.

The final permission read can itself take time. Immediately after it, the
adapter compares the clock to the evidence window from the latest successful
check for that exact transmission. Expired evidence or a clock regression
blocks the start without another file read. This applies to fuel-return,
monitoring-loss, restoration and Chief-connection warnings. The phrase still
has to fit its original deadline. The checks and the native write remain
separate operations.

`ArchiveStatusSource` compares the selected archive's `run.json` identity before
and after reading status. The existing archive schema still cannot prove every
same-context reset or participant ownership. The [race-watch contract](../schema/race_watch.md)
and its explicit solo-player or viewed-slot/cockpit assumptions still apply.
Fresh fuel and restored-supervision output guards also compare viewed-slot
bindings. A changed slot/count or missing participant evidence cannot play an
old message even before the next Supervisor observation. Pre-start approval
never creates a fuel call before active racing.

Readers retry the entire identity/status transaction only for transient access
or sharing errors (Windows errors 5/32, or EACCES without a Windows error code).
The limit is three attempts, two 10 ms waits and a 50 ms monotonic retry budget;
OS I/O itself is not interruptible. Missing files, malformed JSON and identity
changes are not retried. Producer timestamps and expiry remain unchanged.
An optional `ArchiveStatusSource.diagnostic` callback receives attempted read
failures, exception types and whether retry recovered; the host must preserve
these privately. Exhausted reads remain unavailable and cannot silently re-arm
fuel. This has focused offline evidence, not a new live reliability pass.

## Mute, failures and closure

`set_muted(True)` interrupts output and suppresses pending or subsequently
submitted notices. Unmuting never replays them. A fuel return can remain due
while radio is muted or degraded; the chief must use the agreed fallback and
must not treat silence as an acknowledgment.

`close()` attempts confirmed stop and returns a snapshot. Inspect its `closed`
field: an unconfirmed stop retains the journal lock and owner references,
reports degradation and permits a later close retry. Starting another output
owner while the first stop is unresolved is not a recovery strategy.

Transport failures, unreadable/changed artifacts and failed output states remain
visible separately from the Supervisor's `fuel_state`. Capture is independent.
The radio's durable delivery history remains in its locked append-only journal;
there is no second message queue or replacement artifact format.

## In-memory snapshot

`tick()`, `set_muted()` and `close()` return a snapshot; `snapshot()` can also be
read directly. This is a host API value, not a new durable artifact schema.
If the host persists it, it must record the observation time and run identity
alongside it and preserve failed outcomes.

| Field | Meaning |
| --- | --- |
| `fuel_state`, `supervision_health` | Current independent Supervisor state |
| `supervision_loss` | Latest durable loss/recovery episode; nullable, historical resolution retained |
| `loss_warning_configured` | Whether the reviewed loss phrase was supplied; not proof of delivery |
| `loss_transmission_id`, `loss_delivery_state` | Last loss warning identity and dispatcher state, separate from the last fuel-return call |
| `recovery_warning_configured` | Whether the reviewed monitoring-restored phrase was supplied |
| `recovery_transmission_id`, `recovery_delivery_state` | Last monitoring-restored occurrence and independent delivery state |
| `delivery_health` | `available`, `muted`, `degraded` or `closed`; available means the dispatcher has no observed fault, not that a headset is ready |
| `delivery_state` | Last known radio state, or `none` before a submission |
| `fuel_notice_id`, `transmission_id` | Nullable current fuel notice and last submitted transmission identities |
| `delivery_matches_current_notice` | Whether that delivery belongs to the current fuel notice |
| `reason`, `failure_type` | Nullable controlled reason and error class; raw exception text is not copied into this snapshot |
| `muted`, `closed` | Requested mute state and confirmed closure |
| `audible_delivery_verified` | Always false here; this adapter does not infer hearing |

The [radio schema](../schema/radio.md) defines delivery-state semantics and the
[race-watch schema](../schema/race_watch.md) defines fuel decisions. A `played`
transport result remains distinct from audible delivery or driver acknowledgment.

## Verification boundary

Tests use synthetic status, generated test WAVs and a guarded fake transport.
They exercise the connected Supervisor-to-dispatcher path, fresh-source changes,
mute, immutable phrase identity, late preparation, insufficient remaining
lifetime, restarted journals, new approvals and failures while fuel remains due.
These are offline checks. No live audio is enabled by importing this adapter,
running tests or using the existing shadow watcher. Hardware acceptance remains
a separately coordinated listening check followed by ordinary driving.
