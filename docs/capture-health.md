# Capture health

Run this command from the repository with one explicit telemetry archive directory.

```console
python -m simcoach.cli.capture_health --run path/to/archive [--interval SECONDS] [--max-age SECONDS]
```

The reporter reads only `status.json` in that directory, twice. The default
interval is 1.2 seconds. The interval must be greater than zero and at most
10 seconds. The default maximum heartbeat age is 3 seconds. Both options must
be finite and positive.

- Exit **0** reports `healthy` when both schema version 1 snapshots show
  running capture, available coverage and a current latest sample. Both UTC
  timestamps must be non-future and younger than the maximum age when read.
  The sample count and elapsed offset must both increase, and the heartbeat
  timestamp must stay the same or increase. The collector's own status
  publication cadence can cause an otherwise active collector to appear
  stalled in a short observation window.
- Exit **1** reports `degraded` for observed issues such as stopped or failed
  capture, coverage gaps, stale or future heartbeats, stalls and regressions.
  It reports `unknown` when either read cannot be interpreted, such as a
  missing file, malformed status or unsupported schema.
- Exit **2** indicates invalid CLI usage. Argument errors go to standard error.

A completed observation emits one JSON report to standard output.
`reasons` contains machine-readable codes and 1-based sample numbers.
Two healthy-looking status snapshots on different archive continuity
generations report degraded health. A malformed present generation reports
unknown health. Older collectors may omit the field, so missing generation
on both samples cannot establish that no source gap occurred.

`sample_advance` gives separate count and offset booleans, or null when
two comparable snapshots are unavailable. `freshness` contains the maximum
age and the signed heartbeat age at each read when available.

`observed_at` is the UTC time of the second observation. A healthy report
expires at the second heartbeat timestamp plus the maximum age. Reports for
unknown or degraded observations expire at observation time. Expiry bounds
how long heartbeat evidence can be used. Capture can stop before expiry, so
take a fresh observation after a restart. Every invocation starts with two
new reads and never loads a prior result. The collector normally creates a
fresh directory per launch. Timestamp and counter regressions can expose
a replacement between reads, but status version 1 provides no run identity
that can prove continuity across a directory replacement.

The report gives evidence of capture progress over one observation window.
It does not establish whole-rig readiness, validate fuel units, or verify
archive segments or raw pages. The reporter emits no raw telemetry,
participant information, collector error text or archive paths.
