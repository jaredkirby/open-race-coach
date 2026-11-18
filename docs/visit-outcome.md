# Offline visit outcome

The outcome report brings together the visit's existing evidence, including failed
capture, insufficient analysis, failed or suppressed radio output and unknown time.
It does not rerun analysis, alter canonical artifacts or produce another Coaching
Instruction. It retains every inventoried attempt rather than selecting the best
lap or report to make consistency look better.

Reported worker source-gap diagnostics remain in `worker_capture`, including the
number of grace-expired interruptions even after capture resumes. This is not a
whole-visit coverage measure. Legacy missing values stay unknown; no zero-gap
claim is inferred from successful artifact closure.

Recovery snapshots retain every worker attempt in `worker_attempts`: the initial
`worker` and owned sibling directories such as `worker-0002`. Receipt-only launch
attempts remain visible even if no worker directory was created. Each entry keeps
its launch/exit and worker-process receipts, closure evidence, reported failure,
and separate closure timing. A closure intent without a result remains unresolved;
the reporter never retries its analysis. All recording rows identify their worker
attempt. Later success does not erase earlier failure or missing evidence.

Each attempt also retains `capture-ready.json` when present. Its launch-to-ready
and observed-exit-to-ready durations are host monotonic observations, not lost
capture duration, driver waiting time or proof of complete coverage. The reporter
preserves this receipt as reported evidence without recalculating or aggregating
its timings. Missing readiness receipts remain unknown.

The singular `worker_status`, `worker_capture`, and `closure_timing` fields describe
only the host's explicit `current_worker`; legacy hosts without that field select
the initial `worker`. An invalid current pointer is unknown, never inferred from
the newest directory. Timings from different attempts are not summed or treated as
driver waiting. The one visit clock remains the source for declared presence time.

The host calls `write_visit_outcome(run)` after writing terminal visit status and
closing its clock, recording and radio journals. The function returns the new
`report_path` and parsed report. Each invocation uses a fresh short private directory
under the visit's `outcomes/`. An optional explicit output directory must be new.
Destinations inside the worker-owned recordings tree are refused, including new
subdirectories within a Recorded Session or Analysis Run.
The report binds source paths and hashes; leave those source artifacts unchanged.
The host's separate `outcome-result.json` distinguishes successful report publication
from an error, even when the original capture/clock status is complete. Its
software timing measures report writing and hash readback, including time since
declared departure when the live host closed the clock successfully.

Read `report.json` for activity totals, the reasons assigned to non-driving time,
each recording and Analysis Run, closure timing, radio outcomes and provenance.
`report.md` includes the lap timing debrief. It reports each finalized Recorded
Session with matching validation evidence, including valid and total lap counts,
best and median times, full valid-lap spread where available, and exclusions.
Captured and simulator timing have individual statistics. Slow Valid Laps
remain in the spread. The report retains timing failures and all available lap
records in JSON. Leaderboard claims and a consistency objective still require
Chief review and scoped evidence.

Reason totals partition time classed as non-driving. Intentional rest has its own
reason. Known totals require a matched terminal clock. Interrupted visits leave
presence unknown. Fixture visits report synthetic time.
Known clock totals mean the reported ledger partitions are available and consistent;
they do not prove exact activity. The archive-backed host may use explicitly bounded
held activity estimates between status publications. Per-interval source diagnosis
and expiry remain in the clock journal; source/context discontinuities count unknown.

When notes were kept before launch, the outcome rechecks the exact arrival and
note receipts and reports declared pre-host reasons alongside unknown pre-host
activity. It displays time declared as waiting for the Chief separately from
observed non-driving time tagged by the host. An incomplete or changed note
chain withholds pre-host attribution. The other elapsed and activity totals
remain usable within their evidence scope.

Closure timing is computed only from supported timestamps and labeled UTC-derived.
It is not the time the driver waited. Played audio means transport completion;
acknowledgment is separate, and neither is a measurement of audible output.
Failed, expired, suppressed and interrupted transmissions remain visible. Corrupt
or changing sources and ongoing visits produce explicit unknowns and prefix evidence,
not fabricated final counts. Missing radio evidence does not mean zero calls.
The outcome reports available software timings per call, including source age,
dispatch queue, time from output intent to transport call, and transport
confirmation. Hardware onset and hearing need separate evidence.

For a terminal visit, the fuel radio audit compares committed return decisions
with matching dispatcher records. It distinguishes confirmation before the
software deadline, confirmation at or after it, expired calls, mute, other
delivery outcomes and decisions with no dispatcher record. An unavailable
dispatcher journal leaves delivery unresolved. Disabled audio is reported
separately. The audit also counts fuel transmissions without a matching
decision when both journals are available. It reads a bounded in-memory copy of
the existing supervision journal and leaves both input files untouched.

The reporter does not infer capture coverage, false calls, missed calls, acoustic
delivery or time to a useful debrief. These require other instrumentation. Existing
canonical reports remain linked by locator and hash, including poor or insufficient
results; they are never rewritten. Only files inside each worker-owned sessions root
are followed when inventorying recording and analysis attempts.

This component is exercised with synthetic offline artifacts. It does not start
the simulator, record, play audio or establish live acceptance. Keep reports private:
source locators, operating results and error details can identify the rig or driver.
See the [outcome schema](../schema/visit_outcome.md) for field and certainty rules.

After a chief review, explicit driver feedback or a bounded archive audit, the
chief can write a new snapshot without replacing the original outcome:

```powershell
uv run python -m simcoach.cli.visit outcome "<visit>"
```

The new report inventories all [output-review attempts](visit-review.md) and
[archive coverage reports](archive-coverage.md) stored under the selected visit.
It does not choose the most favorable review or silently widen an audit window.
Attachments keep their own scope, identities and uncertainty; the reporter does
not re-audit raw archives or turn a chief opinion into driver confirmation.
