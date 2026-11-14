# Agent Instructions

You are working in the Open Race Coach repository.

Open Race Coach is a local-first sim-racing project evolving toward a personal race chief. Its implemented post-session coaching core records telemetry into durable local artifacts, runs deterministic analysis, and emits at most one data-supported Coaching Instruction per Analysis Run. Read `docs/race-chief-direction.md` to distinguish accepted direction, proposed architecture and implemented behavior.

## Operating Style

Be precise, direct, skeptical, and evidence-bound. Do not soften conclusions to avoid discomfort. If a premise is wrong, say so and explain why. Do not praise the user's question or validate weak premises.

Do not hallucinate simulator behavior, telemetry schemas, OpenAI API details, or live validation status. If the repo does not contain evidence for a claim, say that directly and put unresolved decisions in the right place.

Use explicit confidence levels when making judgments: high, moderate, low, or unknown.

## Source Of Truth

Read these files before significant changes:

- `CONTEXT.md` for domain language and terms agents must use consistently.
- `docs/DECISIONS.md` for accepted design decisions and rejected alternatives.
- `docs/race-chief-direction.md` for the current product direction and unresolved implementation choices.
- `docs/validation/ams2-2026-11-14.md` for scoped live results and remaining reliability gaps.
- `schema/*.md` for durable artifact contracts.
- `README.md` for supported workflows and user-facing commands.
- Relevant tests under `tests/` before changing behavior.

Keep docs, schemas, tests, and implementation aligned. Do not change one layer and leave the others stale.

## Project Boundaries

The Python package namespace is still `simcoach`; the public product name is Open Race Coach. Do not mechanically rename imports or package paths unless explicitly asked.

Deterministic analysis owns statuses, selected deltas, evidence, and reportability. Coach Refinement may rewrite prose only; it must not reinterpret telemetry or select different evidence.

Manual ChatGPT mode and OpenAI API mode are separate workflows. Do not mix their state machines, artifacts, or retry semantics.

Live AMS2 and ACC validation require Windows plus configured simulators. Public fixtures and unit tests are not proof of live simulator validation.

Protect driving time: prepare automated and replay checks before requesting physical validation. Reuse accepted live evidence within its scope; rerun affected checks when configuration changes or new uncertainty warrants them. Preserve failed artifacts and output reviews. Keep raw telemetry and private rig details out of shared docs.

The historical v0 spec's post-session limits describe that bootstrap release. Live radio, fast event reactions and durable specialists are now product direction, not existing implementation. A Live Reaction must not bypass deterministic ownership of Coaching Instructions. Runtime, subscription and audio capabilities need current documentation and local verification before being claimed.

## Implementation Rules

Use existing repo vocabulary exactly: Recorded Session, Analysis Run, Reference Lap, Comparison Lap, Corner Segment, Reportable Delta, Lap Loss Cause, Coach Report, Coach Refinement, and Coaching Instruction.

Preserve artifact immutability. Completed Analysis Runs are timestamped canonical outputs and must not be overwritten.

Preserve stable identity semantics. Recorded Session IDs and Lap IDs are identity; filesystem paths are locators.

Write durable artifacts atomically using the existing helpers and patterns. Never leave partial files looking canonical.

Avoid broad refactors unless they are necessary for the requested change. Keep changes scoped to the current behavior and update tests near the affected code.

## Commands

Set up dependencies:

```bash
uv sync
```

Run the test suite:

```bash
uv run pytest
```

Run linting:

```bash
uv run ruff check .
```

Validate a finalized Recorded Session:

```bash
uv run python scripts/validate_session.py data/sessions/<session_dir>
```

Run deterministic analysis:

```bash
uv run python scripts/analyze.py data/sessions/<session_dir>
```

## Git Hygiene

Before editing, inspect the worktree and do not overwrite unrelated dirty files. Stage and commit only files you changed intentionally.

Generated or live-capture artifacts should not be added casually. If adding fixtures, include provenance and make clear whether they are raw simulator bytes, public sample data, or synthetic test data.

This repository is public. Before committing or proposing publication, review the exact staged diff and new files for credentials, participant names, private machine paths, raw telemetry, copied chat/log content and unsupported claims. Do not use broad staging for a mixed worktree. Ignored files are not a security boundary; do not force-add private artifacts. Keep detailed personal operating notes and exploratory vendor/account research local; publish concise requirements, reproducible findings and clearly labeled proposals. Reverify any provider entitlement, pricing or terms claim before publishing it. Report review limits honestly; a pattern scan is not proof that a change is safe or correct.
