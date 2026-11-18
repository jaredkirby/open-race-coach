# Independent local operations service

Status: explicit offline-tested prototype. It runs fuel supervision and the
existing single radio dispatcher outside the visit coordinator, bound to one
declared visit and its recorded process identity. `visit start --operations-service`
selects it as sole fuel/radio owner and retains the visit's existing controls.
The default remains embedded during this offline integration stage. Do not
start a standalone service alongside embedded audio as a workaround.
No unattended service, automatic restart or logon installation is provided.

The chief prepares a new private output directory and validated fuel plan. Start
only after explicit driver arrival and fresh coordinator identity/heartbeat:

```powershell
uv run python -m simcoach.live.operations_service `
  --visit "<exact-active-visit>" --out "<new-separate-private-directory>" `
  --archive "<exact-archive>" --plan "<approved-plan-json>" --present --duration 600
```

This command is silent by default. Explicit audio additionally requires
`--enable-audio --phrase <fuel-package> --selection <selected-output-json>
--chief-phrase <chief-loss-package>`. Optional loss/restored packages retain the
existing requirements. The chief-loss package must contain the reviewed phrase
“Race chief connection lost.” It uses priority 90 below urgent fuel 100, a
half-second debounce, fixed five-second lifetime, existing deduplication and
mute. Native evidence/expiry checks run after preparation and during playback.
Output also requires the service deadline and departure controls to remain
valid. No generated speech or arbitrary message text arrives over IPC.

Each instance starts muted and unarmed. It takes a per-visit operations lock
and the native radio's session-local output claim. A fresh service ID binds
fuel requests in its own `requests/` directory; old instance or stale requests
cannot approve fuel. Commands and receipts are evidence, not proof of hearing.
Standalone `VisitControls` can address the service directory. Integrated visits
use the visit's mute file and receipt directory via `--visit-controls`. New fuel approval
and unmute require a verified advancing coordinator connection. Mute remains
available when that connection is lost.

New fuel approval rechecks the selected archive after reading the request and
checking the Chief connection. The same fuel policy handles any loss or change
seen in that second read. Fuel and plan decisions also recheck the request age,
service duration and departure under the visit and service request locks.
If a lock is busy, the unconsumed request stays pending until the next check or
its deadline. The receipt distinguishes decision time from publication time.

After a previously connected coordinator stops or becomes unresponsive, already
approved fuel rules continue independently of it. Source/context/refuel faults
still suspend/disarm under the existing Supervisor policy; service independence
does not relax the selected context, viewed-slot confirmation or fuel checks. A connection
warning requires a fresh independent identity probe showing the bound process
absent/exited, or the same running process with stale heartbeat. Denied/unknown
probes cannot support spoken failure claims. Recovering connection cancels an
obsolete warning; it does not replay missed messages or reapprove fuel.

Explicit departure in either directory closes the service. Reported chief
closure also closes it without inventing departure. The maximum duration is
bounded at two hours; reaching it closes operational output, not the driver
visit. The service never controls the simulator, records occupancy, starts or
stops capture, adopts a worker, or asserts capture readiness. Process failure
does not change physical presence. Failed publications/journals propagate with
retained evidence and attempted audio closure. Unconfirmed close remains a
failure, not a successful release.

Fuel radio freshness is checked against time sampled after source I/O. A page
published during the read may be valid; future pages, regressing clocks and
expired original notice deadlines remain refused. The service does not extend
deadlines to compensate for blocking reads or native preparation.

Windows tests use separate hidden chief/service processes, synthetic telemetry,
unique test output claims and fake audio. They are not headset, actual simulator,
sleep/resume, logon or resource-impact evidence. The integrated client launches
once, pins actual process identity separately from the launcher, and refuses
stale or rebound evidence. Fuel forwarding is acknowledged only by the matching
service-ID/request-byte-hash receipt. Ambiguous publication is not retried;
receipt-copy failure remains pending without repeating the command. Failed
temporary files and conflicting receipts are retained.

Capture and clock continue through operational startup failure, exposing unknown
fuel supervision and an incomplete visit. Closure checks output closure plus
actual process/launcher exit. Outcomes retain service radio, launch/forwarding
evidence and a hash of its supervision journal. Service liveness alone does not
establish capture, fuel or rig readiness. Default-path migration and observed
ordinary live service-backed visits remain open.
See the [artifact contract](../schema/operations_service.md).

Combined Windows process tests now exercise the real visit host, recording
worker and service together with a generated adapter and synthetic archive
status, audio disabled. They cover recording before synthetic garage departure,
practice/race/Time Trial context boundaries, canonical validation/analysis and
closure, plus a test-owned service exit while recording continues. Failure
remains visible in the final visit and outcome; it does not authorize a restart.
The test provenance is synthetic even where operational status uses the selected
`live` code-path label. This is not AMS2 adapter, headset or ordinary visit evidence.
Inspection artifacts use bounded fresh-read retries for Windows sharing errors,
retaining failures/recovery in the diagnostic evidence. Persistent unreadability
still breaks verified connection; no rejected approval is automatically resent.

The same bound forwarding/receipt path supports explicit
[plan changes](fuel-plan-transitions.md). The service requires a fresh chief
connection and matching current plan ID, records the change and clears approval.
Two new observations and a later cockpit check are required before fuel can arm.
Capture and seat time remain owned by the visit and continue independently.
