# Chief-owned Driver Visit prototype

The coordinator starts an explicitly declared visit, observes the selected
continuous archive, owns a separate recorder child, and closes its Recorded
Sessions through validation and deterministic analysis. The chief operates it;
the driver supplies intent, presence, fuel confirmation and departure.

Status distinguishes the original confirmed arrival from the current clock's
presence declaration. Departure reports absent while finalization continues;
timeout or interruption reports unknown. Neither an alive process nor an initial
arrival flag proves continued occupancy. A failed clock publication remains a
failure even when departure intent was accepted. Older saved status files are
preserved; use their clock/departure evidence when their arrival flag is stale.

Fuel approval for race/Time Trial now supports an explicitly confirmed viewed
slot using `visit fuel --confirm-player-view --participant-index <slot>` with
the existing cockpit litres, verification timestamp and note options. It
requires new collector participant evidence and an exact matching plan; it
does not automatically change plans when session mode changes. A stationary
race pre-start can be approved before launch, with fuel speech inhibited until
racing. See [fuel supervision](race-watch-prototype.md) for the conditions and
limits. Source-file updates do not upgrade the running archive collector.

The chief can explicitly [replace the plan within the same visit](fuel-plan-transitions.md)
using `visit plan`. Acceptance disarms fuel and requires new source observations
and cockpit confirmation. Recording and presence continue; no automatic mode
selection, simulator setup or fuel loading is implied.

This is an offline-tested lifecycle prototype. Its live mode has not been
validated in an ordinary visit. Audio is disabled by default. Explicit opt-in
connects the [fuel-radio adapter](fuel-radio.md) to a selected output and starts
muted; neither a generated phrase nor a successful closure proves an audible call.

## Recording recovery within a visit

The chief may explicitly add `--operations-service` to a live-archive start.
This selects one hidden [operations service](operations-service.md) for fuel and
radio and constructs no embedded operational owner. Audio still requires the
normal options plus `--chief-phrase <reviewed-connection-loss-package>`. The
visit bound must be at most two hours; fixture visits cannot select this mode.
The default embedded path remains during offline integration. Neither path's
offline checks establish a fresh live acceptance result.

Fuel CLI requests and mute/departure controls keep the visit path. Only a bound
service receipt acknowledges forwarded fuel approval. Parent status separates
service identity/freshness, pending receipt errors and capture readiness. No
restart follows stale status or ambiguous launch. Capture continues through
operational startup failure with unknown fuel state and an incomplete visit.
Closure checks the actual service exit; outcomes include `operations/radio.jsonl`,
service status, hashed supervision journal and command evidence. A running
service does not establish `ready_to_drive`.

The live coordinator can replace an unexpectedly ended recorder without restarting
the visit clock, radio, fuel supervisor or independent archive. This is bounded to
three worker starts per visit. It waits at least two seconds after observing exit,
then requires fresh source admission for the observed context, the original launcher
handle to report exit, the actual worker's PID and creation identity to be observed
absent or exited, and the shared capture lock to be available. The worker records
its own process identity because a Windows virtual-environment launcher can have a
different PID. The replacement must acquire the same capture lock before opening
the adapter. Lock scope is the selected visit root, not every recorder on the rig.

The host and worker sample free space on their recording destinations. With a
fresh reading below the 1 GiB floor the host waits to start a worker, and an
active worker attempts to seal its prefix and exit. The Chief can inspect the
storage readings and the worker's stop reason. Replacement still requires native
exit evidence, fresh source and storage observations, and the capture lock. The
visit clock and independent fuel owner continue under their existing rules.
Storage probe errors also withhold capture readiness and new checkpoint
handoffs. Storage readings reserve no space.

Stale status, missing process identity, denied process access, a held lock or an
ambiguous launch cannot authorize replacement. These leave a degraded state for
chief inspection. There is no force-kill, adoption after coordinator restart or
retry of old validation/analysis effects. Normal source gaps already wait within
the same worker and do not consume another attempt. Explicit departure prevents
new starts; the original visit deadline and presence boundaries remain in force.

The first worker stays at `worker`; replacements use fresh `worker-0002` and
`worker-0003` directories. Each keeps its own captures, failures, closure and
debrief. `capture-attempts/` preserves launch, process-exit and first-ready receipts,
including the evidence used for replacement. Outcomes and reviews retain every
attempt; `current_worker` selects current readiness only. First-ready timing uses
host monotonic observations, not complete capture coverage or assistant-caused
waiting. A recovered visit remains `incomplete` with `capture_interrupted`, even
if the later recording closes successfully. Recovery does not erase the gap.

Synthetic tests exercise real Windows child failure and replacement under one
clock without simulator or audio access. Actual ordinary AMS2 recovery remains
unvalidated. The driver does not need to operate recovery controls or repeat a
driving exercise to demonstrate this offline path.

## Start and control

For an explicitly selected deployment, use the [guarded source release](visit-releases.md)
so the host, recorder and operations children verify the same source. Preparing a
candidate does not activate it or declare driver presence.

When the driver reports being seated, the Chief can register arrival immediately.
This records the boundary even if the plan, archive or host is still being prepared.

```powershell
uv run python -m simcoach.cli.visit arrive --present --out "<private-visit-root>"
uv run python -m simcoach.cli.visit start --present --arrival "<printed-arrival-id>" `
  --intent "Six consistent laps" --archive "<explicit-existing-archive>" `
  --plan "<private-fuel-plan.json>" --out "<private-visit-root>"
```

If the Chief retries arrival while the same driver is still seated, `arrive`
returns the one coherent open receipt with `reused: true` and its original
timestamp. Use the same visit root throughout. The host claims the receipt once
under the visit lock and includes the earlier interval as unknown activity.
A live `start --present` can claim the sole open receipt automatically. If the
root contains none, the visit clock starts at host entry.

Use `depart-before-host <arrival-id> --left --out <private-visit-root>` if the
driver explicitly leaves before the host claims the arrival. If an unresolved
arrival is stale or a prior claim was interrupted, inspect its files before
deciding which boundary still applies. To retire an obsolete unclaimed receipt
when departure timing is unknown, use
`abandon-before-host <arrival-id> --confirm --out <private-visit-root>`.
Abandonment preserves the original declaration and partial files and records
unknown presence and duration. The Chief supplies these operations.

While the host is unavailable, the Chief can record a prospective reason based
on the driver's intent and the work in progress:

```powershell
uv run python -m simcoach.cli.visit note-before-host "<printed-arrival-id>" `
  --reason assistant_wait --out "<private-visit-root>"
```

This declaration lasts until the next reason, host claim or departure. Use
`unspecified` when the reason ends and the host is still unavailable. The opening
period before any reason is unspecified. Once the host claims the arrival, use
the active host's annotation command for new reasons. The outcome shows declared
pre-host waiting separately from tagged non-driving time during the host run.

The process prints a new private directory. It does not start or stop the
independent collector, change simulator settings, or publish artifacts. Do not
run a second standalone recorder or clock for this visit. Presence is an
assertion, not inferred from simulator activity. The short default root
`.scratch/v/` and internal Windows extended paths limit path-length failures.

Activity accounting requires advancing observations from the selected archive
identity and current context. Repeated status polls may hold an admitted estimate
only until its original two-second expiry; source/context changes revoke it.
Host `activity_source` and clock interval `source_evidence` retain advancing,
held or unknown diagnosis. These are bounded activity estimates inside declared
presence, independent of fuel approval and capture admission.

From another chief-owned process, declare a reason prospectively:

```powershell
uv run python -m simcoach.cli.visit reason "<visit>" intentional_rest
```

Supported reasons: `intentional_rest`, `setup`, `assistant_wait`, `other_wait`,
`unspecified`. They subdivide observed non-driving time only. They do not make
gaps known or classify driving as wasted time. See [Visit Clock](visit-clock.md).

For solo practice/test, record an actual fresh loaded-fuel and player check using:

```powershell
uv run python -m simcoach.cli.visit fuel "<visit>" --cockpit-litres 8 `
  --verified-at "<UTC-ISO-check-time>" --confirm-solo-player `
  --note "Actual cockpit comparison and context confirmed"
```

The number is illustrative, not a universal load recommendation. Inspect the
request receipt and current supervision status for acceptance. A queued request
does not establish fuel approval. Capture readiness requires a live child,
fresh status, recent ticks in an active intake phase, and independent archive
progress/context checks.
When a previous worker still holds the shared capture lock, a new visit keeps
time and reports `waiting_for_capture_lock`. It waits before creating a worker
attempt. The Chief can check the previous worker's closure while the clock runs.
The host publishes an expiry from the earlier of the last worker tick and archive
observation deadlines. Reading a recent status cannot extend either deadline.
Local controls and checkpoint requests check that expiry before use.
Capture admission also checks agreement between source timestamps and elapsed
offsets, plus observer wall/monotonic time, against fixed baselines and successive
intervals. More than 0.5 seconds of disagreement removes readiness even when all
counters keep advancing. A new coherent source pair may restore capture admission;
fuel approval remains separate. This has offline fault-injection evidence, not
actual sleep/resume validation.
`recorder_intake_ready` alone measures polling, not fresh simulator evidence.
Whole-rig `ready_to_drive` remains
false because full audio/readiness integration is not established.

The worker retries brief heartbeat file-access failures within three attempts
and a 50 ms retry budget, without extending the original five-second lease.
Missing, corrupt, future and genuinely expired leases still fail closed.
Read failures and lease ages are retained in private worker diagnostics;
unreadable leases are distinguished from expired leases. These bounded reader
changes have offline tests; they require a fresh ordinary capture before a
live reliability claim.
Worker status publication is best-effort after process identity is established:
an exhausted status I/O write no longer aborts capture or skips canonical closure.
Failed unique temporaries and a best-effort error journal are retained; the next
successful observation includes cumulative publication diagnostics. Stale reader
evidence still removes readiness. Failed terminal status publication returns a
nonzero worker exit even if canonical closure succeeded. Canonical artifact I/O
errors remain failures. This recovery has offline tests, not a live reliability pass.
The host preserves archive reader diagnostics in its supervision journal and
samples observation time after the read, so a newer status published during a
retry is not compared against a timestamp sampled before the read began.

When the driver leaves:

```powershell
uv run python -m simcoach.cli.visit stop "<visit>"
```

The first stop declaration records UTC and a performance-counter boundary.
The coordinator ends the clock at that boundary before waiting for recording
closure, so analysis time is not seat time. Invalid/regressed boundaries remain
an incomplete visit requiring review; old intervals are not rewritten. A timeout
or Ctrl+C records interruption, not an inferred departure. New presence requires
a new visit; no clock is automatically resumed.

## Selected output and local controls

Before a coordinated listening check, identify the actual headset route using
the read-only [output inventory](radio-prototype.md#selecting-the-intended-headset).
Save its selection privately and prepare the reviewed `Pit this lap.` render
package. The chief can then add all three options to the start command:

```powershell
  --enable-audio --phrase "<private-render-directory>" `
  --audio-selection "<private-output-selection.json>"
```

Add `--loss-phrase "<private-loss-render-directory>"` to enable the reviewed
`Fuel monitoring unavailable.` warning through the same dispatcher. It is optional
for backward compatibility; `loss_warning_configured=false` means no spoken loss
warning is configured. It requires the existing audio opt-in and selected output,
starts muted, and is refused in synthetic fixture mode. See the
[loss warning policy](fuel-radio.md#monitoring-loss-warning). Optionally add
`--recovery-phrase "<private-recovery-render-directory>"` for the reviewed
`Fuel monitoring restored.` follow-up. It requires the loss package and fresh
verified restored supervision after a corresponding output attempt. These
options do not establish acoustic delivery or driver understanding.

An archive is required; synthetic fixture mode refuses audio. The selection
has no default-device fallback. Audio starts muted and requires a fresh explicit
Unmute. The host polls priority mute between source observations; this is not
a measured acoustic mute-latency guarantee. Routing, volume, intelligibility and
actual stopping still need the driver's listening check before operational use.

Open the [local controls](visit-controls.md) before driving:

```powershell
uv run python -m simcoach.live.visit_controls --visit "<visit>"
```

The window provides mute, departure and waiting reasons without terminal input
from the driver. It does not raise itself during driving or install global keys.
Closing it does not end the visit. Read coordinator acknowledgments separately
from submitted requests. Radio errors remain visible as degraded delivery and
do not stop capture or the presence clock. A failed or muted fuel call is not
automatically replayed on recovery; the chief must retain the agreed fallback.

## Capture ownership and failures

A cooperating coordinator and child use distinct locks under a common root.
The presence clock starts immediately, while recording waits for two fresh,
advancing observations in one supported observed context, including garage/grid
and fresh menu/replay states. Source status exposes actual context and whether
it matches the original plan. A context change requires a fresh pair, but does
not require editing the fuel plan to preserve the next race or Time Trial. This
[source admission](capture-startup.md) is independent of fuel approval: unknown
fuel must not prevent preserving an otherwise usable capture. Missing or corrupt
archive identity/data keeps admission closed. The worker is launched once;
startup failure does not silently spawn another worker.

The child requests cooperative stop if its first tick has not arrived within
15 seconds. That timeout is a failure even if a late tick can be finalized.
This bounds the initial startup decision, not a blocked adapter call or every
successor session. A blocked call can retain ownership beyond the deadline.
The child holds capture ownership until its recorder actually exits. Missing,
future or expired coordinator heartbeat requests graceful stop within the
five-second lease window. Parent failure cannot stop the independent archive.
Hung recorder calls can delay cooperative stop; the parent records an unconfirmed
worker after its bounded wait and does not kill it or silently replace it.
Different roots are not a machine-wide ownership boundary.

Every successfully allocated Recorded Session is inventoried, including context
boundaries. A failed successor allocation cannot rewrite its finalized
predecessor. Additional unregistered artifacts under the worker's own output
directory are retained and flagged for inspection, not guessed to be valid.
The recorder remains buffer-based: forced worker exit can lose normalized ticks;
the independent raw archive remains necessary. The AMS2 adapter binds recorder
metadata, lap-invalidation evidence and tick to one supported stable copy, checking
the sequence before, within and after copying. Unsupported or unstable copies are
not recorded. Metadata-unavailable observations are skipped during the grace period
instead of attaching new ticks to stale context.

The adapter also requires source sequence progress before producing a new tick.
Repeated pages cannot acquire new Capture Time merely because they were polled
again. A first page after connection is only a baseline; inspection reads do not
consume it. A lower sequence, including a possible wrap, starts a new transient
source epoch and requires subsequent progress. The recorder separates these
histories with a new Recorded Session ID; this does not establish a restart.
Explicit static snapshot conversion remains available for offline replay.

An observed change to a different active viewed-participant slot also starts a
new epoch on an advancing page, retaining its first usable tick in the successor.
Returning to the previous slot starts another recording. This prevents positions,
lap histories and invalidation evidence from different viewed slots being merged
when car/track/mode and lap number remain equal. It is not a player-identity
detector: same-slot reassignment and unseen changes remain unresolved. These are
offline synthetic results, not an additional live simulator acceptance claim.

Live visit workers opt into continuous source-gap handling. After metadata or
ticks are unavailable beyond their grace, the recorder seals the supported prefix
once and waits under the same worker and capture lock. It allocates a successor
only when a usable paired metadata/tick observation returns, keeping that tick's
original Capture Time. No samples are interpolated across the gap. Source-gap
count and current reason remain visible; completed artifacts do not imply complete
coverage. Stop and lease loss still request cooperative closure while waiting.
File, finalization and adapter exceptions remain failures and are not retried.
The standalone recorder and synthetic fixture worker retain their finite behavior.

AMS2 adapter 0.3.2 opens the existing mapping read-only for each copy and
releases both the view and the handle before reporting it. If the mapping is
absent, the recorder receives no observation. The first stable page after an
observed absence establishes a new epoch and baseline. Later advancing pages
retain their original Capture Time from the same worker clock. A native Windows
rehearsal replaced a disposable mapping under the same name while the collector
and recorder remained running. Both received replacement data and the recorder
finalized two valid sessions. Actual AMS2 restart behavior remains NOT RUN.

Observed lap-counter regressions now finalize the supported prefix and start a new
Recorded Session ID with the incoming tick and its original Capture Time. This is
a counter-discontinuity boundary, not a claim that the simulator restarted. Pauses,
pits and same-lap distance reversal do not independently trigger it. Resets that keep
the same counter remain unresolved. Other adapters retain a sequential compatibility
fallback; this change establishes no ACC snapshot coherence or live validation.

Only closed captures that pass validation reach analysis. Complete
`insufficient_data` outputs remain useful evidence. Failed recordings, invalid
timestamps, failed analyses and partial files remain private. An exclusive
closure-intent artifact prevents blind repeated analysis after an ambiguous
crash. Review existing outputs before recovery; restarting does not redo effects.

`worker/debrief.md` collects canonical results without Coach Refinement or a new
causal interpretation. It still needs chief review and a supported next step.
The automatic [visit outcome](visit-outcome.md) adds per-recording lap timing and
exclusions for the departure debrief. The Chief can use it before reviewing the
full Analysis Runs.
Exit zero describes that invocation's closure; inspect per-recording outcomes,
clock publication state, outcome receipt and mode. It does not prove coaching
effectiveness.

## Recovery inspection and outcome evidence

For a pit return during an ongoing visit, the chief can request a
[stint checkpoint](stint-checkpoint.md). It rotates to a successor recording and
reviews the prefix in one background job, without declaring departure. Inspect
the checkpoint receipt and review its captured-time consistency, exclusions and
deterministic output before giving a next objective. This is an offline-tested
prototype; ordinary live performance and debrief usefulness remain unverified.

The chief can inspect one explicit visit without changing it:

```powershell
uv run python -m simcoach.cli.visit inspect "<visit>"
```

The [recovery inspector](visit-recovery.md) compares current Windows process
creation times with recorded identities. A stale heartbeat, old status or reused
PID cannot prove a current owner. Legacy receipts without creation time and
permission failures remain unknown. This command does not acquire capture,
restart a worker, replay audio, repeat analysis or infer departure. It does not
certify that locks are free or that restarting is safe.

After closing its owned artifacts, the host writes a new private
[visit outcome](visit-outcome.md) and an `outcome-result.json` receipt containing
the exact report locator and hash. It preserves clock partitions, every capture
and analysis attempt, failures, insufficient results, and reported radio outcomes.
Unknown metrics remain unknown: completed output is not proof of hearing,
coaching usefulness, capture coverage or a measured acoustic latency.

The terminal parent status describes capture/clock closure; the outcome receipt
separately establishes whether the summary was published. A publication failure
returns nonzero and retains diagnostics and partial output without rewriting the
terminal source status. A written outcome still needs chief review and the
driver's corrections. No new Coaching Instruction or Coach Refinement is created.

Use [explicit output reviews](visit-review.md) to retain useful, insufficient,
misleading and failed results, then `visit outcome "<visit>"` to publish a new
snapshot including those reviews. Earlier outcomes and their hashes stay intact.
Coverage auditing is a separate [bounded offline command](archive-coverage.md);
large archives are not automatically scanned in the departure/closure path.

## Offline acceptance and remaining work

`start --fixture <synthetic-json> --present --intent "test only"` runs the same
child/closure path without opening a simulator mapping. Its clock is explicitly
a test stimulus in a replay-mode parent, not actual driver seat time. Keep fixture
provenance separate from real raw telemetry. See [artifact contract](../schema/driver_visit.md).

Remaining gates include real departure/restart behavior, selected-headset output
and mute timing, control-window usability, crash recovery beyond observed lease
shutdown, live startup timing, and a reviewed next-visit consistency comparison.
Prepare these offline before requesting the minimum ordinary-driving check.
