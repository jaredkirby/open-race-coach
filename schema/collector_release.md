# Collector source snapshot

Schema version: 1. Owner: `simcoach.live.collector_release`.
Write timing: prepare into a new private directory, verify source before/after
copy, publish manifest only after matching inventory. Compatibility: additive
collector artifact; existing workspace launches remain possible. No deployment,
scheduled-task edit, service restart or driver-presence change is performed.

`collector-release.json` required fields: kind=collector_source_snapshot,
release_id, files, prepared_at (UTC Unix seconds), python, dependencies,
deployed=false, acceptance=NOT RUN. files maps relative paths to sha256 and byte
size. Only package Python source plus pyproject.toml/uv.lock is copied. No raw
captures, credentials, environment files or arbitrary package assets are copied.
The snapshot supports the collector, not every application resource/workflow.
Limits: 2000 files, 16 MiB per file and combined copied bytes. Paths must stay
within the exact source/destination roots without redirects or parent traversal.

release_id is SHA256 of UTF-8 `json.dumps(files, sort_keys=True)`. Verification
requires exact source inventory/hashes, no unlisted files (including Python bytecode
caches), matching release ID, Python version and the recorded numpy/openai/pandas/
pyarrow/PyYAML/ulid-py versions. An altered environment is refused. This checks
versions, not every installed dependency byte or transitive distribution. The
shared interpreter/environment is not an immutable deployment or OS sandbox.

Preparation preserves partial copies and `prepare-failure.json` with error_type
and deployment=NOT RUN on failure. Existing destinations are never overwritten.
Successful verification returns required release_path/release_id, integrity enum
PASS, files count, source_bytes, deployed=false and live_acceptance=NOT RUN.
Byte counts are integer units; verification failure raises, not a PASS receipt.

New archive run.json has additive runtime fields: schema_version, source_mode enum
workspace/snapshot, nullable release verification, process (native PID/creation
identity or explicit unknown), python, executable and clock. Clock requires name
perf_counter, implementation, resolution_s, monotonic and adjustable booleans.
Runtime provenance is produced at startup; absent fields in old runs remain
unknown. Disk source versions cannot retroactively establish an old process's
loaded code. A snapshot is reverified before opening the simulator mapping.

The optional selected-release launcher requires an isolated `-I -B` interpreter,
the exact release ID, imports from that snapshot, and a fresh attempt directory
outside the snapshot. It rejects loaded `simcoach` modules from another root.
The direct legacy entry point remains available; it does not provide these new
ownership/selection guarantees. Tests use hidden child processes with synthetic
input. This does not authorize
switching the scheduled launcher, automatically upgrade an existing collector,
establish logon/reboot recovery, prove power-loss durability or validate live data.

## Explicit launch attempt

Owner: `simcoach.live.collector_launch`; schema version 1, kind `collector_launch`.
An exclusive new attempt directory contains `launch.json` and an `archive/` parent.
Required receipt fields: state, requested_release_id, release_path, started_at (UTC
Unix seconds), process (native PID/creation FILETIME or unknown), archive_parent,
target_hz, automatic_retry=false, live_acceptance=NOT RUN and nullable exit_code.
States are admitting, collector_entering, collector_returned and failed. Admission
requires verified source/environment, exact selected ID, confirmed native process
identity and a newly created, owned Windows-session mutex. Existing objects are
refused even when unowned or abandoned; the mutex is also compatible with the
legacy launcher's WaitOne exclusion. The collector runs in the owning process.
This is cooperative same-session exclusion; it cannot detect a legacy orphan
whose launcher died or a direct entry-point invocation that ignores the mutex.
Migration requires native process inventory and reconciliation before starting.

After verification `verified_release` contains the verification result; after
admission `admitted_at` is set. An observed collector return adds ended_at,
exit_code and exact archives (run.json parents) within this attempt. It is not a
live readiness or archive integrity verdict. `ownership_released=true` is written
only after confirmed mutex release. Failed receipts include error_type, error and
ended_at; failure may occur after effects, so reconcile artifacts/native identity.
Storage failure may leave no receipt, a temporary file or an older state. Process
death can leave collector_entering and an interrupted archive indefinitely.
Never infer process exit or retry permission from stale/missing status. No automatic
retry occurs, and an existing attempt directory is never reused or overwritten.

The opt-in PowerShell 7 wrapper creates a separate fresh `<attempt>.launcher/`
directory containing command.json (explicit executable/argument array and actual
process receipt locator), stdout.log, stderr.log, and exit.json or failure.json.
It starts Python hidden, waits on that handle, and records wrapper_exit_code;
the actual collector's native identity/closure remains in launch.json and archive
artifacts. Exceptions record actual_collector_state as unknown. A wrapper failure
does not kill or restart the collector. Both directories are private. File flush
and atomic replacement are not claims of power-loss durability.
