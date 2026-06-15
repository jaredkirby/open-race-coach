# Open Race Coach

Open Race Coach records simulator telemetry into local files and later turns the data into at most one data-supported coaching instruction.

Public home: https://openracecoach.com

┌─┐┌─┐┌─┐
└─┘┴└─└─

## Status

Pre-alpha public-development seed. This repository is public for development visibility and review, not general use.

Phase 0, the Phase 1 AMS2 ingest foundation, Phase 2 deterministic analysis, Phase 3 Coach Refinement plumbing, and the ACC adapter scaffold are implemented. Open Race Coach has not yet been validated on live AMS2 or ACC rigs. Live AMS2/ACC validation still has to be run on Windows with the simulators configured for shared memory; until then, public fixtures and unit tests are compatibility evidence, not proof of live simulator support.

## Setup

```bash
uv sync
```

## Record AMS2

In Automobilista 2, set shared memory to Project CARS 2 mode, start a practice session, then run:

```bash
uv run python scripts/record.py --sim ams2 --out data/sessions
```

Stop recording with `Ctrl+C`. Open Race Coach attempts to finalize `session.yaml`, `ticks.parquet`, and `laps.jsonl`.

## Record ACC

Start an Assetto Corsa Competizione session, then run:

```bash
uv run python scripts/record.py --sim acc --out data/sessions
```

To capture raw ACC shared-memory page fixtures for adapter regression tests:

```bash
uv run python scripts/capture_acc_fixture.py --out tests/fixtures/acc --count 5
```

Each capture writes physics, graphics, and static `.bin` page dumps plus one `.json` sidecar.

## Validate AMS2 Struct Assumptions

Run this before trusting real recordings after an AMS2 update:

```bash
uv run python scripts/validate_ams2.py
```

The validator prints live values, asks you to perform simple checks, and fails if the sampled fields do not satisfy coarse sanity assertions: stationary pit speed near 0 m/s, throttle/brake response, lap increment at start/finish, deliberate cut invalidation, and smooth position movement.

## Capture AMS2 Fixture Bytes

After the validator passes on Windows, capture raw `$pcars2$` shared-memory snapshots for adapter regression fixtures:

```bash
uv run python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5
```

Each capture writes a `.bin` raw mmap dump plus a `.json` sidecar with the hash, struct size, important offsets, and decoded sanity fields.

## Analyze

Validate the finalized session artifacts first:

```bash
uv run python scripts/validate_session.py data/sessions/<session_dir>
```

```bash
uv run python scripts/analyze.py data/sessions/<session_dir>
```

This writes a deterministic Analysis Run under `<session_dir>/analysis/`, including `analysis.yaml`, `corner_segments.json`, `selected_delta.json`, and `coach_report.md`.

## Coach Refinement

OpenAI API mode uses API billing and requires `OPENAI_API_KEY`:

```bash
uv run python scripts/analyze.py data/sessions/<session_dir> --coach --coach-mode api
```

Manual ChatGPT mode writes a prompt for copy/paste and consumes no OpenAI API tokens:

```bash
uv run python scripts/analyze.py data/sessions/<session_dir> --coach --coach-mode chatgpt
uv run python scripts/analyze.py data/sessions/<session_dir> --analysis-run <analysis_run_dir> --chatgpt-response <response.json>
```

ChatGPT responses must be JSON only, either directly or inside one fenced JSON block.
