# Review a stint while the visit continues

The local visit prototype supports a chief-requested checkpoint. It finalizes
the recording prefix and runs validation and deterministic analysis in one
background job while the same worker records into a successor Recorded Session.
Driver presence, fuel approval and radio state are unchanged.

```powershell
uv run python -m simcoach.cli.visit checkpoint "<exact-visit-directory>"
```

The driver need not run this command. The chief should request it at a suitable
stint boundary, normally after a pit return, then inspect the returned receipt
path. `requested` means only that a request was published. A fresh matched host
and worker, advancing capture and a non-departing visit are required. Busy,
expired or mismatched requests are refused; they are not deferred into another
stint. There is no automatic pit detector or automatic retry.

Fresh storage evidence participates in the capture deadline. Before it accepts
a new handoff the worker also checks its local storage state. A storage refusal
leaves an explicit receipt.

The recorder allocates a successor before handing off the old buffer. Both retain
original Capture Time. A mid-lap checkpoint can leave ineligible fragments on
either side; it does not join them into a fabricated complete lap. Existing
context and source-gap boundaries retain their separate behavior. Rotation still
requires disk allocation, so continued capture does not promise zero sampling
delay or complete simulator coverage.

Each `worker/checkpoints/<id>/` holds intent, handoff, closure, status and debrief
evidence. `stint-debrief.md` includes consistency by recorded timing source and the existing
deterministic output. `consistency.json` retains every lap and exclusion, with
statistics over all valid timed laps within each source, including slow ones.
Captured and simulator-reported timing stay separate; mixed or unknown sources
withhold combined pace and spread. The version 2 summary retains per-lap source
and separate source groups; prior version 1 outputs remain unchanged. Groups also
stay separate by Recorded Session across context changes.
The chief must review sufficiency and choose one supported objective. Checkpoint
completion does not establish useful coaching or driver receipt of the debrief.

On Windows, checkpoint storage uses extended paths internally so request IDs and
atomic temporary filenames do not exceed legacy directory-length limits. Failed
publication remains a recorded failure; no checkpoint intent is retried to hide it.

At departure, the worker reuses checkpoint results instead of repeating their
Analysis Runs. Uncertain or failed effects remain explicit, with files and
diagnostics preserved. A hung job retains worker ownership and leaves closure
unconfirmed. No restart attempts to reconstruct lost in-memory ticks. The
independent archive remains separate; this mechanism does not guarantee recovery
from process death or power loss.

Confidence is high in the bounded synthetic tests: intake continues while
finalization is held, repeat checkpoints do not repeat analysis, post-effect
failures remain uncertain, and real Windows host/worker/service processes retain
one seated visit. Ordinary live operation, resource impact and useful time to
debrief remain NOT RUN. See the [artifact contract](../schema/stint_checkpoint.md)
and [visit workflow](driver-visit-prototype.md).
