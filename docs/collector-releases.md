# Prepare a collector source snapshot

The standing launcher historically ran directly from the editable checkout.
A later process start could therefore import changes that had not been selected
for deployment. The preparation command creates a separate source snapshot that
can be checked before a controlled maintenance window:

```powershell
uv run python -m simcoach.cli.collector_release prepare --repo . --out "<new-private-release>"
uv run python -m simcoach.cli.collector_release verify "<exact-release>"
```

Preparation copies package Python files and dependency declarations, records their
hashes, and verifies that the source did not change during the copy. It excludes
captures and environment files. Existing destinations and failed copies remain
untouched. Verification checks the inventory and declared Python/package versions.
The interpreter and installed dependencies remain shared; this is source isolation,
not a self-contained installer or a guarantee against local modification.

No command above launches a collector or edits the scheduled task. Actual rollout
requires a verified idle rig, current native collector/launcher identities,
cooperative stop and artifact closure, an explicit version choice and one new
owner. A stale saved status or newest directory cannot establish current ownership.
Preserve interrupted tails and record any maintenance gap; do not repair them to
claim continuous coverage. Failure or uncertain closure is not permission to kill
or duplicate the old owner.

For an incumbent still attached to the scheduled launcher, compare two fresh
readbacks of the installed task, the writer and wrappers by native creation
time, and the exact archive header and heartbeat. Include the AMS2 AVX
executable in the process inventory. A stale or unavailable mapping can
coexist with a running simulator. Hold the switch if the simulator is present,
the source is degraded, or a relevant process cannot be identified. Confirm
driver absence and obtain maintenance approval separately.

An opt-in PowerShell 7 launch path is now available for a reviewed maintenance
window. The Chief supplies the exact prepared snapshot ID and new private attempt
path; no driver terminal work is intended:

```powershell
pwsh -NoProfile -NonInteractive -WindowStyle Hidden -File scripts/start_collector_release.ps1 `
  -Python "<selected-python.exe>" -Release "<exact-release>" `
  -ReleaseId "<reviewed-64-character-sha256>" -Attempt "<new-private-attempt>"
```

This wrapper launches Python hidden with `-I -B`, imports from the selected source
root, and leaves a command receipt plus stdout/stderr beside the attempt. The
collector verifies the selected ID/environment, rejects bytecode caches and mixed
package imports, then creates and owns the same mutex used by the legacy launcher.
Capture executes in that owning process, not in a child whose supervisor could
release ownership while capture survived. Existing mutex objects deny admission.
The attempt directory is exclusive: failures and uncertain outcomes are retained,
never retried automatically. The wrapper waits; it does not install a task or
change the standing launcher. This path is for trusted reviewed local snapshots,
not a security sandbox or protection against concurrent source modification.

`<attempt>/launch.json` binds the actual PID/creation identity, selected version,
archive parent and observed return. Inspect its exact archive paths, run metadata,
status and segment integrity. A wrapper exit or collector return alone does not
prove closure or readiness. A crashed process may leave an entering receipt and
partial segments; reconcile native identity and artifacts before a new attempt.
Legacy or directly launched collectors can bypass the new ownership protocol, so
the migration inventory/closure check remains mandatory. No automatic replacement
is attempted. The collector's run metadata also records source mode and clock.
Legacy metadata cannot prove which current file contents an old process loaded.
An idle heartbeat proves neither live telemetry nor fuel readiness.

Confidence is high in the scoped synthetic snapshot and hidden process checks,
including refusal, storage failure and abrupt actual-collector exit.
The standing launcher has not been migrated to snapshot deployment. Ordinary live,
scheduled-launcher, logon and power-loss acceptance remain separate. See the
[contract](../schema/collector_release.md) and [continuous archive](continuous-telemetry.md).
