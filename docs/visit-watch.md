# Independent visit liveness observation

`simcoach.live.visit_watch` runs outside the Driver Visit coordinator and keeps
private evidence of process/heartbeat changes. It reuses the existing
[read-only identity inspector](visit-recovery.md); a fresh status file is not
process proof, and a running process is not capture or fuel readiness.

```powershell
uv run python -m simcoach.live.visit_watch "<exact-visit-directory>" `
  --duration 60 --interval 0.5 --out "<fresh-private-monitor-directory>"
```

The chief operates this bounded command. It defaults to a fresh directory under
`.scratch/visit-watch/`, never inside the observed visit. It does not start or
stop a recorder, infer departure, change the simulator, acquire capture/radio
ownership, repeat closure/analysis or play audio. It is not installed at logon
and the visit host does not automatically launch it. Background invocations
must use the rig's hidden-launch procedure rather than opening a terminal.

The first usable host PID/creation-time receipt is pinned. A changed receipt
identity latches `binding_changed`; it cannot quietly switch to another owner.
Missing/malformed receipts or denied probes remain `unknown`. A reused PID means
the recorded process is absent. A host observed absent/exited without a terminal
status is `stopped`; this is not proof of a crash or driver departure.

`responsive` needs a matched running host and fresh advancing heartbeat. Repeated
polls can hold that assessment only within the heartbeat's original three-second
freshness window. Stale/unavailable/regressed heartbeat yields `unresponsive`,
never a claim that the process exited. `awaiting_progress` retains the first
baseline. `closing_reported` and `terminal_reported` describe preserved status,
not successful artifact closure, current process exit or completed acceptance.
The native identity remains alongside those labels.

Changes in coordinator assessment, process evidence, selected worker, worker
status or launch ambiguity retain a new full inspection and hash-bound journal
transition. A worker exit remains visible even if its coordinator is responsive.
The monitor's own observation gap over max(3 seconds, twice the polling interval)
is recorded as unknown and requires renewed progress. This is offline-tested
gap handling, not a machine sleep/resume validation.
The final wait is followed by an inspection even when it extends past the
requested duration, so completion cannot refresh a pre-gap responsive result.

Fresh status snapshots summarize current observation. The monitor stops at its
bounded duration; exit zero means observation completed, not a healthy rig.
Inspection read/clock errors become unknown evidence. Publication failure stops
the monitor and propagates; it cannot make a source process healthy or erase
earlier transition files. Existing output directories are refused.

## Scope and next integration

Synthetic Windows tests exercise a real test-owned child that crashes while
the monitor records responsive-to-stopped evidence. They do not terminate the
actual simulator, coordinator or capture worker. Game/audio performance, actual
logon, sleep/resume and unattended monitor uptime remain unverified.

This monitor records failure evidence only. It cannot warn through a radio owned
by a dead coordinator, and must not create a competing audio owner. An independent
single radio service with explicit lease/ownership and failure evidence is still
required for audible coordinator-death warning. Existing fuel loss/restored
phrases concern supervision within the living coordinator and do not satisfy
that gate. See [artifact contract](../schema/visit_watch.md).
The native radio now has a session-local output claim across journals, described
in [radio ownership](radio-prototype.md#one-owner-and-explicit-playback). It
prevents cooperating transports from overlapping; it does not move the radio
out of the coordinator or authorize this monitor to speak. The explicit
[operations service prototype](operations-service.md) now owns a separate local
Supervisor and the same radio path, with offline process-exit tests. Its opt-in
visit launcher and closure integration are offline-tested; the default remains
embedded, and ordinary live acceptance remains open.
