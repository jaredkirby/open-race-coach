# Prepare a fuel budget from saved evidence

The chief can inspect a bounded selection of private raw archive segments to
estimate fuel per lap before a visit. The command reads saved bytes, verifies
their hashes and produces a reviewable candidate. It never loads fuel, arms the
supervisor or changes the simulator.

```powershell
uv run python -m simcoach.cli.fuel_budget --archive "<archive>" `
  --segment samples-000001.jsonl.gz --segment samples-000002.jsonl.gz `
  --start-at "<UTC ISO timestamp>" --end-at "<UTC ISO timestamp>" `
  --context "<exact-context.json>" --participant-index 0 `
  --timed-laps 5 --out-laps 0 --in-laps 1 --reserve-laps 2 --out "<new-review>"
```

Context is a JSON object with car, track, variation and session_state. The selected
slot is an evidence filter, not authenticated proof of the player. Inspect its
provenance alongside the intended conditions before relying on the result.

The report retains all observed lap spans and exclusions. Slow or invalid laps
still contribute fuel evidence when continuity is supported. A stationary
grid-to-green transition can include the higher-consumption standing-start lap.
Unexplained high-consumption fragments block a candidate instead of disappearing
from the calculation. A collector-marked break prevents fuel spans from
crossing segment boundaries. Refuelling, resets, gaps and untrustworthy fields
remain explicit. Constant fuel does not yield a consumption estimate.
The reviewer can see an observed invalidated crossing when the lap counters
stay still and continuous motion, distance, sector, flags and timer all agree.
It can separate fuel spans and anchor the next full lap. It gives no simulator
lap time and never makes an incomplete fragment eligible.

With at least three eligible laps, the default policy takes the maximum observed
burn, adds 25% and rounds up to .05L/lap. This is a declared engineering margin,
not a calibrated confidence bound or a guarantee about future conditions. The
chief must review weather, setup, assists/fuel settings, pace, start procedure and
the small evidence sample. Earlier grid idling, formation/warmup laps and longer
running need explicit allowances. Do not silently count them as zero.

Candidate output separates outlaps, timed laps, inlap and reserve and retains the
existing return budget. It uses a wrapper that must be deliberately reviewed before
extracting the plan. Actual cockpit fuel must meet that plan and any simulator
loading increment must be rounded upward. A changed intention needs an explicit
[plan replacement](fuel-plan-transitions.md) and fresh fuel confirmation.

Coarse historical offset ties remain visible. They may support ordered volume
differences but do not establish strict timing coverage. No archive is repaired.
Reports are private and preserve source hashes; none are automatically published.
Confidence is high in the synthetic checks and scoped saved-byte arithmetic;
confidence in a future consumption prediction is moderate at best. Live use and
new audible warnings remain separate acceptance gates. See the
[artifact contract](../schema/fuel_budget.md) for limits and failure semantics.
