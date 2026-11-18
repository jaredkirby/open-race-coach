# Offline archive evidence audit

This auditor checks a deliberately selected UTC window and named closed archive
segments. It does not run automatically over a visit or scan the whole archive.
It opens no simulator mapping, starts no recording and changes no source files.

Call `analyze_archive_coverage(archive, start_at=..., end_at=...,
segment_names=[...], max_gap_s=0.1)`. Supply UTC timestamps and exact closed
`samples-NNNNNN.jsonl.gz` filenames. `write_archive_coverage_report(report, new_dir)`
can preserve the result in a fresh private directory outside the source archive.
The chief should select and record the window before inspecting the result.

The same bounded audit is available through a chief-operated command:

```powershell
uv run python -m simcoach.cli.archive_coverage --archive "<explicit-archive>" `
  --start-at "<UTC-ISO-start>" --end-at "<UTC-ISO-end>" `
  --segment samples-000001.jsonl.gz --segment samples-000002.jsonl.gz `
  --out "<new-private-report-directory>"
```

Exit 0 means the selected scope completed its integrity checks and has a known
metric; it does not mean complete time coverage or rig readiness. Exit 1 retains
an incomplete, failed or unknown audit report. Invalid invocation is exit 2.
To include a report in a later visit outcome, use a new directory under that
visit's `coverage/`, then request a new `visit outcome` snapshot. This attaches
the report with its explicit window; it does not expand it to the whole visit.

The reported span measures intervals between verified original samples, clipped
to that fixed UTC window. Gaps above the declared bound contribute no span. Files
must pass manifest hashes, gzip/record checks, raw supported layout and timing
coherence. A collector-marked coverage break also blocks the bridge between
otherwise valid, adjacent segments. Both local timing deltas and
cumulative UTC-offset alignment are checked. A clock discontinuity makes the
mapping unknown instead of moving timestamps to make a better-looking result.

The remainder is unverified time, **not proof that data was lost**. The metric
does not establish driver presence, useful canonical capture or full simulator
update coverage. Repeated even sequence numbers may be valid captured bytes;
they do not count toward advancing-sequence spans. No participant data is emitted.

Read the per-segment results and limits alongside the numeric span. A bounded
partial audit may retain a conservative verified span; missing evidence or an
unstable time mapping produces null metrics. `PASS` means the selected integrity
checks passed, not that a whole Driver Visit was captured. The denominator never
shrinks to successful samples. Preserve failed or insufficient reports instead
of silently retrying with a shorter window or a more permissive gap.

Source identity and selected hashes are rechecked; ordinary later manifest
appends do not invalidate the closed selection. No partial/orphan segment is
promoted or repaired. Work limits bound file count, bytes and sample processing;
they cannot interrupt a blocked filesystem call. Existing raw captures remain
private. Synthetic tests and historical offline audits are not new live validation.

See the [archive audit schema](../schema/archive_coverage.md) for the exact metric,
timing tolerances, limits, provenance and unknown-result rules.

## Audit a declared visit interval

After explicit departure, the Chief can audit the visit's fixed clock window
using the already selected archive identity. Run this separately from visit
closure so file verification does not hold up the debrief.

```powershell
uv run python -m simcoach.cli.archive_coverage --visit "<closed-visit>" `
  --archive "<selected-archive>" --archive-sha256 "<selected-run-json-sha256>" `
  --out "<closed-visit>/coverage/<new-audit>"
```

If the host claimed an explicit arrival, the auditor verifies the retained
declaration and claim in the visit root and includes the earlier interval
automatically. For older visits where the Chief retained an earlier driver
declaration separately, `--arrival` accepts a reviewed JSON receipt with
`schema_version: 1`, `kind: reviewed_arrival_declaration`,
the terminal clock's `visit_id`, the original timezone-aware `at` and a short
`basis` from the Chief's review. Retain the original declaration separately.
Without either receipt, the scope starts at the host clock. Earlier time remains
unknown. The auditor requires a matching terminal clock and explicit departure.

The command freezes the whole UTC interval and candidate segments before
reading raw samples. Each tile contributes only within its own boundaries.
Unverified windows remain in the denominator, including prehost time and the
unsealed tail. A failed segment, incompatible clock anchors or changing source
can withhold the aggregate. Percentages are lower bounds for verified archive
spans. They do not measure seat occupancy, loss or useful Recorded Session time.
The report is available as a separately scoped attachment in a fresh visit outcome.
