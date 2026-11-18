# Select a visit and dashboard source release

The opt-in release path keeps the visit host, recorder, operations service and
dashboard on one explicitly selected source snapshot. It does not install a task,
start an ordinary visit, update a collector or configure a Mac route by preparing
files. Existing workspace commands remain development paths.

Prepare and inspect a new private candidate:

```powershell
.venv/Scripts/python.exe -B -m simcoach.cli.visit_release prepare --repo . --out "<new-private-release>"
.venv/Scripts/python.exe -B -m simcoach.cli.visit_release verify "<exact-release>"
```

The manifest pins package source, dashboard browser assets, the launch wrapper and dependency
declarations. Its identity also binds the observed Python/dependency versions.
The interpreter and installed dependencies are shared, not a sealed environment.
Captures, reviewed phrases, output selections and credentials are not included.
Never edit a selected snapshot to deploy a later change; prepare another candidate.

After review and explicit activation authorization, the Chief can invoke a role
through the hidden PowerShell 7 wrapper. These arguments are schematic: supply the
exact verified archive, fresh presence declaration and current reviewed fuel plan.
When the driver declares being seated, invoke `simcoach.cli.visit` with
`arrive --present --out <absolute visit root>` in its own launch attempt.
Use the printed arrival ID and the same root when starting the host.

```powershell
$roleArguments = @('start', '--present', '--intent', '<driver intention>',
    '--arrival', '<printed arrival ID>',
    '--archive', '<absolute exact archive>', '--plan', '<absolute reviewed plan>',
    '--out', '<absolute short visit root>', '--operations-service')
& '<selected wrapper>' -Python '<selected python.exe>' -Release '<exact release>' `
    -ReleaseId '<verified identity>' -Attempt '<new absolute launch attempt>' `
    -Module 'simcoach.cli.visit' -RoleArguments $roleArguments
```

Audio remains disabled unless its existing explicit options and reviewed assets
are supplied. The operations service starts unarmed; headset output starts muted
when selected. Capture readiness, fuel approval and physical readiness retain
their own checks. Do not use a successful launch receipt to release the driver.

The wrapper records its command and owns one hidden process handle. The actual
role creates an exclusive attempt directory, verifies identity/environment and
loaded package origins, changes to a separate writable working directory, then
records entry before calling the role. Recorder and operations children inherit
the exact release selection through explicit isolated commands and repeat
admission independently. Each role's native process metadata links its source
release and launch receipt. A post-return source check detects later changes;
it cannot undo effects that occurred before that check.

Use absolute, unredirected runtime paths outside the snapshot. Relative paths are
refused rather than reinterpreted after the working-directory change. Each control
command or dashboard invocation also uses a new launch attempt. Dashboard binding
remains loopback-only; use the exact visit/archive/service paths and a separately
reviewed encrypted private route for a Mac. No networking is enabled by the wrapper.

Refusal, interrupted entry and failed final publication are preserved. A surviving
attempt is never reused or automatically retried. Inspect the actual native
process, role receipt and application artifacts before a new attempt. Existing
capture, clock, operational and output ownership checks remain authoritative;
the source guard adds no second recorder and does not adopt an orphan.

This is integrity checking for trusted, reviewed local code, not a security sandbox
for untrusted Python or protection against an administrator changing running code.
The bootstrap, wrapper and interpreter must be selected and reviewed too. Legacy
entry points and direct Python function calls are not made impossible; only the
supported guarded path provides these receipts. A direct supported role CLI in a
marked snapshot refuses entry without admission. No SAS runtime conformance,
live simulator acceptance, audible delivery or private Mac transport follows from
source verification. Confidence depends on the scoped checks recorded with each
candidate. See the [release contract](../schema/visit_release.md).

The combined process test also exercises guarded snapshots: host, recorder,
operations service and dashboard carry the same release identity and matching
native process receipts. Generated telemetry covers garage recording, practice,
race and hotlap transitions, fresh fuel approval after plan changes, checkpoint
review and final closure. A separate case terminates only the test-owned
operations process, checks that recording continues and supervision becomes
unknown, and retains its unfinished launch receipt and incomplete visit.
The test substitutes a bootstrap to inject the synthetic adapter and crash hook;
the dashboard uses the ordinary guarded bootstrap. These checks cover source
selection and integrated lifecycle behavior, not the real AMS2 adapter, audio,
Mac transport or live arrival readiness.
