# Standalone recorder control contract

Schema version: 1. Local private control artifacts; not Recorded Session metadata.
Owner: the controlled standalone recorder; stop intent belongs to the chief.
Write timing: identity at fresh-directory creation, status during execution and
after closure, stop intent on explicit request, closure after capture exits.
Compatibility: refuse unsupported control versions; never reuse a run directory.
Required fields are listed per artifact below. Nullable status fields are
`request_error` and `error_type`; all other status fields are required and non-null.
Timestamp units are UTC Unix seconds; counters are ticks, PID is an integer,
paths are locators, and run/request IDs are strings.
Status enum: `starting`, `recording`, `stopping`, `closing`, `complete`, `failed`,
`cancelled`. Stop action enum: `stop` only.

`run.json` is written once in a newly created directory: `schema_version`,
`run_id` (32-character UUID hex), `sim`, `created_at` (UTC Unix seconds), `pid`.
The PID is diagnostic; it does not authorize signalling or prove current ownership.

`stop.json` contains `schema_version`, matching `run_id`, `request_id`,
`action: stop`, and `at` (UTC Unix seconds). Stop writers serialize under
`request.lock` and atomically publish the request. Repetition preserves the first
valid request. A conflicting request is rejected. The owner refuses malformed,
wrong-version, wrong-identity, unsupported-action and future-dated requests.
A valid delayed request remains effective. Tokens are not access control.

`status.json` is atomically replaced by the owner, carrying `schema_version`,
`run_id`, `status`, `updated_at` (UTC Unix seconds), `pid`, `stop_requested`,
nullable `request_error`, nullable `error_type`, `ticks_observed`,
`capture_phase`, and `sessions` (exact locators reported by the recorder).
The lifecycle is starting → recording → stopping/closing → complete, failed or
cancelled. A source wait remains nonterminal and exposes the recorder phase.
Only the owner publishes a terminal state. A stop caller reports submission,
never manufactures terminal success. Status alone does not prove a process is live.

The parent `capture.lock` stays held through capture exit, validation, analysis
and terminal publication. Different parent directories and uncontrolled legacy
recorders are outside this lock domain. The directory is never reused, even
after success or owner failure; original evidence remains available.

Closure uses the existing visit worker closure format: `closure-intent.json`,
`closure-progress.json`, `closure.json`, and `debrief.md`. Each Recorded Session
has separate PASS/FAIL validation and PASS/FAIL/NOT RUN analysis. No capture is
complete merely because a stop request exists. No-data cancellation creates an
empty closure and explicitly says recording never started. A surviving closure
intent after failure needs inspection before any retry; it is not permission to
repeat analysis blindly. No Coach Refinement or audio side effects occur.
