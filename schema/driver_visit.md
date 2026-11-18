# Driver Visit coordinator artifacts

Schema version: 1.

Embedded VisitAudio snapshots also include the additive `configuration` object
described in [operations_service.md](operations_service.md): the exact loaded
output-selection hash and transport kind, separate from delivery or hearing.

Fuel requests may include `player_view_confirmed` (boolean) and
`participant_index` (integer slot) alongside the existing fields. CLI confirmation
options are mutually exclusive. Explicit viewed-slot confirmation requires its
index; legacy solo confirmation cannot include one. Embedded supervision passes
the assertion through unchanged; service mode includes it in the hashed forwarded
intent. The Supervisor owns acceptance, context/slot checks and pre-start policy.

Additive operational backend: `operations_mode` enum `embedded` or `service`.
Service mode writes `operations-plan.json`, `operations-intent.json`,
`operations-spawn.json`, private child logs and `operations-forwarded/`;
the child owns `operations/`. No embedded Supervisor/radio is constructed.
Parent `operations` contains health (`healthy`, `degraded`, `unknown`), nullable
reason/service ID, binding flags, actual process evidence, nullable launcher
exit and service state, pending receipt names and receipt/submission errors.
Initial unavailable snapshots may omit fields. Healthy means bound process,
fresh status/heartbeat and live launcher, not fuel/capture readiness. Unverified
service state yields unknown fuel state and nullable reported mute. There is no
automatic restart/adoption. See [service contract](operations_service.md).
Owner: `simcoach.live.visit_worker` and `simcoach.cli.visit`.
Write timing: new private run per invocation; worker status and parent heartbeat
are atomically replaced. Terminal closure/debrief artifacts are written after
recording stops. Existing canonical Recorded Sessions are never repaired in place.
Compatibility: reject unsupported coordinator versions. Recorded Session,
Analysis Run and Visit Clock schemas retain their independent contracts.

## Arrival before host startup

`arrive --present` exclusively publishes `arrivals/<arrival_id>/declaration.json`
under the private visit root. Fields include schema version 1, a 32-character
lowercase hex `arrival_id`, `declaration=driver_arrived`, the exact `visit_root`,
UTC `at` and system performance counter `elapsed` in seconds. It uses the same
root lock as the visit host. Under that lock, a second `arrive --present`
returns the sole coherent open receipt and its original time. The response
adds `reused` to indicate whether it opened a receipt or returned one.
An unfinished declaration with no published receipt is retained and cannot be
claimed. Multiple pending legacy receipts and an unresolved `resolution.partial`
produce explicit errors that identify the affected declarations. Live
`start --present --arrival <arrival_id>` claims the named receipt. Live
`start --present` may adopt the sole open receipt in its root. Fixture mode does
not adopt a receipt.

The host requires nonnegative UTC and counter differences of at most 7200
seconds with disagreement at most 0.5 seconds. It publishes `resolution.json`
under the arrival directory before opening the clock or starting workers.
`resolution=claimed` binds the `run` basename. The host status copies the
resolution as `arrival`. Other fields are schema version 1, arrival ID,
`arrival_sha256`, `arrived_at`, resolution UTC `at`, counter `elapsed`,
`elapsed_s` and `unknown_s`. The clock accounts for the entire prehost duration
as unknown activity. Host deadlines use the host start counter.

An unclaimed arrival can be resolved with `depart-before-host --left`.
Its resolution uses `driver_left_before_host`, null run and null durations if
the clock or age check fails. The explicit departure remains recorded in that
case. Published or partial resolutions prevent reuse. A failed or interrupted
claim is retained for inspection. Older starts can omit the receipt and have
no `arrival` field. Fixture mode refuses it. Clock agreement is a continuity
check for the local host. Physical presence and restart behavior require
separate evidence.

`abandon-before-host <arrival_id> --confirm` retires an exact unclaimed
declaration, including an uncertain partial resolution. A new
`abandonment-<id>.json` records schema version 1, the arrival ID and exact
declaration SHA-256, `disposition=abandoned`, UTC `at`, `presence=unknown`
and null `elapsed_s`. A published abandonment prevents claim or reuse of that
ID. The original and failed files remain in place. A partial abandonment
cannot retire the declaration. A published resolution owns its disposition.
None of these recovery markers establishes a driver departure.

An optional `note-before-host <arrival_id> --reason <reason>` appends
`reason-NNNN.json` in the unclaimed arrival directory under the same root lock.
Reason values match the [clock contract](visit_clock.md). Numbering starts at 1
and is capped at 64 per arrival. Each record contains schema version 1, exact
arrival ID, sequence, reason, UTC `at`, counter `elapsed`, and
`previous_sha256` over the exact preceding note bytes or the original declaration
bytes. The clock pair must remain coherent from both the previous note and arrival.
Published notes and incomplete publication files remain available for inspection.

If notes are present, resolution adds `declared_intent`. Its `state` is
`verified` when the source chain and clock bounds agree. It includes `note_count`,
`head_sha256` over the last note and `reason_s` with all five reason keys. Notes
apply prospectively until replaced or until resolution. Time before the first
note belongs to `unspecified`. These declared totals sum to the pre-host duration
and overlap that entire period of unknown activity. They do not affect activity
totals or carry a reason into host observations. If note publication, chain
validation or timing fails, the intent is `state=unavailable` with no totals.
Missing notes leave the field absent. Annotation faults leave the primary arrival
duration and coverage envelope available when their own receipts verify.

## Bounded capture attempts

New live hosts sample free space at the visit root. Each worker separately
samples its output directory, or its parent before the directory exists.
`recording_storage` in host and worker status contains schema version 1,
private `path`, Unix `at`, performance-counter `elapsed`,
`minimum_free_bytes`, nullable `free_bytes`, `state` (available, low, unknown),
nullable `reason` and nullable `expires_at`. It may include a probe or journal
error class. The local floor is 1 GiB. Reads are normally one second apart,
expire three seconds from query start and require coherent UTC and monotonic
ages. A cache hit does not renew the observation. Fixture mode omits live
measurements.

Live worker starts and replacements require a fresh available host sample
alongside existing source and ownership checks. The launch intent records
that sample. A blocked launch spends no attempt and publishes
`capture_storage_unavailable` in recovery. Before the first worker, host
phase is `waiting_for_capture_storage`. A worker checks its own destination
before opening intake. Its observed low reserve during capture requests a
cooperative stop, closes the prefix via the ordinary finalizer when possible,
and reports `recording_disk_reserve` with a failed attempt. A read failure
during intake leaves activity running and makes capture readiness unknown.
New checkpoint handoffs are refused while storage is unverified. Existing
accepted checkpoint effects finish through their existing paths.

Capture readiness uses the earliest usable expiry across the source, worker
tick and both storage observations. Recorded host storage transitions are
appended to the host supervision journal. Fuel authority, independent raw
capture and presence are governed by their own evidence and controls.
Free-space observations reserve no bytes, and successful prefix closure
depends on remaining space.

Capture source admission requires fresh advancing samples with coherent clocks.
It compares both successive intervals and fixed UTC-minus-monotonic baselines
for the observer and the source's UTC-minus-offset baseline, with a 0.5-second
tolerance. Accumulated drift cannot slide the baseline indefinitely. A clock
fault reports degraded/not ready with no expiry. A changed source-clock baseline
requires a coherent advancing pair before reacquisition; restoring the old offset
is another discontinuity. Invalid source data clears the source baseline, and
invalid observer time clears the observer baseline. These are transient checks,
not new durable fields or proof of sleep/resume behavior. Reacquiring capture does
not approve fuel, unmute radio or establish whole-rig readiness.

New hosts retain the initial `worker` and use fresh sibling `worker-0002` and
`worker-0003` directories for replacements, at most three starts per visit.
Each worker writes `worker-process.json` with `schema_version=1`, UTC Unix `at`
and its actual `process` snapshot (PID, state, creation FILETIME text, reason).
The launcher may have a different PID. Missing or unsupported identity evidence
cannot establish actual worker exit.

`capture-attempts/NNNN/intent.json` includes schema version, UTC `at`, integer
`attempt`, `worker_directory` basename, `command`, `mode` and nullable
`recovery_basis`. Replacement basis records `prior_attempt`, `launcher_exit_code`,
`actual_worker` identity observation, `capture_lock_available`, UTC
`source_admitted_at`, and monotonic `cooldown_elapsed_s`. This records observations,
not a lock reservation. Every child acquires the shared capture lock before
opening its adapter. `spawn.json` adds launcher `pid` and `process` snapshot.
The initial legacy `worker-intent.json` and `worker-spawn.json` remain preserved.
An intent without a trustworthy spawn/worker identity is ambiguous, not permission
to repeat the launch. Receipts are atomically published once per effect.

`exit.json` records schema version, UTC `at`, `attempt`, `worker_directory` and
launcher `exit_code`. Actual worker termination is verified separately before
replacement. `capture-ready.json` records the first fresh combined capture
readiness observation: schema version, UTC `at`, `attempt`, `worker_directory`,
`launch_to_ready_s`, nullable `observed_exit_to_ready_s` and `basis`. Durations
use the host's monotonic observations; exit-to-ready starts when exit was observed,
not when the source was last captured. Neither is full coverage or driver delay.

Host `current_worker` selects one attempt for current readiness and singular
worker fields. `recovery` includes schema version, `state`, `max_attempts=3`,
`current_worker`, `attempts`, nullable `worker_exit_observation`,
`capture_interrupted`, and `prior_attempts_preserved`. Attempt summaries include
integer `attempt`, basename `worker_directory`, nullable `exit_code`, UTC
`started_at`, nullable `exited_at` and nullable `first_ready_at`.
State enum: `awaiting_source`, `starting`, `worker_running`, `departure_requested`,
`launch_uncertain`, `recovery_disabled`, `attempt_limit`, `recovery_cooldown`,
`worker_exit_unconfirmed`, `capture_lock_unavailable`.

An initial start also checks the shared capture lock after source admission.
If the lock cannot be acquired, the host reports `waiting_for_capture_lock`
and continues the same presence clock. No launch intent or worker attempt is
created for that wait. The next poll rechecks current source readiness and
the lock. The worker still acquires the lock itself before adapter access.

Automatic replacement is live-host only, after observed launcher termination,
at least two monotonic seconds, proven absence/exit of the actual worker,
fresh observed-context admission and a free parent capture lock. It cannot restart
presence, extend the visit deadline, arm/unmute radio, retry previous closure or
overwrite any attempt. An unexpected exit latches `capture_interrupted`; terminal
visit status stays incomplete even after successful replacement. Synthetic fixtures
end once and do not automatically retry their deliberately finite workers.

Legacy runs without attempts/current-worker fields retain the single-worker
interpretation. Outcome and recovery tools inventory every attempt independently
of the current pointer; a later success must not hide earlier failure or unknown
effects. This contract does not establish machine-wide process ownership.

## Worker and host status

Required worker `status.json`: `schema_version`, `status`, `updated_at`, `pid`,
`ticks_observed`, `sessions`, `stop_reason`, `error_type`. Time units: UTC Unix
seconds. Sessions are explicit paths created by this worker, never selected by
newest directory. `stop_reason` and `error_type` are nullable. Status enum:
`starting`, `recording`, `closing`, `complete`, `failed`. Recording readiness
requires observed ticks, a fresh worker heartbeat, and independent source
checks; existence of a process or directory alone is insufficient.
`active_ticks`, `intake_active` and nullable `tick_age_s` distinguish current
intake from boundary/terminal finalization; old tick totals cannot establish it.
`tick_age_s` is the age at the worker observation. Its age continues to increase
after `updated_at`; a recent publication cannot extend the tick's three-second
validity window.

Optional `publication_diagnostic` contains cumulative integer `failures` and
nullable `last_failure`: UTC `at`, `error_type`, nullable `errno`/`winerror`,
temporary basename, original `observation_at` and attempted worker `status`.
These are historical diagnostics, not current health. Status I/O failures retain
unique `worker-status-*.tmp` files when created and append best-effort
`status-publication-errors.jsonl`. Capture and canonical closure continue; old
status timestamps never refresh on failure. Terminal publication failure yields
nonzero worker exit. Canonical closure failures are not suppressed.

Optional nullable `lease_diagnostic` retains the latest problematic or retried
lease observation, not current health. The worker also appends observations to
`lease-diagnostics.jsonl` and publishes `lease-diagnostic.json`. Fields are
`schema_version=1`, UTC Unix `at`, boolean `valid`, nullable `reason`, nullable
`age_s`, nullable `error_type` and `reads`. Each read diagnostic has
`schema_version=1`, `event=file_read`, `role=coordinator_heartbeat`, UTC Unix `at`,
integer `attempts`, boolean `recovered`, and `failures` containing integer
`attempt`, `error_type`, nullable `errno` and nullable `winerror`. Optional
`publication_error` records diagnostic I/O failure in worker status. Original
producer timestamps and lease duration are never extended by reader retries.
Stop reasons distinguish `coordinator_lease_expired`,
`coordinator_lease_read_unavailable`, `coordinator_future_lease` and
`coordinator_invalid_lease`; initial failure remains
`coordinator_lease_unavailable`. Old artifacts remain valid and unchanged.
Parent `recorder_intake_ready` reports only that worker evidence; `capture_ready`
also requires fresh advancing evidence from the selected archive. The nullable
UTC Unix `capture_expires_at` is the earlier of the recorder tick deadline and
the source deadline. The host checks both after its worker/request reads and
withholds readiness if wall and monotonic elapsed time disagree by more than
0.5 seconds since observation. Readers recheck the deadline after reading.
Older host statuses without a deadline remain historical observations. Neither
ready flag certifies a whole rig or audible communication. Empty, malformed or
failed closure evidence cannot complete a visit even when a child returns exit zero.
Fuel approval is separate from capture admission. Parent `source` holds the
CaptureGate snapshot (`health`, nullable `reason`, `ready`, nullable `source_id`
and nullable UTC Unix `expires_at`). `phase` is `waiting_for_source`,
`waiting_for_capture_lock`, `starting_capture`, `recording`, `capture_closing`, `capture_closed` or
`capture_failed`. A missing identity cannot be replaced by a usable sentinel.
`first_tick_timeout` is a failed worker outcome independently of artifact closure;
valid artifacts can still be preserved and analyzed after that failure.

New source snapshots add nullable `observed_context` (strings `car`, `track`,
`variation`, integer `session_state` 1–6) and nullable boolean
`expected_context_match`. Legacy snapshots without these fields do not establish
a match. Two fresh advancing observations in the same supported context admit
capture independently of the fuel plan, race state or motion. Game states
1, 2, 3, 4, 6 and 7 are supported; fresh menu/replay telemetry is retained with
its normalized Vehicle State. A context change resets admission until the next
advancing observation, including changes away from the original fuel plan.
Unsupported/missing context and stale source still deny admission. These fields
do not approve fuel, prove current worker context alignment or certify readiness
to drive. Existing worker and artifact checks remain required.

New worker status also reports `continuous_source_gaps` (boolean), `capture_phase`
(`starting`, `recording`, `waiting_for_source`, `finalizing`, `closed`, `failed`),
nullable `source_gap_reason`, and nonnegative integer `source_gap_count`. Legacy
statuses without these fields provide no source-gap evidence. The count is sticky
within one recorder invocation and counts detected grace-expired source gaps, not
lost samples, elapsed gap duration or complete coverage. A successful closure does
not erase it. Waiting retains the same worker and lock with no active ticks or
fresh tick age. Parent phase returns to `waiting_for_source` during that wait.
Live workers enable this policy; finite synthetic fixture workers do not. Artifact
or adapter exceptions remain terminal failures rather than automatic retries.

`heartbeat.json` requires `schema_version=1`, `at` in UTC Unix seconds. The
worker accepts a coordinator lease for at most five seconds. Missing, corrupt,
future or expired lease requests cooperative recording stop. The coordinator
and worker use separate ownership locks under one common private root. No
retry automatically spawns a replacement worker, and no worker stops the
independent continuous collector.

`closure-intent.json` is a one-time effect record written before automatic
validation/analysis. Its presence without `closure.json` requires reconciliation;
analysis must not be blindly repeated after a crash. Required terminal closure
fields: `schema_version`, `recordings`, `closed_at`. Each recording records
`path`, `validation` (PASS/FAIL), `analysis` (PASS/FAIL/NOT RUN), nullable
`summary`, nullable `analysis_path` and nullable `error_type`. Complete
insufficient-data analysis is preserved; failed or incomplete capture skips
analysis and remains on disk. An empty list means recording never started.
`unregistered_artifacts` inventories additional files/directories inside the
worker-owned sessions root without analyzing them or assigning a canonical ID.

`debrief.md` is a visit-level index of the exact canonical outputs, not another
Coaching Instruction source. `failure.txt` retains private diagnostics when
worker recording fails. Replay input is an explicit synthetic JSON object with
`info` (SessionInfo fields) and `ticks` (NormalizedTick objects); output provenance
must state replay/test mode. Replay never opens a simulator mapping or audio.

Parent visit status includes `mode` (`live`/`replay`), lifecycle status, explicit
presence confirmation, current activity/reason clock status, worker status and
stop outcome. Replay presence is a test stimulus, not real measured seat time.
New status projections distinguish arrival_confirmed=true (the original accepted
arrival declaration) from presence (declared_present, declared_absent, unknown)
and declared_presence (true, false or null respectively). All three accompany the
same detached clock snapshot during startup, running, closing and terminal output.
An explicit processed departure becomes false even if later artifact closure
fails; interruption/timeout becomes unknown, not continued presence or inferred
departure. Clock publication_complete/error and visit status remain separate:
accepted departure intent does not prove successful publication or full closure.
Older outputs may retain declared_presence=true after termination; preserve them
and consult their clock/departure evidence instead of treating the old flag as
current occupancy. These are declarations, never sensor proof of seat occupancy.
New host status includes `activity_source`, the current ActivityEvidence snapshot
described in the [clock contract](visit_clock.md). Its advancing/held/unknown
diagnosis is independent of capture admission and fuel approval. It is a reported
endpoint with original expiry, not measured occupancy or whole-interval coverage.
Ending presence stops the visit clock immediately; recording finalization and
analysis may continue afterward. A timeout is not proof the child terminated;
retain its PID/status and require inspection before another capture.
The first `stop` request includes UTC `at`, shared local performance-counter
`elapsed`, and `declaration=driver_left`. That boundary must lie between the
clock's last observation and current time. Parent state becomes `closing` before
waiting for the child; no new control requests are accepted after departure.

Host clock advances and departure publication share `request.lock`. A contended
observation is skipped; a published stop prevents further clock advance. This
also covers departure during source I/O. The command-line stop waits at most one
second for that lock and rechecks lifecycle status before choosing its boundary.
The controls client retains its original intent timestamp but chooses the
accepted departure boundary under the lock; its result adds `boundary_at` and
`boundary_elapsed`. These acceptance timestamps do not replace the earlier
intent audit or prove when the driver physically left. Existing failed clock
artifacts are not rewritten.

Optional parent `radio` is a fuel-radio snapshot accompanied by the parent's
observation time. `audio` declares configured mode, not confirmed sound; audio
is disabled by default and explicit enabling starts muted. `ready_to_drive`
remains false until whole-rig acceptance is implemented and evidenced.

New radio snapshots add boolean `loss_warning_configured`, nullable
`loss_transmission_id` and `loss_delivery_state` (`none` or a dispatcher state).
These describe the optional monitoring-loss phrase in the same radio journal;
the existing transmission/delivery fields continue to describe fuel-return calls.
The latest `supervision_loss` is the supervisor's historical episode, not hearing
confirmation. Loss delivery failures contribute to degraded delivery health while
the loss remains unresolved and audio is unmuted. Missing legacy fields do not
establish configuration or delivery. The optional `--loss-phrase` requires audio
opt-in; it never enables playback by itself.

Optional recovery package snapshots add `recovery_warning_configured` (boolean),
nullable `recovery_transmission_id` and `recovery_delivery_state` (`none` or a
dispatcher state). Recovery occurrence identity binds loss ID and restored arm
ID, with a fixed five-second expiry from the committed resolution. It uses the
same journal/output owner at priority 80. Eligibility requires a matching prior
played/acknowledged loss record or current-owner transport start confirmation,
then healthy armed fuel supervision and independent fresh source checks.
Transient start confirmation is not reconstructed after restart and does not
prove sound/hearing. Interrupted/suppressed/failed/expired recovery output
degrades delivery health while its restored arm remains current and unmuted.
`--recovery-phrase` requires `--loss-phrase` and explicit audio enablement.

The priority `mute.json` requires `schema_version=1`, a 32-character lowercase
hex `command_id`, boolean `muted` and finite UTC Unix `at`. The client also
emits `intent_watermark`, the newest intent timestamp retained across writes;
legacy requests without it use `at`. The client writes
it under `request.lock`; it is not also queued. Only a fresh explicit Unmute in
an active visit can be applied. Malformed or stale controls cannot restore audio.
Each coordinator acknowledgment at `receipts/<command_id>.json` records
`schema_version`, `at`, `accepted`, `applied`, `muted`, nullable `reason` and
nullable `audio_heard` (unknown here). Applied mute state is not evidence of
hearing or measured acoustic silence. Private client `controls/*.intent.json`
and `*.result.json` retain submission/refusal/write failures separately. See
[local controls](../docs/visit-controls.md) for the client behavior.

Mute acknowledgments additionally bind `command_id` and `command_fingerprint`
(SHA-256 of the canonical request). Historical receipts are immutable. Malformed
or conflicting requests use separate `receipts/rejected-<first24-of-fingerprint>.json`
artifacts; the full fingerprint remains inside, and their nullable command ID
is included only when interpretable. The
rejection fingerprint hashes raw bytes when parsing is impossible. An older
receipt cannot establish the current radio state after later controls or faults.

New invocations publish `host-process.json` before capture and add a `process`
snapshot to `worker-spawn.json`. The [recovery contract](visit_recovery.md)
defines PID plus creation-time identity and read-only inspection. Old status,
a process number or a lock file alone cannot establish a current owner. Diagnostic
probe failure remains unknown and does not prevent independent capture.

After final parent status and owned journal closure, the host writes a new
[visit outcome](visit_outcome.md). Its separate `outcome-result.json` receipt
requires `schema_version=1`, `at` (UTC Unix seconds), `status` (`written` or
`failed`), nullable `report_path`, nullable `report_sha256` and nullable
`error_type`. The hash covers the exact published report bytes. Failure retains
partial reports and `outcome-failure.txt` and makes the invocation exit nonzero.
Parent terminal status is not rewritten to attach the locator: it is a hashed
report input and describes capture/clock closure, not successful outcome delivery
or chief review. Inspect the outcome receipt as well as that status.

The receipt's additive `software_timing` reports `scope=same_visit_host_counter`,
`write_and_hash_s` and nullable `after_declared_departure_s`. Counters are
sampled before the outcome write and after both report files are published and
the JSON hash is read back. The latter duration requires a successfully closed
live clock and a coherent explicit departure counter. Failed writes and
discontinuous host counters leave timing null. Fixture visits leave the
departure duration null. Driver delivery and usefulness require other evidence.

Worker status additionally exposes `checkpoints`: active ID, job-running flag,
latest nullable receipt, diagnostic errors and reviewed-recording count. The
[stint checkpoint contract](stint_checkpoint.md) defines requests, handoffs and
one background job without presence/fuel changes. Final closure reuses prior
checkpoint rows, including uncertain NOT RUN placeholders, without repeating
Analysis Runs. Checkpoint failures make closure incomplete even if subsequent
recordings validate. A hung job retains worker ownership until it finishes.

`requests/<id>.json` also accepts `action=plan` with UTC `at`, a versioned FuelPlan,
`expected_plan_id` and `note`. The embedded owner applies the
[plan replacement contract](race_watch.md#explicit-plan-replacement) and receipts
include accepted/reason/current plan ID. The service backend forwards identical
intent under its own instance binding; only its matching receipt establishes
acceptance. Current supervision exposes plan/revision/ID. A changed plan updates
the capture gate's expected-context diagnostic, not capture admission or presence.
The original visit plan in launch metadata is never overwritten.

## Fuel handoff query

`visit fuel-check` is read-only. It returns `decision`, `reason` and an
absolute UTC Unix `expires_at`. GO exits 0 and also includes observed
`fuel_litres`, `required_litres` and `return_litres`. HOLD exits 2 with a
reason and null expiry. A historical or unsupported owner yields HOLD.

The Chief supplies the intended plan and audio selection. GO requires fresh
recording intake, bound live host, worker and operations processes, current
presence, active Chief link, accepted fuel supervision, a settled fuel feed,
and an exact match to the current archive reading. The supported pit handoff
requires a checked participant, game state 2, speed at most 0.1 m/s and pit
mode 2 or 5. The observed load must still cover the plan. An applied unmute,
selected held output and configured monitoring-loss warning are required.
Expiry uses the earliest component deadline. The result covers the software
path; physical hearing and whole-rig release remain Chief checks.
