# Race watch prototype artifacts

Schema version: 1.
Owner: `simcoach.live.supervisor` and `simcoach.cli.race_watch`.
Write timing: one SQLite transaction per observation or decision, before returning
an actionable notice. Each invocation creates a new private run; no armed fuel
state or pending speech is restored after restart.
Compatibility: reject unknown plan, input and journal schema versions. These
artifacts do not change Recorded Session or Analysis Run contracts.

## Required plan fields

`schema_version=1`, `context` (`car`, `track`, `variation`, `session_state`),
`litres_per_lap`, `timed_laps`, `reserve_laps`, `return_threshold_litres`, and
`estimate_source`. The rate must already include the chief's uncertainty margin.
Units: litres, seconds and integer lap counts. The load budget is one outlap,
the planned timed laps, one inlap and the reserve. The return threshold must
cover one full return lap plus reserve; it is not a prediction of fuel to an
exact pit position. All numeric inputs must be finite, with booleans rejected.

Optional version-1 plan fields `out_laps` and `in_laps` are integers 0–100,
defaulting to one each. Required load is `(out_laps + timed_laps + in_laps +
reserve_laps) * litres_per_lap`. Reserve still requires at least two laps, and
the return threshold still covers one full return lap plus reserve regardless
of `in_laps`. A grid start can explicitly budget zero outlaps; the engine does
not infer a formation lap, timed-race length or fuel rate. Plans remain bound
to one exact context and do not automatically follow session changes.

## Journal

`events.sqlite3` contains `metadata(schema_version)` and
`events(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)`.
Every required payload has `schema_version`, `event`, `at` (UTC Unix seconds)
and event-specific fields. Enum event values: `started`, `observation`,
`fuel_armed`, `arm_rejected`, `fuel_disarmed`, `fuel_suspended`, `fuel_recovered`,
`supervision_loss`, `notice`, `stopped`.
Observations retain only whitelisted operational fields, never raw pages or
participant names. Reasons are controlled codes, not unfiltered exception text.
Fuel/capacity/context are nullable when unsupported or unavailable. `notice`
means a local decision exists, not a Radio Transmission, playback or acknowledgment.
New fuel-return notices retain `source_at`, the archived observation's UTC Unix
seconds. It is available for radio timing diagnostics. Older notices may omit it.
Persisted observations and decisions include failures; closed runs are not reused.

## Replay input

Each JSON line requires `at` (original UTC ISO timestamp), `source_id` (explicit
archive identity) and `status` (the version-1 collector status contract).
Replay uses the historical timestamp as its virtual clock and always suppresses
audio. Reconstructed status envelopes must be labelled as reconstructed, with
original segment hashes and sample timestamps retained in private provenance.
An optional replay-only `approval` object contains `cockpit_litres` and `note`;
this is a test stimulus, not evidence of a real cockpit check.

## Evidence and expiry

Observer UTC and monotonic elapsed time must stay within 0.5 seconds of their
initial difference and of each successive interval. Small changes cannot
accumulate by resetting the baseline on every poll. A gap, regression or clock
change revokes approval and establishes a new observer baseline. Fresh advancing
readings and a new cockpit approval are still required after that reset.

Source UTC and capture offset also retain a fixed difference for each admitted
source history, with the same 0.5-second tolerance and per-interval check.
`source_clock_changed` revokes approval even when both clocks advance and the UTC
timestamp appears fresh. A new source or a rejected discontinuity establishes a
new baseline without restoring old approval. Bounded copy-contention recovery
retains the previous baseline. Fuel and recovery radio guards recheck source
clock continuity immediately before output; they cannot silently rebase it.
These are clock-consistency gates, not proof of OS sleep/resume or logon recovery.

When the archive publishes `continuity_generation`, both fuel admission and
capture admission compare it across observations. A change, including the
appearance or disappearance of the field, invalidates the previous pair.
A malformed present field is invalid evidence. Fuel approval is revoked and
a cockpit check after invalidation is required to rearm. A generation change
observed during unstable-copy recovery also requires fresh approval. The
fuel-return and monitoring-restored guards separately compare it after fresh
source I/O, including after audio-device preparation. Valid legacy sources
can still be read without this continuity evidence.

The source identity is the SHA-256 of the explicitly selected archive's immutable
`run.json`. File selection by newest directory is not supported. Version-1
collector status has no intrinsic run identity or exact latest-sample timestamp;
this prototype cannot detect every same-context reset between heartbeats or
prove that the viewed participant is the driver. Legacy solo approval is limited
to practice/test contexts (states 1/2), and when participant evidence is present
it must report one participant. Other contexts require `player_view_confirmed`
true, `solo_player_confirmed` false and an integer `participant_index` matching
the current checked slot. This is an explicit chief confirmation of player
control, not telemetry proof. All approvals retain the fresh cockpit fuel check.

New collector summaries contain `participant_observation`: version 1, slot
`index`, participant `count`, boolean `active`, slot `car`, `race_state` and
`pit_mode`. Required bounds: 1–64 participants, index 0 through count−1, active
true, slot car/race/pit equal the corresponding global fields. Missing legacy
evidence remains null; present malformed/inactive/conflicting evidence is
rejected. `Reading.participant` contains only index/count/car, never names.
Changes (including appearance/disappearance) revoke an arm. Fresh radio guards
recheck the same binding before output; recovery does not bypass it. A slot is
not stable identity across an unobserved reset/replacement. The SDK provides no
explicit local-player identifier in this binding; confirmation remains essential.

Fuel state enum: `unarmed`, `armed`, `suspended`, `return_due`. Supervision health enum: `unknown`,
`healthy`, `degraded`. Two advancing observations are required before arming.
Future/stale clocks, unavailable coverage, unexplained counter changes, gaps,
context changes and refuel/reset evidence cancel arming. A new cockpit check is
then required, with only the bounded unstable-copy exception below.
Supervision health combines capture progress and interpretable
operational fields; it is not a standalone verdict on archive integrity. The
driver clock and canonical recorder remain independent.

Normally arming requires active race state 2. A stationary race pre-start is
also admissible only for session state 5, race state 1, game state 2 or 4,
pit mode 0, finite observed speed at most 1 m/s and checked participant evidence.
Two fresh advancing readings and explicit player-view/cockpit approval still
apply. It carries the same approval into active racing; no return notice is
created before game/race states are both 2. An observed race 2→1 transition
revokes approval as `race_reset`. Paused, moving pre-start, garage/pit and terminal
states remain unsupported for arming. Status `phase` is `prestart`, `active` or
`unknown`; additive `participant` and `required_load_litres` expose the evidence
and selected budget. Finished, disqualified, retired or DNF states revoke approval.
Approval records bind the source identity and must
postdate any observed invalidation. `approval` in status is nullable before an
attempt, otherwise contains `accepted`, `reason` and `at`; invalidation revokes
an accepted approval visibly. Live observation mode is `shadow` and emits no audio.

Fuel estimates and notices expire at the source heartbeat plus the configured
age limit, never later than three seconds in the CLI. Actual audio delivery,
whole-rig readiness and uninterrupted lifecycle operation require separate evidence.

## Bounded copy-contention recovery

An existing approval can enter `suspended` only on `coverage_unstable_copy`,
under the same verified source identity, while its last admitted reading is
still younger than `max_age`, and before any return notice was issued. No fuel
estimate or notice is actionable while suspended: health is degraded, current
fuel is null and notice validation fails. The approval record remains historical
authorization; `accepted: true` alone is not active supervision.

`fuel_suspended` records `arm_id`, `started_at`, `expires_at` (UTC Unix seconds)
and `expires_elapsed` (the caller's monotonic seconds). Deadline is the earlier
of three seconds after detection or five seconds after the last admitted source
timestamp, mirrored into the monotonic clock. Polling and further copy failures
never extend it. Two advancing usable readings are required before that deadline;
identical polls do not count and another copy failure resets the pair count.
Existing source/context, capacity, race/game, counter, timestamp, lap and
cumulative minimum-fuel checks still apply. The prior reading's three-second
source-gap limit is not relaxed. Expiry or any other fault fully disarms.

`fuel_recovered` records the unchanged `arm_id`, `loss_id` and `suspended_at`.
No old return notice is replayed: instability after a return notice fully
disarms and requires fresh approval. Restart never restores suspended approval.

`supervision_loss` records `id`, `started_at`, original `arm_id`, `initial_reason`,
current `reason`, `updated_at`, and `resolution` (`recovering`, `recovered`,
`requires_approval`, `reapproved`). Fresh explicit approval adds
`replacement_arm_id`. Status exposes nullable `supervision_loss` as the latest
episode and nullable `recovery` containing the current suspension deadlines.
The radio snapshot also exposes this episode and the Supervisor's current
reason, even without a return transmission. It is durable loss/recovery evidence,
not proof of a spoken warning. Earlier episodes remain in the journal.
An optional radio loss package may use `requires_approval` and its committed
`updated_at` as an occurrence with a fixed five-second deadline. It does not
announce temporary `recovering` states or alter this supervisor state machine.

This policy cannot detect an entirely unobserved same-context reset/refuel or
same-slot participant replacement between samples. Bounded recovery retains the
approved viewed-slot/solo assumption; it does not establish identity or spectating
support. Constant Time Trial fuel is not evidence of an unlimited-fuel setting or
a consumption estimate. Native shared-memory and audible acceptance remain separate.

## Explicit plan replacement

Supervisor status adds `plan` (current FuelPlan fields without file schema version),
`revision` (integer starting at zero) and `plan_id`. The ID is SHA-256 of UTF-8
`json.dumps({"revision": revision, "plan": plan}, sort_keys=True, allow_nan=False)`.
It is a compare-and-replace token within this owner, not authentication. Original
run plan metadata is immutable; status and journal describe later plans.

`change_plan` requires a validated FuelPlan, `expected_plan_id`, `requested_at`,
`now` (UTC Unix seconds) and nonblank `note` of at most 2048 characters. Request
age must be nonnegative and below three seconds. A changed binding, identical
plan or regressed clock is refused before effects. `plan_change_rejected` records
the refusal reason. Accepted `plan_change` commits previous plan/ID/revision,
new plan/revision, request time and note before mutation. Storage failure propagates;
no success receipt or automatic retry is allowed after an uncertain effect.

Acceptance increments revision, disarms, clears notices/suspension and both source
observations, marks degraded/awaiting_plan_observations and raises the approval
floor to acceptance time. Two fresh observations in the new context and a cockpit
check strictly after that floor are required. Status exposes the new plan even
while unarmed. The result has accepted/reason/plan_id and, on success,
`fuel_approval_required=true`. No capture, presence or simulator change occurs.

## Practice pit departure

In solo practice the checked sole participant in slot zero can arm in the
active cockpit while stationary (at most 0.1 m/s) with race state 1 and pit
mode 5. The same approval can continue through motion in that exit state.
The return rule still requires active racing outside pit mode. An observed
active-to-prestart reset revokes approval.

An unarmed stationary observation with a checked participant and a known
continuity generation can preserve the basis of a recent cockpit check across
brief copy contention. Approval stays unavailable until two distinct
advancing readings pass the continuity checks. Both must arrive before the
original source reading expires, within the same game/race/pit, lap, view and
generation, at most 1 m/s, and within 0.05 L of the anchor. A new
copy failure resets the pair. A substantive fault invalidates the check.
The service can leave an intake pending during this window. The original
request and cockpit deadlines apply throughout.

Observation status includes nullable `healthy_since` in UTC Unix seconds.
A degraded observation or plan replacement clears the continuous healthy
interval. The Chief's `visit fuel-check` uses ten uninterrupted seconds as a
provisional departure margin following the intermittent Laguna feed. This
history accumulates during preparation. The service can arm before it accrues.
