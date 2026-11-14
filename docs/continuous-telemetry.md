# Continuous AMS2 telemetry

Run the background observer for the entire rig visit, including setup, pit
stops, menus, restarts, failed laps, and time after a planned recording ends.
Its independent raw archive allows later diagnosis of fuel exhaustion and
other incidents outside a coaching capture.

```powershell
uv run python -m simcoach.cli.telemetry_watch --hz 60
```

The command prints its private run directory under `.scratch/telemetry/`.
It waits for an existing Project CARS 2 shared-memory mapping, captures the
full supported 20,700-byte page at a target 60 Hz, and reconnects when the
simulator disappears or its mapping stops updating. All exposed page fields
are retained, including fuel, controls, positions, session state and driver
names. Captured rate depends on the machine and simulator. Data outside the
supported shared-memory layout is outside this archive.

Completed segments are compressed and closed about once per minute.
`status.json` reports the heartbeat, current coverage and most recent
fuel fields. `events.jsonl` records missing/stale/unsupported telemetry and
sampling delays. The schema is documented in
[telemetry archive](../schema/telemetry_archive.md).

Fuel fields retain the simulator's raw values. Verify their units and the
loaded fuel against the cockpit before using them to calculate a return
margin. Capturing fuel provides evidence; the observer currently does not
calculate fuel-to-finish or deliver audible pit calls.

Use the existing `record` workflow to produce a canonical Recorded Session
for validation and analysis. Its stop boundary does not stop this observer.
Automatic conversion of raw archive segments into canonical Recorded
Sessions is future work.

A one-off, provenance-labeled recovery from reviewed closed segments passed
validation on 2026-11-14. It created a fresh Recorded Session and preserved
the failed original; it is not a supported importer command. See the
[live evidence summary](validation/ams2-2026-11-14.md). The same review
measured about 57.75 Hz in one segment against the 60 Hz target, not a
guarantee for other runs.

## Windows startup

`scripts/start_continuous_telemetry.ps1` launches the observer with the
workspace's Python environment and retries process failures after five
seconds. A named mutex prevents duplicate instances of that launcher.
Launcher output and exit codes stay under
`.scratch/telemetry/launcher-logs/`. The rig's scheduled task starts this
launcher when the current user signs in. The launcher does not start AMS2.
Task registration and a manual launch were verified on the test rig; a
fresh logon/reboot recovery test is still outstanding. Cloning this repo
does not install that scheduled task on another machine.

To finish the current observer gracefully:

```powershell
uv run python -m simcoach.cli.telemetry_watch --stop "<printed run directory>"
```

To keep the launcher disabled across sign-ins, create
`.scratch/telemetry/disabled`, then stop any active run with the command
above. Remove that marker and start the scheduled task to resume.

Raw pages remain private and are never uploaded or deleted automatically.
At 60 Hz the uncompressed pages alone total approximately 4.5 GB per hour;
compressed storage varies. The observer pauses sampling with an explicit
coverage gap when less than 1 GiB remains available, retaining earlier
segments. Review disk usage and archive older runs deliberately.

Power loss, sign-out, process failure, missing simulator data, unsupported
versions, and disk limits can interrupt coverage. Review heartbeat and
coverage events before claiming a continuous history. A full archive also
does not prove physical seat occupancy; the driver clock uses explicitly
bounded visits to estimate time at the rig.
