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

New collectors use `time.perf_counter()` for elapsed observation time and sample
pacing. Some Windows Python versions expose a coarser `time.monotonic()` clock
that can assign the same offset to successive observations after a slow copy.
The archive retains actual offsets and raw pages; it never adds artificial time
increments. This correction has synthetic regression coverage. Existing running
collectors and historical archives are unchanged; live verification remains open.

[Collector source snapshots](collector-releases.md) prepare an explicit version
for reviewed rollout. Preparation alone changes no running process or launcher.
New startup metadata records native identity and clock/source provenance;
old archives do not gain that evidence from later source edits.

Completed segments are compressed and closed about once per minute.

The collector also closes the valid prefix when the mapping disappears,
becomes stale, reports an unsupported layout, or sampling pauses for storage.
Two seconds without an accepted sample also separates the next segment,
including sustained unstable copies. Brief unstable copies can remain in the
same segment. A closed prefix stays available even if the collector never
receives another page. The coverage audit leaves the marked gap uncredited.
The Windows reader retries a contested copy once after a short yield. Each
attempt checks the sequence before, within and after the copied page. If both
fail, coverage becomes unstable. The first subsequently accepted page is
submitted to the status publisher immediately with its own timestamp.

A sustained unstable copy also releases the mapped view. This matters when a
publisher exits with an odd sequence while writing a page. The recorder releases
its own read-only view after every copy, so either reader can wait for the
replacement mapping. Windows tests used a disposable mapping with two real
readers. Actual AMS2 restart recovery remains NOT RUN.
[Windows mapping lifetime](https://learn.microsoft.com/en-us/windows/win32/memory/closing-a-file-mapping-object).

`status.json` reports the heartbeat, current coverage and most recent
fuel fields. `events.jsonl` records missing/stale/unsupported telemetry and
sampling delays. The schema is documented in
[telemetry archive](../schema/telemetry_archive.md).

The heartbeat retains the collector's continuity generation across later
healthy updates. If a reader misses an unavailable status during reconnection,
the changed generation still prevents capture admission and fuel approval from
carrying over. A detected break seals the preceding raw segment and records
its reason. Generation is scoped to the exact archive, and legacy statuses
may lack it.

Use [capture health](capture-health.md) with the explicitly selected run
directory to check two fresh heartbeats and advancing samples. Process
existence or a saved `running` label alone is not healthy capture evidence.

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

Status publication writes and flushes a temporary file, then atomically replaces
the prior status. Brief Windows access-denied or sharing violations during that
replacement allow at most five 10 ms waits before failing. Other errors fail
immediately. Exhaustion preserves the old destination and failed temporary;
the caller must expose the failure. This does not bound filesystem call latency
or support multiple writers to the same status path. No running collector is
updated merely by changing this helper's source.

New collectors isolate status writes in one publisher thread with at most one
pending snapshot plus the in-flight write. Raw segment writing and rotation do
not wait for status I/O. A newer pending snapshot replaces an older pending one;
this drops redundant status updates, not raw samples. Observation UTC and offset
are captured before archive/event flushes and retained through queuing and retry.
Publication cannot give stale observations a new timestamp. Consumers continue
to reject missing or expired status; continuing raw capture does not imply fresh
health, fuel approval or readiness while publication is unavailable.

Publication I/O failures use exponential retry delays from one to thirty seconds.
New observations do not reset that delay. Each attempt uses an exclusive
`status-publication-NNNNNNNN.tmp`; failed files remain intact. Sticky failure
counters appear in subsequent status snapshots and in the archive event journal.
The mailbox is bounded; retained diagnostic files have no total storage cap.
The collector checks free space once per second. It pauses raw sampling below
1 GiB and while the space query is unavailable. An available reading permits
sampling to resume in the same run. When a healthy source transitions to a
gap, the collector queues a noncurrent status before closing the prefix.
Storage readings reserve no bytes for diagnostics. Unexpected publisher
exceptions stop that publisher
with a visible fatal diagnostic while raw capture continues.

On stop, the collector finalizes raw segments, submits a terminal snapshot and
waits up to two seconds for that exact publication to finish. Terminal failure
or timeout returns nonzero and records `terminal_confirmed=false` when the event
journal remains writable. Closed raw segments are retained. An older running
snapshot cannot acknowledge shutdown; raw/source cleanup failures produce failed
status rather than false completion. Status I/O already blocked inside the OS
cannot be cancelled; its daemon may remain blocked until process exit. The bound
is on the wait, not all filesystem cleanup. In an embedding process that remains
alive, a delayed publisher may complete later without changing the earlier receipt.

`--stop` recognizes the archive's `run.json`, so an initial status outage does
not make the stop command unavailable. New run metadata declares
`status_publication_mode=isolated_thread_v1`; an existing process without that
receipt must not be assumed to have this behavior. The ordinary launcher may
restart a nonzero exit unless its existing disabled marker is set.

Offline tests include blocked publisher I/O and a real Windows read handle
without delete sharing on disposable files, with raw rotation continuing and
status recovering after release. These are filesystem tests, not live AMS2,
headset, gaming-performance or uninterrupted-coverage acceptance.

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

An opt-in [selected snapshot launcher](collector-releases.md) can instead run one
explicit reviewed source version with hidden output, native ownership and retained
attempt receipts. It does not replace this scheduled task, automatically retry a
failure or establish live/logon acceptance. Migrate only after reconciling existing
owners and archive closure during an idle maintenance window.

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
