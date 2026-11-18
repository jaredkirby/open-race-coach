# Reviewed session recipe and synthetic observation

Schema version: 1. Owner: `simcoach.live.session_recipe`.
Write timing: recipe/review/observation artifacts precede compilation; a journal
event commits before a command is returned. Assessment returns an offline report.
Compatibility: new opt-in library artifacts and additive coordinator events. No
historical recipe or capture is migrated; no live input backend or CLI is installed.

## Recipe and review

Recipe required keys: schema_version(integer1), id(label), scope(synthetic_only),
app(label), app_version(label), context, fuel_plan_sha256, steps. Unknown keys are
refused. Files are bounded to65536 bytes, strict UTF-8 JSON with no duplicate keys
or nonfinite numbers, and redirected filesystem paths are refused.

context required keys: car, track, mode, opponents, laps, conditions. Car/track/
conditions are bounded labels; track includes the intended layout. Mode enum:
practice, race, time_trial. Opponents is an integer0–99. Laps is nullable except
for race, otherwise integer1–999. A mode's simulator-specific restrictions are
not inferred by this contract. Conditions must name the intended context, not
claim weather recognition. Fuel_plan_sha256 is a64-character lowercase SHA256
reference supplied by the Chief; no litres are stored or selected by this module.

steps contains1–64 connected transitions. Each step requires id, from_state,
to_state, target(nullable label), requires(list of distinct context keys), and
provenance. Step IDs are unique; source and destination must differ and adjacent
steps connect. States are semantic labels, not pixel locations. No coordinates,
keystroke sequences or arbitrary executable operations are accepted.

provenance requires kind(enum observed_summary/proposed/unknown), evidence_sha256,
and note(label up to512 characters). The digest references private supporting
evidence; a digest or asserted provenance label does not prove the observation's
truth. The reviewer must assess its source. Unknown action/absent target refuses
compilation; it cannot be silently promoted to an observed selector.

Review required keys: schema_version(integer1), recipe_sha256(exact recipe bytes),
scope(synthetic_only), decision(approved), reviewer(label), note. Loading requires
an exact-byte match. The review is a trusted Chief assertion for offline rehearsal,
not an authenticated signature or approval to operate the desktop. An in-memory
recipe mutation is refused. Existing loaded bytes remain the reviewed snapshot.

## Observations and compilation

Observation artifact required keys: schema_version(integer1), scope(synthetic_only),
app_version, observation, context. The caller pins its exact byte hash. observation
uses the coordinator's typed target/focus/driving contract. Context may be partial,
but every present value must exactly match the recipe, including JSON types. Each
step additionally requires its listed fields. App/version, freshness and active
fuel-plan hash must match. No UI recognition or native observation source is
implemented here; synthetic packets cannot be used as live evidence.

Time unit is the current owner's performance-counter seconds. Observation age
must be under2s; a compiled command expires at observation time+1.5s. Native target
identity fields stay fixed across steps. Prior commands in the run must be verified
or skipped, from the same recipe and permission generation. Run IDs are32 lowercase
hex characters; command IDs derive deterministically from run ID, recipe hash and
step index. A submitted or previously compiled step cannot be compiled again.
An abandoned compile requires explicit review/new work, not an implicit retry.

Only a select command is produced. The current source or destination must be
observed. At the destination the old button may be absent; the coordinator rechecks
fresh destination evidence and marks skipped without sending input. Any later
state change instead causes refusal. Compilation grants no permission and dispatches
nothing. The trusted Chief may extend an existing gateway binding after all prior
commands are confirmed, so a recipe needs no new model turn or permission grant
for each step. Extension remains an in-process operation.

Additive journal event recipe_step_compiled contains run_id, recipe_sha256,
review_sha256, observation_sha256, fuel_plan_sha256, index(integer, zero-based),
step_id and command_id. This event is intent, not admission or effect confirmation.
Preserve command receipts and failed artifacts separately.

## Assessment report

Assessment requires final fresh full context, destination, receipts, native target
identity and the same active generation. Required gate names are devices, profiles,
haptic_output, independent_capture, canonical_intake, fuel_supervision, loaded_fuel,
radio_route, storage, maintenance_idle and driver_handoff. Missing gates stay NOT RUN.

Each supplied gate requires result(enum PASS/FAIL/NOT RUN), at, expires_at,
evidence_sha256, scope(synthetic_only), run_id, generation, fuel_plan_sha256. Times
must satisfy at<=now<expires_at and all bindings must match. Maximum evidence
lifetime is2s except profiles/radio_route(300s) and storage(30s). These are offline
policy caps, not verified live guarantees. A future live adapter must invalidate
configuration evidence when it changes and reuse accepted hearing evidence within
its scope, rather than require another listening check on every step. Stale, malformed
or mismatched gate evidence becomes NOT RUN. Evidence hashes are assertions from
the trusted fixture, not verification of a real device/capture/audio artifact.

Report required keys: schema_version, scope, recipe_sha256, run_id,
navigation(PASS/NOT RUN), gates(mapping of gate names to results),
rehearsal(PASS/FAIL/NOT RUN), live_ready(always false), live_validation(always NOT RUN).
Fresh explicit gate failure produces FAIL; incomplete evidence produces NOT RUN.
A synthetic PASS never permits whole-rig READY or claims audible delivery, loaded
fuel, hardware readiness, actual session context or physical validation.
