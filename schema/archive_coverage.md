# Bounded archive evidence spans

Schema version: 1.
Owner: `simcoach.live.archive_coverage`.
Write timing: an explicit offline audit returns a report; an optional writer
publishes it into a new private directory using an exclusive partial, fsync and
rename. Source archives are read-only and never repaired or controlled.
Compatibility: supported run/schema version 1, raw layout 14, size 20700 bytes and
sequence offset 7320 are required. Unsupported inputs yield unknown evidence.

## Required inputs, denominator and units

`analyze_archive_coverage(archive, *, start_at, end_at, segment_names, max_gap_s=.1)`
requires an explicit archive, original UTC start/end and unique closed segment
names. Names must exactly match `samples-NNNNNN.jsonl.gz`; paths, partials and
duplicates are refused. The fixed denominator is the requested UTC end minus
start, in seconds, never the span of whichever samples passed. No offset boundary
is inferred from successful samples, no window is trimmed, and no seat/Driver Visit
duration is established. UTC strings or timezone-aware UTC datetimes are accepted.

For adjacent original observations, both UTC and run-offset deltas must be positive
and agree within the declared 25 ms tolerance. UTC-minus-offset alignment stays within
50 ms of one anchor across selected verified samples, including across omitted files;
the anchor is not reset after a gap. Cumulative drift or a discontinuity makes the
metric unknown, even when each small delta looked plausible. Original times are
never shifted, substituted or extrapolated to make the window fit.

`verified_sample_span_s` is the union of original UTC intervals between adjacent
fully verified samples where **both** UTC and offset gaps are at most `max_gap_s`,
clipped to the requested window. Default maximum gap is 0.1 s; permitted values are
positive and at most 1 s. `unverified_s` is the remaining requested duration. It is
**not proven lost-data time**, missing sample count or absence from the seat.
`advancing_sequence_span_s` counts the subset whose sequence strictly advances;
it is not proof of driving, participant identity or every simulator update.

## Segment verification and bounds

Only explicitly selected, closed, manifested segments contribute. Verification
checks compressed SHA-256, gzip completion, strict JSONL records, manifest count
and first offset, raw base64/20700-byte layout/version/even matching sequence,
summary version/sequence, original UTC and offset progression. Raw participant
fields are never decoded or emitted. Repeated sequence values can represent valid
captured bytes but are counted separately from advancing sequence intervals.
Sequence regressions likewise do not establish fresh updates or an inferred wrap.

A segment contributes only after its entire stream passes. Corrupt, omitted,
partial or unmanifested material cannot supply a bridge. Across files, a bridge
requires consecutive manifest entries and consecutive segment numbers, both fully
verified. A manifest entry with `continuity_break_after=true` excludes the
interval to its successor with `reason=collector_reported_coverage_break`,
even when the times fall inside the gap tolerance. Both verified prefixes can
still contribute their internal spans. A failed middle segment cannot join
its successful neighbors.

Limits are recorded in each report: at most 16 selected segments, 2 MiB per metadata
read, 32 MiB compressed per segment, 256 MiB compressed work including rechecks,
128 MiB decompressed per segment, 1 GiB decompressed total, 64 KiB per record, 50,000
sample-line guard and 60 seconds of checked work. Boundary detection can read one
extra record/byte before refusing. Limits stop further checking and preserve
partial/unknown findings. Checks cannot preempt a blocking operating-system read.
No full-archive raw scan or simulator observation is performed.

Run identity and selected segment hashes are rechecked. Normal append-only manifest
growth outside the selection is allowed; the initial manifest prefix must remain
unchanged. Changed source bindings or uncompleted rechecks make the metric unknown.

## Report contract

Required report fields: `schema_version`, `archive_path`, nullable `source_id`,
`requested_window`, `requested_segments`, `max_gap_s`, `delta_tolerance_s`,
`alignment_tolerance_s`, `limits`, `scope_complete`, `integrity_result`,
`metric_known`, nullable `metric_unknown_reason`, nullable metric seconds above,
`segments`, `boundaries`, `diagnostics`, `samples_verified`, `samples_in_window`,
`repeated_sequence_samples`, `sequence_regression_intervals`, `over_gap_intervals`,
`provenance`, `meaning`. Completed source admission also records `work` byte/line
counters. `integrity_result` and segment result enum: `PASS`, `FAIL`, `NOT RUN`.
Integrity describes the selected verification work, not whole-window coverage.
`scope_complete` reports whether work limits prevented completing requested checks.

Each segment retains path, result/reason, expected hash and observed hash when read,
plus verified sample count and diagnostics. Counts describe the selected segment
scope, including samples outside the clipped window; `samples_in_window` explicitly
limits point timestamps to the requested endpoints. Boundaries record whether a
bridge was admitted. Provenance retains initial/final manifest hashes, original
run hash, source stability and UTC-offset alignment diagnostics. No raw page,
participant name, summary payload or absolute position is copied into the report.

When at least two verified samples establish a stable time mapping, a partial audit
can report a conservative lower-bound span from verified segments. Unknown mapping,
insufficient verified samples or unstable source bindings produce null metrics,
not invented zero or full coverage. Preserve every failed and insufficient result.

`write_archive_coverage_report(report, output_dir)` returns `report_path` and `report`.
The directory must be new and outside the audited archive; existing output and
source archive paths are refused. Reports contain private locators and hashes.

## Visit envelope audit

`simcoach.live.visit_coverage` produces a private `plan.json`, `report.json` and
optional `tile-NN/report.json` in a new immediate child of the visit's
`coverage/`. Refusals after allocation retain `failure.json`. Schema version 1,
kind `visit_archive_coverage_plan` or `visit_archive_coverage`. The audit
requires one archive named by the terminal live host and its `run.json` SHA-256.
The existing terminal clock/departure contract must hold. Replay and uncertain
departure do not supply a declared envelope. A claimed arrival is checked
against the original root-owned declaration bytes, resolution, terminal clock
and departure counter. A reviewed receipt cannot replace that claim. The optional
reviewed arrival receipt binds the recorded clock's `visit_id` and original time. It is a
review declaration with hash provenance, not independently authenticated presence.

The envelope reports `start_at`, `end_at`, `duration_s`, the scope
`host_start_to_departure`, `declared_arrival_to_departure` or
`reviewed_arrival_to_departure`, `prehost_declared_s`,
and both wall and monotonic host durations. The UTC denominator remains
unchanged across tiling and does not rewrite the monotonic clock ledger.

The frozen plan selects candidates by manifest closure time and includes
neighboring segments when they can support a bridge. Selection is bounded and
does not prove every overlapping segment was chosen. There are at most 30
disjoint 300-second tiles with the standard 0.1-second gap bound. Each tile has
the base audit's independent work limits. The aggregate checks a 120-second
work budget between tiles and final source checks; a tile already in progress
retains its own work limit. The final compressed-source recheck uses the base
256 MiB budget. Ordinary manifest appends stay outside the frozen plan.

Report `result` is PASS for a supported positive lower bound, FAIL for selected
integrity faults or unstable input bindings, and NOT RUN for absent/insufficient
verified span or incompatible aggregate clock alignment. `metric_unknown_reason`
explains withheld counts. Tile locators, provenance and failures remain visible.
`verified_span_lower_bound_s`, `lower_bound_percent` and
`unverified_or_unknown_s` are nullable. Known tile spans are clipped to their
disjoint intervals before summing. A compatible shared alignment bound, stable
archive/declaration identities and final checks of contributing segment hashes
are required. Uncredited evidence cannot shorten the denominator.
