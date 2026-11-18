# Driver Visit time ledger

Schema version: 1.
Owner: `simcoach.live.visit_clock`.
Write timing: append transactions at observations, prospective annotations and
explicit departure; atomic status after each transaction and one terminal summary.
Compatibility: a fresh directory and visit identity are required for each start.
Never reopen a previous visit as running or infer occupancy from simulator activity.

## Boundaries and clocks

Construction requires an explicit `presence_confirmed=True`, UTC timestamp and
`time.perf_counter()` observation. Every update uses both clocks; elapsed duration
comes only from the monotonic difference. Negative monotonic differences are
rejected without appending. A fresh visit is required after a clock reset.
Wall-clock reversal or disagreement above 0.5 seconds makes the interval unknown.
An observation gap above `max_gap_s` (default 1 second) is entirely unknown.

A live host can initialize the clock from a claimed arrival declaration and
record one unknown interval ending at host start. That interval has no
simulator evidence or non-driving reason. See the
[arrival receipt contract](driver_visit.md#arrival-before-host-startup).

Explicit `end()` declares absence and closes the unobserved tail as unknown.
It rejects subsequent updates. `close()` records interruption at the last measured
time and leaves presence unknown; it does not invent elapsed time or absence.
A crash can leave running status without a terminal summary: that visit is incomplete,
and time after its last journal observation is unmeasured, not driving or waiting.

## Artifacts

`events.sqlite3` contains `events(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)`.
The append-only writer never updates prior rows. Every payload includes
`schema_version`, `visit_id`, `event` and `at` (UTC ISO timestamp).
Event values: `started`, `interval`, `reason_changed`, `ended`, `interrupted`.
Units are seconds for durations/offsets and UTC ISO timestamps for wall time.
Nullable fields are interval `reason` outside non-driving activity,
`terminal_intent` before closure, and `publication_error` without a failure.
Intervals include `start_at`, `start_offset_s`, `end_offset_s`, `seconds`, `activity`,
`reason`, `gap` and `clock_changed`. Offsets are run-local monotonic seconds.
Reason changes apply only to intervals after the annotation and never relabel
previous records. The journal transaction commits before in-memory totals advance.

New interval events also include nullable `source_evidence`; older version-1
events may omit it. It describes only the current endpoint, not complete interval
coverage. The archive-backed host supplies a detached ActivityEvidence snapshot
with `schema_version=1`, `activity`, `evidence` (`advancing`, `held`, `unknown`),
nullable `reason`, nullable `source_id` (verified archive SHA-256 identity), nullable
`source_at` (UTC Unix seconds), nullable `sequence`/`samples` integers, nullable
`offset_s`, nullable `expires_at` (UTC Unix seconds), nullable `expires_elapsed`
(local monotonic seconds), and `policy` with `max_age_s`, `max_poll_gap_s` and
`clock_tolerance_s`. Values are reported evidence; the clock's structural checks
do not reopen or independently validate an archive. No raw pages or context labels
are included by this producer. Direct callers may omit evidence; omission does
not establish a verified source or fresh classification.

Default admission requires two strict timestamp/count/offset/even-sequence advances
within two seconds under unchanged source identity and context. Menus may have empty
context labels. A repeated admitted snapshot can be held only until both its original
UTC expiry and monotonic deadline; polling does not refresh these. A new heartbeat
without source progress cannot renew admission. Source/context changes, a
changed archive continuity generation, unsupported or malformed inputs,
sequence regressions/wraps, host gaps above one second and
clock discontinuities revoke admission. Host UTC-minus-monotonic and source
UTC-minus-offset alignment are checked against fixed anchors, with default 0.5s
tolerance. Reset candidates establish a baseline, not a known activity endpoint.

`status.json` is replaced atomically; `summary.json` is published once at terminal
completion/interruption, with flush and fsync before replacement. Failed temporary
files and journals remain for diagnosis. A summary publication failure does not
make the journal disappear; no existing directory or terminal summary is reused.
Terminal summary publication precedes the terminal status checkpoint, so a failed
publication cannot leave a newly completed status without its summary. Read a
terminal summary together with its matching terminal status; publication is not
accepted until both files agree. Filesystem replacement is atomic per file, not
across this pair. If later status publication fails, preserve the summary and old
checkpoint for review rather than promoting the incomplete publication.
Summary fields: `schema_version`, `visit_id`, `status`, `presence`, `started_at`,
`updated_at`, `elapsed_s`, `activity_s`, `not_driving_reason_s`, `current_reason`,
`journal_sequence`, `terminal_intent`, `publication_complete`, `publication_error`.
Status enum: `running`, `finalizing`, `complete`, `interrupted`, `publication_failed`.
Presence enum:
`declared_present`, `declared_absent`, `unknown`.

The in-process status becomes terminal only after both summary and checkpoint
publication succeed. Failure latches `publication_failed`, with error class only,
and `publication_complete=False`. `close()` preserves that failure without retry,
another terminal event or additional time. The committed `terminal_intent` may
still be complete, and presence may still be explicitly declared absent: neither
means artifacts were successfully published. A surviving process cannot silently
reopen or repair this state. A crash between file replacements likewise requires
manual evidence review, not automatic inference from a lone summary.

## Disjoint accounting

Activity enum: `driving`, `pit_moving`, `not_driving`, `transition`, `unknown`.
Two matching usable observations assign an interval to that activity; differing
observations assign transition; either unknown endpoint assigns unknown.
In the archive-backed host, usable endpoints can include explicitly bounded held
estimates of the same source observation. The resulting activity partitions are
estimates, not proof of uninterrupted observed driving. The source-evidence mode
does not override the interval's gap, clock-change or unknown-endpoint rules.
The activity totals sum to measured elapsed time, subject to floating-point tolerance.
Pit movement may be automated and is not proof of driver control.

Reason enum: `intentional_rest`, `setup`, `assistant_wait`, `other_wait`, `unspecified`.
These are declared intent, not telemetry conclusions. `not_driving_reason_s` is a
partition of only the `not_driving` activity total. Its sum equals that total;
do not add it to activity totals. All other activities have null interval reason.
Unknown intervals remain unknown and cannot be charged to a waiting reason.
New started events and summaries include `reason_policy=until_driving`.
Under this policy `current_reason` retains the last prospective declaration until
explicitly changed or a positive interval is classified as `driving`. The latter
clears it prospectively to `unspecified` with a `reason_changed` event at the same
endpoint, `cause=driving_resumed`. Explicit annotations use `cause=declared` and
take precedence at their timestamp. Interval and reason-change events commit in
one transaction before in-memory state advances. Zero-duration observations,
transitions, pit movement, gaps, clock faults and unknown intervals do not clear
the declaration. Later non-driving time requires a new declaration for attribution.

These are additive version-1 fields. Older started events/summaries may omit
`reason_policy`, and older reason events may omit `cause`; those ledgers retained
the last declaration until explicitly changed. Never retrofit the new policy into
historical totals. A reason does not establish activity or explain every delay.
Rest is not product friction, and unspecified non-driving time is not automatically
waste.

Artifacts are private operating records under an ignored caller-selected directory.
No raw telemetry, participant identity or freeform personal notes are required.
This ledger does not modify the existing driver clock or canonical artifacts.
