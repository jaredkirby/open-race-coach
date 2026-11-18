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
`offset_s` is elapsed observation time from the collector's monotonic origin,
not simulator time. New collectors default to the high-resolution performance
counter for offsets and pacing. Older collectors may use a coarser clock;
consumers must assess the recorded offsets without inventing spacing.
The summary exposes simulator fields such as `mFuelLevel`,
`mFuelCapacity`, controls, speed, game/session/pit state, lap information,
track and car. Non-finite summary values become null; raw bytes retain the
original representation.

New summaries also expose simulator-reported `mLastLapTime`, `mBestLapTime` and
`mCurrentTime` in seconds. Negative/unavailable sentinel values remain source
values here; consumers must treat them as unavailable. Non-finite values become
null. Older collectors may lack these additive fields. They are not normalized
captured lap times, reviewed debriefs or independently checked leaderboard times.

The collector requires matching even sequence values before, inside and
after the copy. It rejects inconsistent copies and unsupported layout
versions. This check strengthens capture consistency, subject to the
simulator's sequence protocol. The archive preserves observed samples
rather than guaranteeing every simulator update.

## Segment integrity

Active or failed segments carry a `.partial` suffix. Once closed and
synced, a segment is renamed and appended to `segments.jsonl`, with
`path`, `samples`, `started_offset_s`, `sha256`, and `closed_at`.
An additive boolean `continuity_break_after=true` records when a known
mapping/storage break or an accepted-sample gap of at least two seconds closes
the segment. Sustained unstable copies also release the current mapping view,
so a replacement publisher can reclaim the name after the last handle closes.
The event retains its observed coverage reason. Regular rotation and orderly
shutdown omit this field. Auditors
can verify each prefix independently and leave the interval after a marked
segment uncredited. Older entries may omit the marker.

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

New `latest.participant_observation` is an additive version-1 summary containing
`schema_version: 1`, `index` (raw viewed slot), `count` (raw participant count),
`active` (boolean), `car` (slot car label), `race_state` and `pit_mode` (slot
enums). Invalid/out-of-bounds slots retain index/count with other fields null.
No participant names or other slots are included. This describes a viewed slot,
not an authenticated player identity. Older running collectors and saved
statuses lack this object; missing evidence must not be invented. Raw pages
remain unchanged and may be used for explicitly labelled offline reconstruction.

New status also includes `continuity_generation`, an integer from zero through
2^53 − 1 scoped to this archive run. It starts at zero. Once the collector has
accepted samples, a known mapping, layout or storage interruption, sustained
unstable copy, source sequence regression or a two-second gap in accepted
samples advances it. Repeated misses in one interruption do not advance it
again. A `continuity_break` event records the generation and reason. An available
heartbeat keeps the new generation after reacquisition, including when the
isolated status publisher coalesces a transient degraded snapshot. Brief
unstable copies and normal segment rotation do not change the generation.
Older status files omit it. It cannot identify a publisher or detect resets
that leave no observed boundary.

New run metadata adds `status_publication_mode=isolated_thread_v1`. Legacy runs
may omit it; source edits do not update a running collector. New status timestamps
and offsets bind the observation before flush/publication; retries retain them.
Status may be missing or stale while raw writing continues. Never promote a stale
status using process existence, publication attempt time or a later diagnostic.

The isolated publisher has one in-flight snapshot and at most one pending
snapshot. Replaced pending snapshots increment a counter and do not discard raw
samples. Retry backoff is one through thirty seconds; terminal shutdown bypasses
backoff for one attempt of the final pending generation. Filesystem calls can
block beyond the coordinator's bounded wait. The default two-second shutdown
wait does not bound raw/event cleanup or cancel an in-flight call.

New status includes `cleanup_errors` (array of `phase`, `error_type`) and
`status_publication`, with `observation_sequence` and `prior_state`. The latter
reports the publisher state immediately before that write, not acknowledgment
of itself: `schema_version=1`, `submitted_sequence`, `confirmed_sequence`,
`attempts`, `failures`, `consecutive_failures`, `superseded_snapshots`, nullable
`last_failure`, and boolean `closing`, `finished`, `fatal`. Sequence/counter values
are run-local integers. Last failure has integer `attempt`, `observation_sequence`,
UTC ISO `at`, `error_type`, nullable numeric `errno`/`winerror`, and a temporary
basename. Observations retain their original UTC/offsets; failure times are not
sample times. Counters are diagnostic, not raw-sample loss or measured latency.

Each status attempt uses an exclusively created
`status-publication-NNNNNNNN.tmp`. Successful replacement consumes that temporary;
failed temporaries remain for inspection. No failed file is reused or deleted.
The queue is bounded but diagnostic file retention has no global size cap.
The shared `write_status` helper's default temporary behavior for other callers
is unchanged; the collector passes its unique temporary explicitly.

`events.jsonl` records coverage transitions with wall time, offset,
previous and current state, plus sampling delay events. Coverage can be
available, unavailable, unstable, stale, unsupported, low-disk, or stopped.
Storage states include `disk_reserve` and `disk_probe_unavailable`. Space
readings at the archive destination are sampled on a one-second cadence, and
unavailable measurements hold new capture until a good observation. Gap
transitions from available also queue a noncurrent status before raw closure.
Use the exact emitted coverage value as the reason for each observed gap.
Completion describes archive shutdown and does not certify uninterrupted
coverage or successful simulator validation.

Additional events: `status_publication_failure` records UTC `at` and the observed
publisher `diagnostic`; `status_publication_closed` records UTC `at`, nullable
`terminal_sequence`, boolean `terminal_confirmed`, `capture_status` and publisher
`diagnostic`. These are written by the raw owner, never by the publisher thread.
Failure events can summarize multiple attempts since the previous observation;
use the sticky counts rather than counting event lines. Terminal confirmation
requires that exact submitted terminal generation to finish, not an older running
write. It may confirm a failed capture status. A failed submission or timeout
remains unconfirmed; process exit is nonzero even if all raw segments closed.
If the event journal itself fails, a final receipt may be absent and exit remains
nonzero. No artifact is repaired to make closure look successful.

Raw archives are private and may contain participant names. Canonical
Recorded Sessions and Analysis Runs retain their own schemas and validation
contracts.

New run metadata may include `runtime` with native process identity, Python and
performance-counter details plus workspace/snapshot source mode. A snapshot
includes checked source identity under the [collector release contract](collector_release.md).
Legacy absence is unknown provenance; it must not be inferred from today's files.
