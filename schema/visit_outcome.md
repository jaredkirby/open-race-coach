# Offline Driver Visit outcome

Schema version: 1.
Owner: `simcoach.live.visit_outcome`.
Write timing: explicitly after visit closure, to a fresh private output directory;
read-only snapshots may also expose incomplete/nonterminal evidence. Publication
uses exclusive partial files, flush/fsync and rename. Failed partials are retained.
Compatibility: unsupported source versions remain unknown; canonical artifacts keep
their own schemas. No validation, deterministic analysis, repair or playback runs.

## Required report fields and units

Additive nullable `operations` inventories service-backed visits: reported
binding, service run/status, launch intent/receipt, supervision-journal byte
hash and size, forwarding intents and service receipts. The selected radio
journal is `operations/radio.jsonl`; embedded history is not silently mixed in.
Missing/mismatched service binding makes radio counts unknown. Source hashes
are verified with other inputs, and nonterminal service status prevents a
reported-terminal scope. A stored binding/process report is not a new native
probe. Incomplete/failed artifacts remain inventoried rather than omitted.

Required `report.json` fields: `schema_version`, `created_at` (UTC ISO timestamp),
`visit_path`, `source_snapshot`, `snapshot_scope`, `visit_status`, `mode`,
`visit_problem`, `worker_status`, `worker_error`, `worker_stop_reason`, `worker_exit`,
`clock`, `recordings`, `closure_timing`, `radio`, `unmeasured`, `sources`, `limits`.
Problem/error/exit and unsupported facts are nullable; unknown is never converted
to zero. All durations use seconds. Source-snapshot enum: `stable_at_read`, `changed`.
Snapshot-scope enum: `reported_terminal`, `nonterminal_or_unknown`. These describe
the read artifacts, not a fresh process-health verdict or proof of completeness.

The writer returns `directory`, `report_path`, `report`. It creates a fresh short-ID
directory under `outcomes/` unless an explicit new destination is supplied. Existing
destinations are refused. `report.md` summarizes lap timing; `report.json` holds the
complete evidence. Terminal coordinator status alone is not outcome-publication
success. The host records its separate outcome publication receipt after writing;
it must not rewrite the input status whose hash is now bound into the report.

New writers also include `output_reviews`, `archive_coverage_audits` and
`supplemental_evidence_scope`. Older version-1 reports may omit these fields;
omission means unavailable evidence, not zero feedback. The arrays inventory
every directory under this visit's `reviews/` and `coverage/`, retaining accepted,
failed, incomplete and unsupported attachments. Each row includes `directory`,
nullable `issue` and `files`; each file records its parsed `reported` object or
null and a state enum of `reported_version_1` or `unknown_or_unsupported`.
Version recognition is not a new validation of the attachment's claims. Source
hashes join the report's normal provenance and change checks. Resolved paths
outside the selected evidence directory are not followed.

New reports additionally retain `worker_capture`, a nullable object copying the
worker's reported `continuous_source_gaps`, `capture_phase`, `source_gap_reason`
and `source_gap_count`. `recording_storage` retains the host's last storage
observation at the top level and the individual worker's last storage
observation in each `worker_capture` object. Missing legacy fields stay null.
These are reported worker
diagnostics, not a revalidated coverage metric or proof that the source recovered.
The normal source hash/change checks apply. Inspect source stability and scope;
successful closure and valid prefix recordings do not imply uninterrupted capture.

These are reported attachments, not promoted metrics. Original output bindings
and archive contents are not re-audited here. A chief's assessment is not driver
feedback, declared feedback is not identity authentication or proof of delivery,
and a selected archive window is not automatically the whole Driver Visit.
Refreshing the outcome creates another report and preserves earlier reports and
the host's initial outcome receipt.
Resolved output destinations inside `worker/sessions` are refused so this writer
cannot add files inside a canonical Recorded Session or Analysis Run.

## Time and recordings

Each recording row adds nullable `lap_review` and `lap_review_reason`. A review
requires one worker closure record reporting validation PASS for a complete
Recorded Session, matching session ID and validation counts, supported lap schema,
and stable, owned metadata and lap bytes. The reader bounds lap files to 128 KiB
and 512 records per session. Failed, partial, malformed, oversized, redirected
or changing evidence withholds the affected review. Older outcomes may omit both
fields. The recorded validation receipt and observed file hashes remain
separate provenance. The outcome does not revalidate ticks.

`lap_review` has scope `reported_validated_recorded_session`, context
(`car`, `track`, `session_type`), every projected lap with its ID, time, timing
source, validity and exclusion reason, and the `exclusions` counts.
`statistics` and `timing_groups` use the shared lap timing contract.
Mixed source records retain distinct timing groups and withhold combined timing
values. Statistics include every Valid Lap in its applicable group.
No pooling across Recorded Sessions, leaderboard context or technical diagnosis
follows from the timing summary.

Clock totals are accepted only from equal, supported terminal `clock/status.json`
and `clock/summary.json` with successful publication and valid disjoint partitions.
Activity totals sum to recorded elapsed time; reason totals subdivide only observed
non-driving time. Interrupted visits retain unknown presence; complete clock output
can establish an explicit declared departure. No seat time is extrapolated after
the last recorded interval, and replay presence remains a test stimulus.
This accepts reported totals, not independently measured activity accuracy. The
host's bounded held estimates and unknown source/context boundaries remain explained
by endpoint evidence in `clock/events.sqlite3`; this report does not re-audit that
journal or turn its estimates into exact driving/occupancy measurements.

New reports copy the clock's optional `reason_policy` into `clock.reason_policy`.
For new clocks `until_driving` means declarations clear after a supported driving
interval; legacy clocks lacking the field produce null. This is reported provenance,
not a reason to relabel earlier totals. Unknown or changed clock evidence also
produces null. Older outcome reports may omit this additive field.

When the live host reports a claimed arrival and its clock snapshot matches
the terminal ledger, `clock.prehost` includes `scope=reported_host_binding`,
`arrived_at`, `host_started_at` and `unknown_s`. This duration is already part
of the unknown activity total. It is null if the host binding is absent,
inconsistent or changed during reporting. The separate visit coverage audit
verifies the original arrival and resolution receipts.

An optional `clock.prehost.declared_intent` contains the reason partition and
note-chain evidence from the independently rechecked arrival and resolution
receipts. `state=verified` includes `note_count`, `head_sha256`, `reason_s`
and `resolution_sha256`. A mismatched or failed note chain produces only
`state=unavailable`. No pre-host reason changes the existing unknown activity
total. Show pre-host declared waiting separately from the host's
`not_driving_reason_s.assistant_wait`. These values cover the explicitly
attributed intervals and do not measure all assistant-caused delay.

`closure_elapsed_utc_s` uses closure-intent `at` and closure `closed_at` only when
finite and ordered. `closure_vs_declared_departure_utc_s` is a signed difference
from an explicit terminal-clock departure; negative means closure preceded departure.
These are UTC-derived durations, not monotonic latency, acoustic latency or driver
waiting time. Missing/reversed/changed evidence yields null with a reason.

Recording inventory combines closure/progress rows, worker-created paths and the
owned sessions directory. Each distinct owned path remains visible, including
failed, incomplete and unregistered attempts. Referenced paths outside that root
are not read. Every analysis directory within an owned session is inventoried;
none is chosen as the best result. Reported validation/analysis values preserve
PASS/FAIL/NOT RUN/UNKNOWN. Canonical `run_status`, `analysis_status`, failure reason,
session identity match and report locator are copied as reported metadata, not a
new acceptance verdict. Insufficient data and failures are retained. No Reference
Lap is selected, no Coaching Instruction is added, and Coach Refinement is absent.
`inventory_reports` retains each source row, including progress-only failures and
conflicting earlier reports for the same path. Final closure supplies the primary
reported status when present; progress never overrides it. Unsupported intent or
progress versions are retained in provenance but cannot establish timing/inventory.

## Radio evidence

Radio reads `radio.jsonl` without opening a Dispatcher. Supported histories retain
all transmissions and transitions. Final-state enum follows the radio schema:
generated, queued, playing, played, expired, suppressed, interrupted, failed,
acknowledged. Final-state counts are disjoint by transmission. Transition counts
count records and can exceed transmission count. Transport-completed count includes
both played and subsequently acknowledged transmissions; acknowledgment count is
separate and must not be added to it. Neither establishes acoustic output or hearing;
heard/not-heard counts remain null. Host radio failures remain separately reported.

`software_timing` per transmission exposes nullable durations for dispatcher
queue, output intent observation to transport call, start call to confirmation, and
confirmation to terminal observation. Each reported local interval requires
ordered points within one timing epoch and wall/monotonic agreement within
0.5 seconds. The intent-to-call interval includes journaling and final checks.
Source-observation-to-decision and source-observation-to-transport
confirmation are UTC-derived and separately labeled. Confirmation also reports
whether it was observed before the original deadline. This is a software
milestone. Audible latency remains null. `software_timing_scope` distinguishes
`reported_terminal`, `parsed_prefix` and `unverified`. Source changes or an
unbound operations journal withhold the timing. Legacy or incompatible points
remain null. Expired/suppressed/failed calls remain in the normal histories.

The additive `fuel_return_audit` joins committed Supervisor `fuel_return` notices
to the selected radio journal in a stable terminal visit. It uses the embedded
journal or the bound operations service journal and explicit audio configuration.
The outcome exposes `reason`, nullable `decision_count`, nullable `outcome_counts`,
nullable `unmatched_transmission_count` and `decisions`. Each decision contains
its decision ID, arm ID, creation and expiry UTC seconds, expected transmission ID,
outcome and nullable matched `transmission_state`. The match checks the exact
transmission and deduplication identities, evidence ID, creation time, expiry,
available source time, priority and optional flag.

One outcome is counted per known decision. Values include
`confirmed_before_deadline`, `confirmation_at_or_after_deadline`, `muted`,
`expired`, `suppressed`, `interrupted`, `failed`, `start_unverified`,
`binding_mismatch`, `no_dispatch_record`, `dispatch_evidence_unavailable`
and `audio_disabled`. Confirmation uses the existing clock-qualified software
timing. Missing dispatcher evidence cannot establish that an attempt was absent.
Unmatched counts require both usable journals and enabled audio. They do not
establish false calls. Decision counts remain null when the supervision snapshot,
its owner or configuration is unavailable or changes. The database snapshot is
limited to 64 MiB and 512 notices. Unsupported SQLite mode or a present write
sidecar withholds the audit. Source files retain their hashes and are never opened
through SQLite, recovered or rewritten.

Invalid/truncated journals preserve parsed-prefix observations but expose no final
counts. Nonterminal/unknown visit snapshots likewise report prefix observations,
not final outcomes. An absent journal does not establish zero attempted messages.
Capture coverage, false/missed call counts and useful-debrief latency remain null
because these artifacts do not establish them.

## Provenance and privacy

Recovery-aware reports add `current_worker` (nullable basename), `worker_attempts`
and `worker_summary_scope`. Legacy reports may omit these fields. Singular worker
and closure-timing fields summarize only the explicit current worker, falling back
to `worker` only when a legacy parent omits the pointer. They are not aggregate
visit success or latency. Unknown current pointers remain unknown.

Each worker attempt has `attempt` (integer derived from its locator),
`worker_directory`, `path`, reported `worker_status`, nullable `worker_error`,
`worker_stop_reason`, `worker_exit`, `worker_capture`, `worker_process`,
`launch_receipts`, `closure`, `closure_intent`, `closure_timing`, `recording_count`
and `issues`. Receipts and process identity are reported evidence, not a fresh
liveness probe or proof that capture ownership was released. Missing/malformed
artifacts remain in source provenance. Attempts with only launch receipts or
unresolved closure intent remain visible. Every recording row adds
`worker_directory` and `worker_attempt`; older rows may omit them.

`launch_receipts` includes nullable `capture-ready` from the immutable
`capture-attempts/NNNN/capture-ready.json` receipt alongside intent/spawn/exit.
The readiness receipt reports `schema_version`, UTC Unix `at`, `attempt`,
`worker_directory`, `launch_to_ready_s`, nullable `observed_exit_to_ready_s`, and
`basis`. Durations use the host's monotonic observations. They are not capture
coverage gaps or driver delay and are not summed across attempts. These are
reported fields bound to exact input bytes; the outcome does not independently
re-establish readiness. Missing receipts remain null.

Initial `worker` and sibling `worker-[0-9]{4}` roots must resolve directly under
the selected visit; external aliases are not followed. Numeric capture-attempt
receipt directories also expose launch attempts without worker output. Directory
discovery alone does not certify a valid launch or the host's attempt limit.
Each owned sessions root is inventoried separately and excluded as an outcome
destination. Closure timing is computed per attempt without adding durations or
discarding earlier failed results. An unknown/nonterminal attempt prevents claiming
a fully terminal evidence snapshot. No automatic reconciliation effects occur.

Each source records path, SHA-256 of bytes actually read, size, initial existence,
read state and optional error class. Missing or malformed inputs remain listed.
Source bytes are rechecked before publication, including initially missing files
appearing during the read. Change invalidates aggregate clock/timing/radio certainty.
This is bounded consistency checking, not an atomic filesystem-wide snapshot.
All original sources and output attempts remain untouched. Reports include private
paths and operating evidence and must stay local; they are not publication-ready.

Each worker attempt includes a `checkpoints` supplemental inventory of intent,
handoff, status, closure intent/result and consistency manifests from the exact
worker's checkpoint directories. This retains failed and uncertain effects.
Recordings are counted from aggregate worker closure, not counted again from
checkpoint manifests. Supplemental files follow the same hash/read-state rules;
checkpoint completion is not a live-validation or driver-delivery claim.
Only checkpoint consistency.json additionally recognizes schema_version=2 as
reported_version_2. Other attachments still require version 1. Recognition is an
inventory label, not independent validation of timing source, completeness or
review sufficiency; original version 1 and unknown versions remain retained.
