# Reviewed dashboard feedback

Schema version: 2 for cards; 1 for attempts, failures and selections.
Owner: `simcoach.live.dashboard_feedback`.
Write timing: exclusive preparation directory, attempt before validation,
immutable card or failure afterward; explicit review and selection later.
Compatibility: additive private artifacts. Recorded Sessions, Analysis Runs and
prior reviews are never rewritten. Version 1 cards omitted lap timing source and
are now unavailable for display; prepare and explicitly review a new card from
the original validated recording. Never infer missing sources or carry forward
the old review onto different bytes. Unknown versions are unavailable.

The preparer validates one finalized Recorded Session under an owned visit
worker's sessions directory without analysis or Coach Refinement. Limits:
128 KiB per metadata/lap file, 64 MiB ticks and 128 lap records. Metadata/lap
bindings and tick-file identity are rechecked after validation. Source hashes
remain private. Excessive/failed inputs retain attempt/failure evidence.

`output/dashboard/<32-hex-id>/card.json` requires schema_version,
kind=dashboard_feedback, prepared_at (UTC Unix seconds), sources, review=required,
and display. Sources contain private bindings/hashes, never the HTTP projection.
attempt.json requires version, session and at; failure.json requires version,
error_type and false review_accepted. Exclusive publication never overwrites.

Display requires recorded_session_id, context {car,track,session_type}, ended_at,
timing_basis (enum captured_time, simulator_time, mixed), scope=one_recorded_session,
laps, exclusions, statistics, timing_groups, nullable objective and null
causal_coaching_instruction. Lap fields: lap integer, lap_time_s seconds,
lap_time_source (enum derived_from_ticks, sim), valid boolean, nullable controlled
invalid_reason. Missing or unsupported sources are refused. Every lap remains.
Timing basis is recomputed from every lap's source, including exclusions.
Statistics use all valid positive times within a single source: valid_laps,
total_lap_records, best_s, median_s, spread_s and population_stddev_s. Best/median
are nullable with no valid laps; spread/stddev are nullable below two. Slow valid
laps remain included. Mixed-source cards retain total counts but null all combined
timing statistics and suppress the objective. timing_groups maps each source to
its separate statistics; no mixed-source pace or consistency estimate is emitted.
No cross-context/full-visit aggregation, simulator best, rank or cause is inferred.

Objective fields: kind=chief_selected_consistency_target, laps (3–20), spread_s
(0.05–10 seconds), timing_basis (captured_time or simulator_time). At least three
valid source laps and one consistent timing basis are required. Parameters are
an explicit chief choice, not a learned guarantee, causal Coaching Instruction
or driver-endorsed goal. Sufficiency alone does not approve display.

The existing explicit visit review must bind the exact card. Only chief reviews
support display. Verdict enum useful/insufficient permits the historical card;
insufficient suppresses its objective. Misleading/failed withholds the card.
Reviewer role is declared, not authenticated. No delivery or driver opinion is
inferred. Private notes and paths never become displayed text.

Selection retains an immutable candidate in output/dashboard-selections/<id>.json,
validates it, then atomically replaces output/dashboard-selection.json with a
unique retained temporary on failure. Required fields: schema_version, review
(private path), review_sha256 (exact chosen review bytes), at. Failed candidates
cannot replace selection. Ambiguity is reconciled from the saved selection;
there is no automatic review, newest-file scan, process or network action.

The reader bounds/confines inputs, verifies the selected review hash and exact
reviewed card binding, then reconstructs the allowlist. Source hashes describe
historical provenance; large tick files are not reread on dashboard polls.
Changed review/card bytes make selection unavailable. Later reviews remain
separate assessments; selecting a new verdict is explicit.

HTTP state enum: reviewed/withheld/unavailable. Required common fields: reason
(nullable), card (nullable). Reviewed adds review_id, reviewed_at, verdict,
evidence_mode enum live/replay, false delivery_verified and false driver_endorsed.
Historical feedback may remain while telemetry is stale, labeled with segment end
and review time. Disconnect clears it. The target appears only with fresh matching
car/track/mode; otherwise it is labeled historical. Timing units stay explicit.

Preparation does not close presence, deliver audio, choose a technique instruction
or prove Mac routing. Browser simulation, saved-data preparation and actual driver
delivery are distinct evidence categories.

## Private consistency plan and follow-up

The `simcoach.cli.dashboard_feedback` role also provides `plan-consistency` and
`compare-consistency`. They use the same bounded Recorded Session validation as
card preparation. They write separate private version-1 artifacts under
`output/consistency/<32-hex-id>/` and leave the card and HTTP schema unchanged.
Each command records `attempt.json`, then an immutable `plan.json`,
`comparison.json` or `failure.json`.

`plan.json` binds the baseline Recorded Session and source bytes, fixed
`baseline_first` and `followup_first` lap numbers, one `count` (3–20),
`spread_s` target (0.05–10 seconds), the Chief's intended `conditions` and
UTC Unix `prepared_at`. Retain its printed SHA-256 before the follow-up starts.
`compare-consistency` requires that hash and the exact baseline visit, then
revalidates both Recorded Sessions and the source binding. The follow-up must
have a different Recorded Session ID and start after `prepared_at`.

Both sides preserve all observed lap records and each fixed `window` slot.
A missing slot has `record: null`. Slots containing fragments or invalid laps
remain present and withhold the score. Windows must use the same timing source.
Recorded sim, car, track/layout, mode and validity method must match. The Chief
supplies `conditions_match` (`matched`, `different` or `unknown`) and `evidence`
from the actual visits, including the intended lap phase. These are review
declarations. They do not independently verify weather, setup, traffic or the driver.

`comparison.json` has status `compared` or `withheld` and nullable `reason`.
Withheld results retain both windows and `change: null`. Comparable results
report `change.median_s` and `change.spread_s` independently, follow-up minus
baseline, and `change.target_met` based on the follow-up spread to six decimal
places. Command exit zero means the comparison report was published, including
a withheld result. Source or plan failures preserve `failure.json`. The report
does not issue a Coaching Instruction, claim delivery, infer leaderboard rank
or replace a finalized Recorded Session or earlier report.
