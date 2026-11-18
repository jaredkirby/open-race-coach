# Independent visit liveness evidence

Schema version: 1. Owner: `simcoach.live.visit_watch`. Each invocation allocates
a fresh external private directory. Source visits are read-only. No previous
monitor journal, state or identity binding is resumed. Unsupported source
versions remain unknown through the existing recovery inspector.

Write timing: `run.json` once at startup; an immutable observation and journal
transition when the assessment changes; `status.json` after every observation
and at bounded completion. A final inspection runs even when a wait extends
past the requested duration. A reported observation gap does not prove sleep.

Compatibility: additive version-1 diagnostic fields; consumers must reject
unsupported versions and treat unknown states as unknown, never as permission
to act. This contract does not change Recorded Session or visit-clock schemas.
Fields described as required must be present; nullable values remain explicit.
Time units are seconds for Unix timestamps, durations and ages. Process creation
times retain the recovery contract's decimal-text 100-nanosecond FILETIME units.

## Run and status

`run.json` requires `schema_version`, absolute `visit` locator, UTC Unix
`started_at`, `duration_s` (0.1–86400), `interval_s` (0.1–10), `mode=read_only`,
`audio=disabled` and the monitor's own process snapshot (PID plus creation time,
or explicit unknown). This records an invocation, not current monitor liveness.

`status.json` requires `schema_version`, `status` (`watching`, `complete`), UTC
Unix `updated_at`, integer `observations` and `transitions`, and the latest result.
It is atomically replaced. Complete means bounded observation ended, not that
the visit completed or the driver left. Old status may remain after output or
process failure; inspect the monitor identity and freshness separately.

Result fields: `state`, nullable `reason`, nullable `pinned_host` (`pid`, decimal
text `created_100ns`), false `capture_health_verified`, false
`driver_departure_inferred`, false `audio_output_attempted`, empty `actions_taken`.
Successful inspections additionally include `host`, `worker`, `worker_launcher`
(existing identity observation objects), `heartbeat`, `reported_visit_status`,
`reported_worker_status`, `current_worker`, and boolean `launch_ambiguous`.
Inspection failures instead include controlled `error_type` and no fabricated
identity or heartbeat. Observer gaps add `observation_gap_s`.

State enum: `unknown`, `awaiting_progress`, `responsive`, `unresponsive`,
`stopped`, `binding_changed`, `closing_reported`, `terminal_reported`.
`stopped` means the pinned recorded process is absent/exited without a complete
or incomplete status; it does not diagnose why it ended. Reported terminal
status takes no position on closure validity. A changed usable host identity
latches binding_changed for the remaining monitor run.

Responsiveness requires current matched-running identity, starting/running
reported state, a fresh heartbeat (age 0–less than 3 seconds) and observed
progress. Repeated polls do not renew source age. Stale, regressed or unavailable
heartbeats revoke responsiveness. A monitor observation gap greater than
max(3s,2*interval_s) yields unknown for that observation and resets progression.
Monotonic-clock regression fails the monitor. No observation authorizes restart,
adoption, capture lock acquisition, audio takeover or inferred physical presence.

## Transition evidence

`observation-NNNNN.json` is a new immutable version-1 envelope with UTC `at`,
`result`, and nullable `inspection` containing the complete underlying recovery
inspection. Numbers increase per observed transition, not per telemetry frame.
Changes in assessment/reason, reported visit/worker states, selected worker,
launch ambiguity, inspection failure type or process state/identity-match/reason
cause a transition. Raw pages and arbitrary recorded command strings are never
executed. Inspection timestamps/hashes identify sequential, non-atomic evidence.

`events.sqlite3` uses `metadata(schema_version)` and
`events(sequence INTEGER PRIMARY KEY,payload TEXT NOT NULL)`, synchronous FULL
transactions. Every payload has `schema_version`, `event`, UTC Unix `at`.
`liveness_transition` includes `result`, evidence basename and `evidence_sha256`
covering the exact published observation bytes. `monitor_stopped` includes
`reason=duration_elapsed`, observations and transitions. Best-effort
`monitor_failed` includes `error_type`; output failure still propagates.
Evidence is published before its journal reference. A failed commit can leave
an unreferenced observation; retain it and do not overwrite/replay effects.
The terminal journal event commits before `status=complete` is published; a
failed terminal commit leaves the earlier watching snapshot, whose age and
monitor process must be checked. A committed terminal event alone does not
prove the final status publication succeeded.

The monitor has no audio authority or automatic repair effects. Real reboot,
sleep/resume, simulator performance and unattended service reliability are not
established by synthetic process tests or a completed monitor run.
