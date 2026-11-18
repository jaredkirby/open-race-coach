# Read-only visit recovery inspection

`uv run python -m simcoach.cli.visit inspect <visit-directory>` reports current process evidence alongside preserved visit files. Keep the output private: it includes process identifiers and recorded metadata. Inspection does not write to the visit, acquire ownership, restart capture, retry analysis, play audio or infer driver departure.

The coordinator records its process identity in `host-process.json`. A spawn
receipt identifies the process the coordinator launched; a Windows virtual
environment launcher can differ from the Python process that actually records.
Each actual worker therefore records its own identity in
`<worker-directory>/worker-process.json`. Inspection exposes both identities. A
dead launcher does not establish that its worker is dead, and missing actual
worker identity remains unknown even when the launcher's exit is recorded.

Identity combines PID with Windows creation time. The latter is decimal text
because a native FILETIME exceeds JavaScript's exact integer range. A reused PID
is reported as absence of the **recorded** process while retaining the current
process observation. Legacy receipts without creation identity remain unknown.
Access denial and probe failures also remain unknown.

The inspector checks the initial `worker`, then `worker-0002` and `worker-0003`,
alongside their immutable `capture-attempts/0001`, `0002` and `0003` intent,
spawn and exit receipts. Initial root `worker-intent.json`/`worker-spawn.json`
remain visible as legacy provenance. A receipt-only attempt or a worker directory
without receipts is still inspected. `status.json`'s `current_worker` basename
selects the top-level worker/closure view; it cannot hide earlier attempts.
Absent pointers use the legacy initial directory. Invalid or unobserved pointers,
unexpected attempt entries, redirected paths and receipt conflicts remain
diagnostics rather than being silently ignored. Fixed known paths are used;
recorded command strings, arbitrary paths and session locators are never followed.

The probe requests only query and synchronization rights, obtains creation time and performs a zero-time wait, then closes its own handle. These operations follow Microsoft's [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess), [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes), [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject) and [access-rights](https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights) contracts.

`host_running` identifies the recorded host at the observation instant.
`worker_running_without_verified_host` means at least one recorded actual worker
or launcher is running; inspect the per-attempt identities to distinguish them.
It does not prove abandonment, since host access may be denied.
`no_recorded_process_running` requires the host and every discovered attempt's
actual worker and launcher identities to be observed absent/exited, with no
discovery or receipt-conflict issue. It describes only those recorded identities,
not unrecorded descendants or lock availability, and is never permission to start
another worker. A launch intent without trustworthy worker/launcher identities
is ambiguous. An exit-code receipt alone cannot establish actual worker exit.
Process presence does not establish capture health, audio delivery or user presence.

Heartbeat age is reported separately. A stale heartbeat cannot prove process exit.
Each attempt retains its own closure evidence, including failed earlier attempts.
Closure intent without result preserves uncertainty about effects that may already
have occurred. Even `result_recorded` means an artifact was observed, not that
validation or analysis passed. Inspection never retries those effects. Paths are
checked for escape and reparse components before reading, but files and probes
are sampled sequentially and can change during inspection; this is not a secure
transactional filesystem snapshot. Timestamps and hashes identify observed bytes.

The Python API is `probe_process(pid)` and `inspect_visit(run, probe=..., clock=...)`. Fake probes test recovery branches; a Windows-only test probes the test process without changing it. Neither establishes live simulator or audible radio validation. See [artifact contract](../schema/visit_recovery.md).

For a bounded independent timeline of these observations, use
[visit liveness observation](visit-watch.md). It pins host identity and preserves
process/heartbeat transitions without restarting workers or acquiring audio.
