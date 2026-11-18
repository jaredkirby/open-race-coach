# Change the fuel plan during a visit

A practice, race or Time Trial switch does not end the Driver Visit. Capture and
seat-time accounting continue. The chief explicitly selects a plan for the new
intention; telemetry never chooses a fuel budget or approves it automatically.
This works with the embedded supervisor and the opt-in operations service.

Read the current visit's fresh `supervision.plan_id`, then submit:

```powershell
uv run python -m simcoach.cli.visit plan "<exact-visit>" `
  --plan "<reviewed-private-plan.json>" --expected-plan-id "<current-id>" `
  --note "Reason and evidence for the next stint"
```

The plan file follows the [fuel plan contract](../schema/race_watch.md). Its rate
and provenance must fit the actual car, track and conditions. Outlap, timed-lap,
inlap and reserve allowances are explicit. This command does not load fuel,
change the simulator or verify the intended race length or opponent count.

Inspect the request receipt and current supervisor status. With the independent
service, only its bound receipt establishes acceptance. A request older than
three seconds, a mismatched expected plan ID, identical plan or missing note is
refused. Concurrent requests cannot silently replace each other's accepted plan.
The ID includes a revision, so returning to earlier plan content does not revive
an old request. Refusal preserves the current plan and approval.

An accepted change records its intent, disarms the old approval, invalidates old
fuel calls and clears the observation baseline. It never stops capture or changes
presence. Wait for two fresh advancing observations in the new exact context,
check actual cockpit fuel, then submit the existing `visit fuel` confirmation.
The cockpit check must postdate plan acceptance and any later invalidation.
Race/Time Trial still require explicit confirmation of the viewed participant.
Preparing a plan before loading its session is allowed; supervision remains
unarmed until the context and fuel checks pass.

The service needs a fresh verified chief connection for new plans. It may continue
already approved local rules after losing that connection, but cannot accept a
new plan then. Ambiguous forwarding or receipt publication is never automatically
resent as a new effect. A storage failure is not acceptance; preserve its evidence
and inspect the owner. Startup plans remain immutable in original run receipts;
current status and the append-only supervision journal retain changes.

Confidence is high in the synthetic state-machine and Windows process checks,
including capture remaining with the same worker through explicit practice/race/
Time Trial transitions. This is not live AMS2 acceptance, a consumption estimator,
automatic strategy, simulator menu control or a complete readiness certificate.
Ordinary live transition behavior and radio delivery remain NOT RUN.
