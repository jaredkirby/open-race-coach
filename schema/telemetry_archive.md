# Continuous telemetry archive

Schema version: 1.
Owner: `simcoach.cli.telemetry_watch`.
Write timing: samples stream continuously, closed segments and manifests
are synced at rotation, and status is replaced about once per second.
Compatibility: consumers must reject unsupported schema versions and raw
layout versions. Existing canonical artifact schemas retain their versions.

Required sample fields are listed below. Nullable values include sanitized
non-finite summary fields, `latest` before the first sample, initial
`coverage`, and `error` during successful operation. Time units are
seconds for offsets and UTC ISO 8601 for wall timestamps. Simulator fields
retain their original units. Status enum values are defined below.
Each launch creates a fresh private directory. UTC timestamps identify wall
time, and `offset_s` uses a monotonic clock within that run.

## Samples

`samples-NNNNNN.jsonl.gz` contains gzip-compressed JSON lines.
Each record has `schema_version`, `at`, `offset_s`, `sequence`,
`struct_size_bytes`, `summary`, and `raw_base64`.
The raw payload is the complete supported shared-memory page.
The summary exposes simulator fields such as `mFuelLevel`,
`mFuelCapacity`, controls, speed, game/session/pit state, lap information,
track and car. Non-finite summary values become null; raw bytes retain the
original representation.

The collector requires matching even sequence values before, inside and
after the copy. It rejects inconsistent copies and unsupported layout
versions. This check strengthens capture consistency, subject to the
simulator's sequence protocol. The archive preserves observed samples
rather than guaranteeing every simulator update.

## Segment integrity

Active or failed segments carry a `.partial` suffix. Once closed and
synced, a segment is renamed and appended to `segments.jsonl`, with
`path`, `samples`, `started_offset_s`, `sha256`, and `closed_at`.
Hashes cover the compressed bytes. Failed or interrupted files remain for
diagnosis. A crash between rename and manifest append can leave a closed
orphan segment, which must be inspected before use. Forced termination can
leave a truncated gzip stream; recovery may salvage only its complete
records.

## Status and events

`status.json` is atomically replaced about once per second. It contains
`schema_version`, `status`, `updated_at`, `offset_s`, `coverage`,
`samples`, `target_hz`, `latest`, `latest_is_current`, and `error`.
Statuses are `running`, `complete`, and `failed`. After an unclean
process exit, an old running heartbeat denotes interrupted observation.
Check `latest_is_current` and heartbeat age before treating `latest`
as a current simulator reading.

`events.jsonl` records coverage transitions with wall time, offset,
previous and current state, plus sampling delay events. Coverage can be
available, unavailable, unstable, stale, unsupported, low-disk, or stopped.
Use the exact emitted coverage value as the reason for each observed gap.
Completion describes archive shutdown and does not certify uninterrupted
coverage or successful simulator validation.

Raw archives are private and may contain participant names. Canonical
Recorded Sessions and Analysis Runs retain their own schemas and validation
contracts.
