# Stint checkpoint artifacts

Schema version: 1. Owner: `simcoach.live.stint_checkpoint`, owned by the active
recording worker. Write timing: durable request/intent before reserving a recorder
boundary; handoff before asynchronous finalization; status after each effect.
Compatibility: additive private worker artifacts; existing Recorded Session,
Analysis Run and seat-time contracts retain their identity and timing semantics.
Timing units are seconds; counts and identifiers are dimensionless.

## Request and identity

The chief's `visit checkpoint <exact-visit>` requires fresh running host status,
heartbeat, capture readiness and independently matched live host/worker process
identities. It publishes `worker/checkpoint-requests/<32-hex-id>.json`, binding
the recorded actual worker identity, never only its launcher or newest directory.
Required fields: `schema_version`, `request_id`, `action=checkpoint`, UTC Unix
`at` seconds, UTC Unix `capture_expires_at` from the selected host observation
and `worker` process receipt. A replacement worker does not adopt it. The client
checks expiry just before publication. The worker refuses the request before
reserving a boundary if its capture evidence has expired or has no deadline.
Requests older than three seconds, future, malformed, redirected, mismatched,
departing or busy are refused. A request is not completion or driver departure.

## Handoff and outputs

`worker/checkpoints/<id>/intent.json` preserves request and SHA-256. Intent or
consumed filename is never automatically replayed. The recorder handles one
reservation at an observed tick boundary, creates a successor, then transfers
the previous tick list and metadata exclusively to one background job. Capture
continues with a new buffer/session identity; original Capture Time is retained.
Successor allocation failure leaves the old buffer in the recorder and is reported.
Detached prefix finalization is never repeated automatically after uncertainty.

`handoff.json` required fields: request ID, source Recorded Session ID,
prefix/successor paths, tick count, first/last Capture Time, UTC `at` and
`rotation_s` in monotonic seconds. Rotation includes successor allocation;
it is not measured simulator coverage or a promise of zero sampling gaps.
The worker's status can briefly have zero active ticks until a successor tick.

Status enum `state`: `accepted`, `detached`, `complete`, `failed`, `refused`.
Required common fields: version, request ID, state and UTC `updated_at` seconds.
Optional fields include reason/error type, requested hash, recordings,
`elapsed_s` (background-job duration, not driver waiting time), debrief path and
review (`chief_review_required` or `unavailable`). A complete checkpoint means
validation/analysis executed; it does not require a Reportable Delta or establish
live validation, useful coaching, hearing or that the driver read the result.

The job finalizes the detached prefix, validates and analyses previously unreviewed
recordings through that prefix, and writes `closure-intent.json`, progress,
`closure.json`, canonical `debrief.md`, `consistency.json` and `stint-debrief.md`.
The usual closure records retain exact session/analysis paths, validation and
analysis PASS/FAIL/NOT RUN. The original canonical files are never rewritten.
The final worker closure reuses checkpoint results, including uncertain/failing
placeholders, rather than silently retrying an Analysis Run. Placeholders have
`error_type=checkpoint_unconfirmed`, NOT RUN validation/analysis and checkpoint ID.

New consistency.json artifacts use schema_version=2 and top-level
timing_basis=per_recorded_session; checkpoint control/status versions remain 1.
Consistency lists every lap with identity, time, lap_time_source, validity and
exclusion reason, grouped by Recorded Session. Each group has timing_basis
(captured_time, simulator_time, mixed or unknown) and timing_groups separated by
derived_from_ticks, sim or unknown. Missing/unsupported source is unknown, never
inferred from adapter or session name. Each source group uses all valid finite
positive times: best, median, full spread and population standard deviation.
Unknown groups retain counts but have no timing statistics. Combined statistics
are null for mixed/unknown basis. Nullable statistics: no best/median without a
valid timed lap, no spread/stddev with fewer than two. Counts valid_laps and the
compatibility alias valid_timed_laps remain on the Recorded Session summary;
total_lap_records retains every record. Excluded/slow laps remain visible. No
cross-context aggregation, leaderboard claim or causal Coaching Instruction is generated.
Old version 1 summaries remain historical artifacts with their old declared
captured-time basis; they are not upgraded or treated as per-lap source proof.
Choosing one objective remains explicit chief review of the deterministic output.

Worker status exposes active checkpoint ID (nullable), job-running boolean,
latest status (nullable), diagnostic errors and number of reviewed recordings.
Outcome worker-attempt evidence inventories all checkpoint manifests, including
failed ones, without counting their recordings a second time.

## Failure and ownership

Only one background job may run; a busy request is refused instead of deferred
into a later stint. Control/publication faults stay diagnostic and do not stop
intake. Failed files/temporaries remain. A successful intent followed by a crash
has an unresolved effect; restart never reconstructs a lost in-memory prefix.
The independent raw archive is separate and untouched, not a guaranteed recovery.
Failed checkpoints make worker/visit completion incomplete even when later
recordings validate. Presence, fuel approval and radio controls are unchanged.

Departure stops the seat clock through the existing protocol, stops intake, then
waits for the owned job before final closure. A hung job retains worker ownership;
parent timeout is unconfirmed closure, never permission to kill/restart. Thread,
memory, disk and simulator resource impact require separate live measurements.
