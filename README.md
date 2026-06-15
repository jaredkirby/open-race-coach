# Open Race Coach

```
┌─┐┌─┐┌─┐
└─┘┴└─└─
```

Open Race Coach is a local-first post-session sim-racing telemetry coach. It records simulator sessions, validates them into durable artifacts, and runs deterministic analysis to produce up to one `Coaching Instruction` per `Analysis Run`.

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

Commands used in this repository:

- `uv run ruff check .` (style and static checks)
- `uv run pytest` (test suite)
- `uv run python scripts/validate_session.py data/sessions/<session_dir>` (validate finalized artifacts)
- `uv run python scripts/analyze.py data/sessions/<session_dir>` (run deterministic analysis)

## Typical workflow

1. Record a session from your simulator.
2. Validate that the finalized `Recorded Session` artifacts are complete.
3. Run deterministic analysis to produce a `Coach Report`.
4. Optionally run Coach Refinement for prose polish (model-assisted; not analysis).

You can inspect all produced files on disk; canonical outputs are under each `Recorded Session` and each `Analysis Run` directory.

## Record

### AMS2

In Automobilista 2, set shared memory to Project CARS 2 mode, start a session, then run:

```bash
uv run python scripts/record.py --sim ams2 --out data/sessions
```

Stop with `Ctrl+C` when the run is done.

### ACC

Start an Assetto Corsa Competizione session, then run:

```bash
uv run python scripts/record.py --sim acc --out data/sessions
```

### Shared-memory fixture capture (for adapter tests)

```bash
uv run python scripts/capture_acc_fixture.py --out tests/fixtures/acc --count 5
uv run python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5
```

Each capture writes page-level `.bin` dumps plus `.json` metadata sidecars.

## Validate

Validate captured AMS2 assumptions before trusting a new real-world setup:

```bash
uv run python scripts/validate_ams2.py
```

Then validate any finished `Recorded Session` before analysis:

```bash
uv run python scripts/validate_session.py data/sessions/<session_dir>
```

`validate_session.py` is where `session.yaml` completeness, artifact versions, and tick/lap invariants are checked as a hard gate.

## Analyze

Run deterministic analysis:

```bash
uv run python scripts/analyze.py data/sessions/<session_dir>
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

`session.yaml` is also copied at session level for convenience, but each `Analysis Run` is the durable canonical source.

## Coach Refinement (LLM layer only)

Deterministic analysis is separated from prose rewrites.

### API mode (requires billing)

```bash
uv run python scripts/analyze.py data/sessions/<session_dir> --coach --coach-mode api
```

Requires `OPENAI_API_KEY`.

### Manual ChatGPT mode

```bash
uv run python scripts/analyze.py data/sessions/<session_dir> --coach --coach-mode chatgpt
uv run python scripts/analyze.py data/sessions/<session_dir> --analysis-run <analysis_run_dir> --chatgpt-response <response.json>
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

Completed `Analysis Run` outputs are immutable.

## Decisions and design history

- Source of truth for domain behavior: [CONTEXT.md](/Users/jaredkirby/projects/new-work/open-race-coach/CONTEXT.md)
- Accepted decisions and explicit rejections: [docs/DECISIONS.md](/Users/jaredkirby/projects/new-work/open-race-coach/docs/DECISIONS.md)

## Contributing

Use short, evidence-backed changes. If you change implementation, update schemas, docs, and tests in lockstep.
