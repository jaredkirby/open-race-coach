# Visit recovery evidence, version 1

Schema version: `1`.

Write timing: host process receipt at startup; launcher receipt immediately after
spawn; actual worker receipt from that worker's own process. Inspection reports
are returned on explicit request, with optional caller-owned publication.

Compatibility: additive version-1 diagnostic fields; unsupported versions remain unknown. Legacy identity without creation time is not upgraded from PID alone. Every enum and nullable field is defined below.

Owner: the Driver Visit coordinator writes process receipts; the read-only recovery inspector produces reports. All artifacts are private operational evidence. This additive version-1 contract does not alter Recorded Session, Analysis Run, or driver-clock artifacts.

## Process snapshot

`probe_process` returns every field below. This object is stored under required
`process` in `host-process.json`, launcher spawn receipts and each actual worker's
`worker-process.json`. Envelopes use `schema_version: 1` and UTC Unix `at` seconds.
Spawn receipts retain launcher PID and command fields. Actual workers use their
own `os.getpid()` when obtaining the process snapshot. The launcher and actual
worker may have different PIDs; neither snapshot grants ownership.

| Field | Type / units / allowed values |
| --- | --- |
| `state` | `running`, `exited`, `absent`, `unknown` |
| `pid` | positive DWORD integer; null for invalid input |
| `created_100ns` | canonical decimal string of positive unsigned 64-bit FILETIME units since 1601-01-01 UTC; null if unavailable |
| `reason` | diagnostic string or null; access denial and API failure are unknown |

No integer-to-string coercion is applied to old identity records. Legacy receipts lacking creation time remain usable as historical evidence but cannot establish identity. Unsupported versions and malformed input remain unknown. A recorded outer worker PID must agree with the nested PID. Unknown diagnostic reason strings are compatible; consumers must use structured states.

## Inspection report

`inspect_visit` returns this object and performs no writes. CLI stdout may be saved privately as an immutable new artifact; the caller owns publication timing. All fields below are required. No safe-to-restart conclusion is provided.

Artifact reads retry transient sharing/access errors with the common three-attempt,
50 ms read budget. Each attempt rechecks confinement and reads new bytes; no cached
status is substituted. An artifact may include `reads`, the diagnostic records
from `json_read.read_with_retry` (attempts, individual failures, recovered flag).
Confinement refusal remains `outside_or_redirected` even if an earlier OS read
failed. Persistent errors remain `unreadable`; invalid JSON is not retried.
Freshness is still evaluated after I/O and is not extended by retrying.

| Field | Contract |
| --- | --- |
| `schema_version` | integer `1` |
| `inspected_at`, `completed_at` | finite nonnegative UTC Unix seconds; beginning/end of sequential observations |
| `assessment` | `host_running`, `worker_running_without_verified_host`, `no_recorded_process_running`, `unknown` |
| `host`, `worker`, `worker_launcher` | identity observations; `worker` is the selected actual worker, never inferred from its launcher; missing/unselected actual identity remains unknown |
| `artifacts` | original eight keys: `host_process`, `worker_spawn`, `worker_intent`, `status`, `heartbeat`, `worker_status`, `closure_intent`, `closure`; root spawn/intent retain initial legacy provenance, while worker status/closure follow the selected attempt |
| `current_worker` | `worker`, `worker-0002`, `worker-0003`, or null for invalid pointer |
| `current_worker_selection` | `legacy_default` (pointer omitted), `selected`, `invalid`, or `unobserved` (allowed name but no observed attempt) |
| `attempts` | ordered attempt evidence objects below; initial attempt always represented, later attempts included when directory or receipt/artifact evidence exists |
| `discovery_issues` | array of `{path, reason}` diagnostic strings for invalid/unobserved selection, unknown attempt entries, redirected/unreadable discovery or bounded-discovery limit |
| `reported_visit_status`, `reported_worker_status` | strings from status evidence or null; never current process proof |
| `heartbeat` | object with `at` UTC Unix seconds or null, signed `age_s` seconds or null, and boolean `fresh` (`0 <= age_s < 3`) |
| `closure_evidence` | `result_recorded`, `result_without_intent`, `intent_without_result`, `not_recorded`, `unknown`; describes readable versioned artifacts only, not success |
| `launch_ambiguous` | boolean; any discovery issue or attempt launch ambiguity; missing actual identity is not repaired from a launcher receipt |
| `lock_ownership_verified` | always false |
| `actions_taken` | always empty array |
| `driver_departure_inferred` | always false |

Each identity observation requires `state` (`running`, `exited`, `absent`, `unknown`), `identity_status` (`matched`, `mismatch`, `unavailable`), `recorded_process` and `observed_process` (objects or null, preserving diagnostic data), `observed_at` (UTC Unix seconds after probe, or null when not probed), and `reason` (string or null). Identity mismatch means the recorded process is absent even if the observed reused PID is running. An absent PID cannot provide a matching creation time. API failures never establish absence.

Each artifact observation requires relative `path`, `state` (`present`, `missing`,
`unreadable`, `invalid`, `unsupported`, `outside_or_redirected`, `unselected`), and
`sha256` (64 lowercase hexadecimal characters or null when bytes could not be
read). `unselected` top-level worker/closure artifacts have null path/hash because
the pointer cannot select an observed attempt. `outside_or_redirected` refuses
path escape or a symlink/reparse component before reading. Present means a JSON
object with supported envelope version was read; deeper business semantics are
not validated. Hashes cover exact bytes read. The inspector never follows command
strings or session paths in these files.

## Recording attempts

Only three supported worker basenames exist: `worker`, `worker-0002`,
`worker-0003`, paired with `capture-attempts/0001`, `0002`, `0003`. Every fixed
candidate is inspected independently of the selected current pointer. Directory
discovery additionally flags unexpected attempt entries; scanning is bounded to
256 entries in each of the run root and receipt root. A limit or unreadable root
prevents a no-recorded-process-running assessment. Discovery never recurses into
unrecognized paths.

Each `attempts` object contains:

- `attempt`: integer1–3; `worker_directory`: corresponding fixed basename;
  `observed`: boolean describing whether directory/receipt/artifact evidence exists.
- `artifacts`: `intent`, `spawn`, `exit`, `worker_process`, `worker_status`,
  `closure_intent`, `closure`. Initial attempt also preserves `legacy_intent` and
  `legacy_spawn`. Known artifact observations include missing/failed states.
- `launcher`, `worker`: distinct identity observations. A launcher is read from
  per-attempt spawn, or initial legacy spawn only if the new file is missing;
  malformed new evidence does not trigger legacy fallback. Actual worker identity
  is read only from its `worker-process.json`.
- `launcher_identity_source`, `worker_identity_source`, `intent_source`: relative
  artifact locators used for interpretation. Initial legacy intent is used only
  when per-attempt intent is missing.
- `conflicting_legacy_launcher`: identity observation or null. When initial new
  and legacy launcher process snapshots differ, both are preserved and probed;
  the older receipt cannot hide a surviving recorded launcher.
- `reported_worker_status`: string or null; `closure_evidence`: same enum as the
  top-level field; `issues`: diagnostic strings for conflicting launcher receipts
  or worker-status PID versus actual recorded PID.
- `launch_ambiguous`: an observed intent lacks trustworthy recorded actual-worker
  or launcher identity, or has a receipt/status conflict. An observed exit code
  does not remove missing process identity uncertainty.

The top-level assessment checks all inspected identities, including conflicting
legacy launcher identity. A running host takes precedence; otherwise any running
actual worker or launcher yields `worker_running_without_verified_host`. Only
verified absence/exit of the host and every attempt's launcher and actual worker,
without discovery/attempt issues, yields `no_recorded_process_running`. Missing
identities yield unknown even when a saved exit/status/closure says complete.
Neither outcome establishes lock ownership or safety to restart.

Compatibility: legacy `worker-spawn.json` remains visible, but describes a
launcher. Old visits without actual `worker-process.json` intentionally no longer
derive worker exit from it. The unchanged schema version permits these additive
diagnostics; consumers must use structured identity states rather than assuming
the old top-level worker identity represented the actual recorder.

Reports are point-in-time evidence, not atomic cross-file/process snapshots. Unknown additional fields may be ignored; unsupported schema versions must not be treated as verified. Missing, failed, stale and conflicting evidence remains visible and must not trigger automatic restart, closure retry, analysis, audio replay, or inferred driver departure.
