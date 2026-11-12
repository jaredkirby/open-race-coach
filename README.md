# Open Race Coach

```
┌─┐┌─┐┌─┐
└─┘┴└─└─
```

Open Race Coach is a local-first post-session sim-racing telemetry coach. It records simulator telemetry into durable `Recorded Session` artifacts, validates them, and runs deterministic analysis to produce up to one `Coaching Instruction` per `Analysis Run`.

Public home: https://openracecoach.com

## What this repo is (and is not)

Current status: `Pre-alpha public-development seed` on `main`.

- Implemented: AMS2 and ACC ingest scaffolding, deterministic analysis, and Coach Refinement plumbing.
- Not yet complete: broad live-rig usage expectations.
- Not yet proven on live rigs: AMS2/ACC live simulator validation. Public fixtures and unit tests are compatibility evidence only; they are not proof of real simulator trust.

If you need this for everyday analysis today, read this as "in-progress" rather than "ready for use."

## Setup

```bash
uv sync
```

The full test suite includes a headless browser check for `docs/index.html`. It requires Node.js 22 or newer and Chrome or Chromium. Set `ORC_CHROME_PATH` when the browser is not discoverable from the normal install locations. A static-only run can skip that check explicitly, but it is not the full suite:

```bash
ORC_SKIP_DOCS_INDEX_BROWSER_TEST=1 uv run pytest
```

Live simulator recording and raw shared-memory fixture capture require Windows, a configured simulator, and its shared-memory interface. This repository's public fixtures and unit tests do not prove Live Simulator Validation.

Use the [Live Simulator Validation runbook](docs/live-simulator-validation.md) when collecting evidence from AMS2 or ACC.

Windows agent: start with [Windows rig handoff](docs/windows-agent-handoff.md) for checkout, PowerShell setup, file search, verification, and the first live AMS2 test. ACC remains a separate validation task.

Installed commands used in this repository:

- `uv run ruff check .` (style and static checks)
- `uv run pytest` (test suite)
- `uv run record --sim ams2 --out data/sessions` (record a simulator session)
- `uv run validate-session data/sessions/<session_dir>` (validate finalized artifacts)
- `uv run analyze data/sessions/<session_dir>` (run deterministic analysis)

The `scripts/record.py`, `scripts/validate_session.py`, and `scripts/analyze.py` paths remain thin backwards-compatible wrappers. Prefer the installed commands for new usage.

## Typical workflow

1. Record a session from your simulator.
2. Validate that the finalized `Recorded Session` artifacts are complete.
3. Run deterministic analysis to produce a `Coach Report`.
4. Optionally run Coach Refinement for prose polish (model-assisted; not analysis).

You can inspect all produced files on disk; canonical outputs are under each `Recorded Session` and each `Analysis Run` directory.

## Record

### AMS2

Live AMS2 capture requires Windows. In Automobilista 2, set shared memory to Project CARS 2 mode, start a session, then run:

```bash
uv run record --sim ams2 --out data/sessions
```

Stop with `Ctrl+C` when the run is done.

### ACC

Live ACC capture requires Windows. Start an Assetto Corsa Competizione session, then run:

```bash
uv run record --sim acc --out data/sessions
```

### Shared-memory fixture capture (for adapter tests)

```bash
uv run python scripts/capture_acc_fixture.py --count 5
uv run python scripts/capture_ams2_fixture.py --count 5
```

Each capture writes page-level `.bin` dumps plus `.json` metadata sidecars under ignored `.scratch/fixtures/{acc,ams2}/` by default. Raw pages and sidecars can contain simulator, session, or machine-specific data. Review hashes, struct sizes, scenario labels, provenance, and privacy before sharing anything. Promote only a small reviewed `.bin` plus sidecar set to `tests/fixtures/` with an explicit `--out` path; do not publish unreviewed captures. No automated redaction tool is provided.

## Validate

Validate captured AMS2 assumptions before trusting a new real-world setup:

```bash
uv run python scripts/validate_ams2.py
```

Then validate any finished `Recorded Session` before analysis:

```bash
uv run validate-session data/sessions/<session_dir>
```

`simcoach.validation` is the shared hard gate used by both `validate-session` and deterministic analysis. It checks `session.yaml` completeness, artifact versions, and tick/lap invariants.

## Analyze

Run deterministic analysis:

```bash
uv run analyze data/sessions/<session_dir>
```

This writes a new `Analysis Run` under:

```
<session_dir>/analysis/<timestamp>_<reference_mode>/
```

Expected deterministic files:

- `analysis.yaml`
- `corner_segments.json`
- `selected_delta.json`
- `coach_report.md` (human-readable summary)

The `Analysis Run` directory is the durable canonical source for deterministic analysis outputs. The session-level `coach_report.md` is only a convenience copy of the latest completed Coach Report render. `session.yaml` remains the Recorded Session metadata artifact; it is not a copied Analysis Run artifact.

## Coach Refinement (LLM layer only)

Deterministic analysis is separated from prose rewrites.

### API mode (requires billing)

```bash
uv run analyze data/sessions/<session_dir> --coach --coach-mode api
```

Requires `OPENAI_API_KEY`.

### Manual ChatGPT mode

```bash
uv run analyze data/sessions/<session_dir> --coach --coach-mode chatgpt
uv run analyze data/sessions/<session_dir> --analysis-run <analysis_run_dir> --chatgpt-response <response.json>
```

ChatGPT responses must be JSON-only (plain JSON or one fenced JSON block).

## Core artifact contracts

- `schema/session_yaml.md`
- `schema/tick.md`
- `schema/lap_record.md`
- `schema/analysis_yaml.md`
- `schema/corner_segments.md`
- `schema/selected_delta.md`
- `schema/coach_response.md`

Each schema file is the contract for its artifact and should be kept aligned with implementation.

## Terms and operating rules

- `Recorded Session`: immutable capture unit with stable identity (`recorded_session_id`).
- `Analysis Run`: deterministic execution result for one `Recorded Session`.
- `Reference Lap`: selected baseline lap for comparison.
- `Comparison Lap`: valid laps from the target session compared against the reference.
- `Corner Segment`: numbered analysis segment used for loss classification.
- `Reportable Delta`: the selected, data-supported difference that can back a `Coaching Instruction`.
- `Coach Report`: deterministic report from analysis.
- `Coach Refinement`: optional prose-only rewrite layer.

Completed deterministic `Analysis Run` evidence is immutable: deterministic statuses, the Reference Lap, Corner Segments, Reportable Delta, and telemetry-derived values cannot be rewritten. Coach Refinement may add `coach_prompt.md`, add `coach_response.json`, update Coach Report prose, and update refinement metadata; it must not reinterpret or select different evidence. A pending manual refinement completes once. Use a new deterministic `Analysis Run` for a different Reference Lap or threshold policy.

## Decisions and design history

- Source of truth for domain behavior: [CONTEXT.md](CONTEXT.md)
- Accepted decisions and explicit rejections: [docs/DECISIONS.md](docs/DECISIONS.md)

## Contributing

Use short, evidence-backed changes. If you change implementation, update schemas, docs, and tests in lockstep.
