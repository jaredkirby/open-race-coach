# Offline fuel budget evidence

Schema version: 1. Owner: `simcoach.live.fuel_budget` and its CLI.
Write timing: new private output directory after bounded archive verification;
candidate only when the evidence supports review. Compatibility: additive artifact,
no change to fuel approval, archive bytes or canonical Analysis Runs. Unsupported
raw layouts/metadata are refused. No automatic retries or approval.

Required inputs: exact archive, unique closed manifested segment filenames,
UTC window, exact Context, viewed participant index (0–63). At most 16 segments,
50,000 samples, 256 MiB compressed /1 GiB decompressed total; individual segments
at most 32 MiB compressed /128 MiB decompressed, line at most 64 KiB, metadata
at most 2 MiB and elapsed work at most 60 seconds. Shared archive verification
helpers enforce confinement, manifest ordering, bytes/hash/count/start offset,
raw version/sequence/layout and original summary version/sequence. Selected
manifest entries and archive identity must still match after reading. A running
collector may append unrelated segments. No source writes or mapping access.

`report.json` required fields: archive_path, source_id (run metadata hash),
manifest_sha256 (snapshot), source_segments, context, participant_index, window,
max_gap_s, scope, integrity_result enum PASS/FAIL/NOT RUN, scope_complete,
failures, laps, diagnostics, estimate, verified_samples and samples_examined.
scope_complete means all selected segments were processed; it does not establish
coverage of every instant in the requested window. Verified samples pass raw
record checks; samples_examined also counts failed/budget-exceeded records.
local_player_identity_verified and automatic_approval are always false;
live_validation is NOT ESTABLISHED. Paths/context are private operating evidence.

Raw global car/race/pit fields must match the active viewed slot. No participant
names are emitted. Game must be active, race active or finished, outside pit.
Fuel capacity and fraction must be finite/bounded. Fuel per observed lap is the
difference between crossing samples' litres, not interpolated lap/second timing.
Counter increment must be one with progress at least .8 before and at most .2
after. First/last fragments are retained and excluded. An observed stationary
grid-to-green transition can establish the first race boundary: session5,
pre-start race1 game2/4, next active race2 game2, completed0, progress<=.1,
speed<=1m/s, same count/capacity, increasing sequence, nonnegative timestamp/offset
deltas each <=max_gap_s and fuel difference from -.001 to .05L. The last observed
pre-start sample anchors fuel; earlier grid idling is not included.

The offline estimator can also establish an observed invalidated line crossing
with unchanged raw lap counters. Active race and viewed-slot evidence must
agree on both sides. Both invalidation flags clear, the final sector rolls to
sector zero, near-line distance wraps, and the invalid timer restarts near
zero. Positive wall/offset progress, stable track length and capacity,
advancing sequence, and plausible continuous motion must corroborate the
crossing. An explicit archive break, refuel or a spatial jump prevents this
inference. This is a fuel evidence boundary, with no change to lap counters,
simulator timing, Recorded Session validity or coaching.

Clock regression, UTC/offset delta disagreement >.05s, observation or source
progress gaps >max_gap_s (default .25s), sequence regression, fuel rise >.001L
above the running minimum, participant-count/capacity change, distance regression,
unusable source rows or omitted segments invalidate the affected span. An explicit
archive continuity break closes the fuel span before the next segment, even when
its timestamps, lap counter and sequence appear coherent. Offset/UTC
ties are counted without repair: nondecreasing coarse time can support volume
differences, never strict timestamp coverage. Frozen sequences cannot extend
progress freshness. Changed fuel/lap/distance without sequence progress invalidates.

Lap rows require start_at/end_at UTC, completed_start/end integer counters,
start_litres/end_litres/consumed_litres, samples, simulator_invalidated boolean,
eligible boolean and reasons array. New rows add nullable start_boundary and
end_boundary. Values include stationary_grid_to_green,
completed_counter_increment, completed_counter_discontinuity and
observed_invalidated_crossing. A missing confirmed boundary remains null.
A counter discontinuity is retained only as an end diagnostic.
Slow and simulator-invalidated laps are not
excluded for those reasons. Every emitted fragment/failure stays visible. Durations
must be positive and consumption >.001L to qualify; flat Time Trial fuel is not
an unlimited-fuel diagnosis or a usable rate.

Estimate fields: eligible_laps, minimum_laps=3, margin_fraction (default .25,
allowed .1–1), rounding_litres (default .05, allowed .001–1), nullable
max_observed_litres, max_partial_litres and litres_per_lap. Status enum:
insufficient_data, unresolved_partial_consumption, review_required, unavailable.
A partial span whose only issues are partial_start, partial_end or
archive_continuity_break withholds the rate if its consumption exceeds the
complete-lap maximum. Otherwise at least three
eligible laps use the largest observed burn with the declared margin, rounded
up using decimal arithmetic. No trimming, averaging away high burns or claiming
a statistical upper bound. Integrity failure always withholds the rate.

CLI candidate.json wraps status=chief_review_required, versioned plan,
required_load_litres, fuel_approval=false, evidence_path and evidence_sha256 of
the exact report bytes. The plan estimate_source binds SHA256 of the report's
canonical `json.dumps(report, sort_keys=True, allow_nan=False)` UTF-8 representation.
The wrapper is not a directly usable FuelPlan. Out/in/timed/reserve allowances
follow the existing FuelPlan contract and exact session context. It does not
automatically transfer a race estimate to practice or Time Trial. Units are
litres, seconds and m/s as named; percentages are dimensionless fractions.
