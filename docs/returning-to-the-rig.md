# Returning to an accepted AMS2 rig

This is the chief's procedure for an ordinary visit after scoped live acceptance.
Prepare the software and saved evidence before the driver arrives. Use the
[live validation runbook](live-simulator-validation.md) for a new rig or a
specific unresolved change; do not repeat pedal/cut exercises by default.

The [Chief readiness assessment](chief-readiness-assessment.md) describes the
read-only evidence pilot joining these checks. Its result is a review aid, not an
automatic release to drive; missing current evidence remains visible.

## Before the driver arrives

Read the [idle Rig Engineer report](idle-rig-preflight.md) for outstanding Steam
work, USB problems or controller profile changes detected between visits. Its
timestamps identify which checks still need fresh arrival evidence.

When recurring engineering is enabled, the Chief inhibits its private maintenance
authority at the first return-to-rig message and verifies any active job's stopped
state before handoff. Re-enable it after the driver explicitly permits engineering
again. The [Development Engineer workflow](development-engineer.md) preserves the
pending job and its original evidence through this interruption.

1. Review the last completed and failed outputs, the driver's feedback and any
   unresolved reliability issue. Preserve them. Select one practice objective
   supported by that evidence; an insufficient or inconsistent Analysis Run
   does not justify a causal Coaching Instruction.
2. Check the checkout, environment and storage. Prepare a reviewed estimate of
   remaining archive and recording writes, including closeout, and leave the
   collector's reserve available. Refresh free space at the actual destinations
   before handoff. Run tests for changes before using them. Keep maintenance
   outside driving, and retain a recovery path.
3. Identify the existing continuous collector's exact run directory from its
   launch record and current process, not just the newest directory. Do not
   start another collector when one is already active. Preserve all prior runs.
4. Prepare a stint length and a fuel budget. Freshly loaded fuel and the actual
   car/track remain cockpit-time checks; yesterday's healthy status or fuel
   reading is not current evidence.

## Rig applications and session preparation

Keep a private rig profile naming required applications, devices, selected
profiles and output routes. On rigs using MOZA Pit House and SimHub, include both
in preparation before launching the simulator. This is an operating sequence,
not a claim that either vendor requires that launch order. Confirm existing
process identities before launching; an inaccessible process list means unknown,
and a launch timeout requires reconciliation rather than another launch attempt.

MOZA readiness requires the actual connected wheelbase, wheel and pedals plus
the approved profile. An open home screen, disconnected-device defaults or a
game-launcher configuration label cannot establish control readiness. Preserve
accepted calibration evidence; do not repeat pedal exercises without a new reason.

For SimHub, verify the intended game connection, selected effect profile and
required enabled output. Plugin enablement and a saved device name do not prove
current function. Bind the output to the rig's confirmed purpose: bass shakers,
pedal vibration and headphones may use different devices. Do not redirect haptic
audio to the radio output or run test effects merely to demonstrate a process is
alive. Hardware powered off between visits is an unresolved arrival dependency,
not proof of a fault. Resolve genuine routing choices with the driver once and
retain the answer privately.

Choose the intended practice/race/Time Trial recipe before arrival, including car,
layout, conditions, opponents and planned laps. Keep observed navigation separate
from proposed steps. Reobserve after each menu action; verify the destination and
actual loaded context instead of replaying old coordinates. A historical recipe's
fuel amount is evidence of that visit, not a standing load recommendation: apply
the current reviewed fuel plan and confirm the cockpit amount. Record cold/warm
loading time separately from seated setup time and unknown intervals.

The [reviewed recipe compiler](../schema/session_recipe.md) can rehearse a path
against hashed synthetic observations and retain where missing selectors or
readiness evidence stop it. Its review binds exact recipe bytes and the current
fuel-plan reference. A rehearsal is preparation evidence, never live rig readiness;
selected-window recognition and physical navigation still need separate validation.
Do not assume a selected-window capture is isolated: reject unrelated overlapping
content and retain unknown focus/native identity rather than guessing. The current
[observer evidence](input-observation-boundary.md) documents this unresolved boundary.

Before handing over controls, require fresh evidence for the applications and
devices, selected session, canonical recorder intake, independent capture, fuel
supervision and selected communication route. Report each unavailable capability
explicitly; an installed app or healthy collector alone is not whole-rig READY.

## At the start of the visit

The chief runs these commands; the driver should not need to manage terminals.

The [Driver Visit prototype](driver-visit-prototype.md) combines the clock,
recording and closure workflow below, with explicit opt-in radio and local
controls. It has synthetic test coverage but still needs an observed ordinary
visit. Choose that coordinated workflow or the standalone commands below;
running both duplicates clocks and capture ownership. With the coordinated
workflow, record declared arrival using `visit arrive` as soon as the driver
reports being seated. Pass the returned ID to `visit start` after preparation.
Time before the host starts is included as unknown activity, and the host clock
continues while capture waits for usable source evidence.

Start the clock when the driver reports being at the rig, including menus and
setup time. Run it in its own terminal/process and retain the printed directory:

```powershell
uv run python -m simcoach.cli.driver_clock
```

The standalone clock configures its logs under its private run directory before
loading the adapter. This avoids sharing the recorder's rotating log. It does
not repair logging across arbitrary processes that import these modules first.

Check the explicitly selected continuous archive:

```powershell
uv run python -m simcoach.cli.capture_health --run "<archive-run>"
```

See [capture health](capture-health.md) for the report and exit codes. Exit 0
means two fresh status observations showed advancing samples. Exit 1 means
degraded or unknown evidence; inspect the reason before proceeding. A missing
mapping while AMS2 is closed is not evidence that the collector has stopped.
The report expires and must be refreshed after a restart or interruption.

Capture health is one readiness input. Confirm the intended simulator, car,
track and practice context, loaded fuel, recording destination and any needed
audio route separately. It does not certify controls, raw archive integrity,
whole-rig readiness or successful delivery of advice.

## Fuel release check

The current release check has known integration limits: independently advancing
archive and service publications can prevent an otherwise healthy feed from
producing a coherent decision, and a legacy unmute-receipt hash comparison can
reject the native receipt format. A private service-owned handoff candidate is
under review; it is not deployed or cleared for driving. Do not describe these
issues as fixed or send the driver onto track to make the check pass. A HOLD
remains a HOLD until the Chief resolves its evidence and the affected path is
verified.

1. Use a consumption estimate from the same car/track and relevant conditions,
   or the simulator's estimate. Record its source, units and uncertainty.
   Retain every observed span and its exclusion reason. Slow or invalid laps can
   supply fuel evidence when continuity is supported. Separate pit/refuel/reset
   intervals and incomplete laps; unexplained high-consumption fragments must
   withhold a candidate instead of silently lowering the estimate. See the
   [saved-evidence fuel budget](fuel-budget-evidence.md).
2. Budget for the actual start procedure, any needed outlap, planned timed/race
   laps, inlap and two additional reserve laps. Include formation/warmup and grid
   idling when applicable. Apply an explicit uncertainty margin, round
   the required load up, and keep it within the tank capacity. Do not extend a
   stint when consumption is unknown; observe normal driving first.
3. Confirm the amount actually loaded in the cockpit after any setup or session
   change. Reconcile telemetry against the cockpit before relying on converted
   fuel values. Stale telemetry, changed capacity, refuelling or a session reset
   invalidates the previous fuel estimate until reviewed.
4. Define a return threshold before release: fuel to reach the pit from the
   current position plus the reserve, with the same uncertainty margin. Use a
   conservative full-lap return allowance when the position-based requirement
   is unknown. Refresh fuel evidence during ordinary driving; do not wait for
   the threshold to be crossed before deciding how to communicate.
5. For a stint with fuel radio, keep the driver stopped during the handoff.
   Submit the cockpit amount through `visit fuel` using the actual observation
   time. Solo practice supports the stationary cockpit at garage exit. Run the
   read-only check against the intended visit, reviewed plan and headphone route.

   ```powershell
   .venv/Scripts/python.exe -m simcoach.cli.visit fuel-check <visit> --plan <plan.json> --audio-selection <selection.json>
   ```

   **HOLD** keeps the car in the pits. The Chief owns the reason and recheck.
   For a settling feed, continue observing without asking the driver to roll or
   repeat a still-valid dash reading. **GO** covers recording, fuel and the
   software radio path until the reported expiry. Give the handoff immediately
   when the other rig and headset checks also pass. Recheck after any delay.
   A fuel command's successful exit only queues the request.

Arm canonical recording before release; continuous raw capture does not replace
a Recorded Session. Use the [recording workflow](../README.md#record) and
preserve a separate complete artifact at the intended boundary. Verify the
current CLI help if automation wraps the stop signal.

## During and after the stint

Keep capture active through pit visits and driver breaks. Make observations
passively; do not ask for cuts or extra driving solely to fill engineering tests.
Respect the planned stint length and the driver's decision to stop.

Stop the driver clock when the driver leaves:

```powershell
uv run python -m simcoach.cli.driver_clock --stop "<clock-run>"
```

Wait for its final `complete` status. Preserve a failed/interrupted clock and
report unknown time separately. Start a new clock on return. The clock cannot
separate intentional rest from avoidable waiting or infer physical occupancy.

Finalize and validate the Recorded Session, then run deterministic analysis.
Save useful, insufficient, misleading and failed outputs with a review:

```powershell
uv run python -m simcoach.cli.driver_clock --review "<clock-run>" `
  --output "<output-file>" --verdict incomplete --note "Evidence and next action"
```

Give a short debrief: whether the practice objective was met, the evidence and
its limits, and one next step. Preserve raw captures and personal visit plans
privately. Automated checks of this procedure do not establish a new live pass,
effective coaching, audible delivery or unattended operation.
