# Visit source selection and process admission

Schema version: 1. Owner: `simcoach.live.visit_release`.
Write timing: prepare an exclusive source directory, then its manifest after source
rechecks; create an exclusive launch attempt and publish admission before role entry,
then observed return or failure. Compatibility: an opt-in path; existing workspace
commands retain their behavior. Older private candidate manifests are not accepted.

## Required source manifest

`visit-release.json` contains required `schema_version`, kind=`visit_source_snapshot`,
`files` (relative path to SHA256 and byte size), `environment` (Python version and
the exact dependency/version map), `release_id`, `prepared_at`, `deployment` and
`live_acceptance`. The identity hashes a sorted JSON representation of kind, files
and environment. Units: timestamps use Unix seconds; byte sizes use bytes. Unknown files,
changed or redirected paths, missing assets, identity mismatch and changed runtime
versions refuse admission. Failed preparations retain their directory and a failure
artifact. This source snapshot is not a complete installed dependency environment.

## Required launch evidence

`<attempt>/launch.json` contains required `schema_version`, kind=`visit_role_launch`,
`state`, `requested_release_id`, `release_path`, `module`, `arguments`, actual native
`process`, `started_at`, `automatic_retry=false`, nullable `exit_code` and
`live_acceptance=NOT RUN`. State enum: `admitting`, `role_entering`, `role_returned`,
`failed`. Process PID and creation identity bind the actual role, not just a venv
or PowerShell launcher. Admission requires a confirmed running native process.

`role_entering` adds verified release, admission time, selected working directory,
and loaded package-source locators. `role_returned` records the observed role code,
return time and loaded origins; it does not assert useful output, healthy capture,
hearing or whole-visit success. Failure retains exception type/detail. Unique failed
publication temporaries remain; a stale entering receipt means an uncertain outcome,
not permission to repeat effects. Initial receipt failure prevents role entry.
Only isolated no-bytecode interpreters are admitted. No imports from another
simcoach tree are accepted at the checked boundaries.

Host/worker process metadata and operations/dashboard run metadata add nullable
`source_release`. A guarded role carries `release_path`, `release_id` and
`launch_receipt`; workspace execution carries null. Child attempts are located in
`capture-attempts/<index>/source-launch` or `operations-source-launch`. Their own
receipt/native identity must match the application's metadata. A parent receipt
does not prove a child started or passed admission.

The wrapper's separate `<attempt>.launcher/` contains its command, stdout/stderr and
exit/failure evidence. Wrapper exit is not canonical artifact closure. It never
reuses a directory, restarts a failed role, force-kills another process or changes a
scheduled task. All locators, logs and arguments remain private.

## Boundaries

Allowed role modules are the visit CLI, recording worker, operations service,
dashboard and feedback CLI. The public wrapper starts the three Chief-facing CLIs;
host-owned children use the internal command builder. Input/output locators must be
absolute, unredirected and separate from the source snapshot. Commands retain their
ordinary presence, source, ownership, fuel and review gates after source admission.
Shared dependency/native-library mutation and malicious trusted bootstrap changes
are outside the integrity guarantee. No full SAS conformance or live gate is implied.
