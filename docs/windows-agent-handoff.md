# Windows Agent Handoff: Open Race Coach

Task: prepare the Windows simulator rig, then perform the first AMS2 Minimal Clean-Lap Live Simulator Validation. This is a telemetry trust test. Live AMS2 and ACC validation is not established by the Mac tests or public fixtures.

Repository: https://github.com/jaredkirby/open-race-coach
Branch: `main`. Record the exact pulled commit in your evidence packet.

## 1. Find the checkout and read authority

Search the user's normal project directories for `open-race-coach/AGENTS.md` or `pyproject.toml` containing `name = "open-race-coach"`. Do not reuse the Mac's absolute paths. If there is no checkout, clone the repository into the user's normal project directory:

```powershell
git clone https://github.com/jaredkirby/open-race-coach.git
Set-Location open-race-coach
```

For an existing checkout, first run:

```powershell
git status --short --branch
git remote -v
git branch --show-current
git log -1 --oneline
```

Preserve local edits and recordings. If clean and on `main`, update with `git pull --ff-only origin main`; stop and inspect divergence or another branch before changing it. Never reset, clean, or overwrite local evidence to match this handoff.

Read `AGENTS.md`, `CONTEXT.md`, `README.md`, `docs/DECISIONS.md`, `schema/session_yaml.md`, `schema/tick.md`, `schema/lap_record.md`, and `docs/live-simulator-validation.md`. Read `schema/analysis_yaml.md` and `schema/selected_delta.md` before assessing analysis output. `plans/README.md` describes the browser editor work; it is not the Windows acceptance checklist.

## 2. Prepare without starting a simulator capture

Use a 64-bit Windows host with Git, uv, and Python 3.11 (uv can provision Python). These commands are for PowerShell, from the repository root. Check `$LASTEXITCODE` after each native command; stop on failure.

```powershell
git rev-parse HEAD
uv --version
uv sync --frozen --python 3.11
uv run python -c "import platform, struct; print(platform.platform()); print(platform.python_version()); print('pointer_bits:', struct.calcsize('P') * 8)"
uv run record --help
uv run validate-session --help
uv run analyze --help
uv run ruff check .
```

No OpenAI key or paid API call is needed. Do not run Coach Refinement for first acceptance.

Run the Python and static checks even if Node/Chrome is unavailable:

```powershell
$priorBrowserSkip = $env:ORC_SKIP_DOCS_INDEX_BROWSER_TEST
try {
    $env:ORC_SKIP_DOCS_INDEX_BROWSER_TEST = '1'
    uv run pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Python/static tests failed' }
} finally {
    $env:ORC_SKIP_DOCS_INDEX_BROWSER_TEST = $priorBrowserSkip
}
```

This explicitly skips browser execution; it is not a full-suite pass. Raw AMS2/ACC fixture tests also skip until reviewed raw fixtures exist. Record skip reasons with `uv run pytest tests/test_raw_fixtures.py -q -rs`.

For browser verification, use Node.js 22 or newer and Chrome/Chromium. The harness discovers standard Windows Chrome paths. For a custom installation, set `$env:ORC_CHROME_PATH` to its full executable path. With the skip variable unset, run:

```powershell
node --version
node tests/docs_index_browser_test.mjs
```

The browser emits its measured assertion count and checked viewports. A browser failure is separate from simulator validation, but must be reported.

## 3. File search map

| Need | Files |
| --- | --- |
| Windows interactive AMS2 gate | `scripts/validate_ams2.py`, `tests/test_validate_ams2.py` |
| Binary capture and sidecars | `scripts/capture_ams2_fixture.py`, `scripts/capture_acc_fixture.py`, `tests/fixtures/README.md` |
| Shared-memory layouts and parsing | `simcoach/adapters/ams2.py`, `simcoach/adapters/acc.py`, `simcoach/adapters/base.py`, `tests/test_adapters.py` |
| Capture lifecycle and lap eligibility | `simcoach/ingest/recorder.py`, `simcoach/ingest/session.py`, `tests/test_recorder.py`, `tests/test_session.py` |
| Artifact validation | `simcoach/validation.py`, `tests/test_validate_session.py` |
| Installed commands | `simcoach/cli/`, `pyproject.toml`, `tests/test_cli_entrypoints.py` |
| Deterministic analysis | `simcoach/analysis/run.py`, `simcoach/analysis/deltas.py`, `tests/test_analysis.py` |
| Recorded Sessions and reports | `data/sessions/` (ignored), each session's `analysis/` directories |
| Local raw candidates and evidence | `.scratch/fixtures/`, `.scratch/live-validation/` (ignored) |

With ripgrep installed, start with `rg --files simcoach scripts schema tests docs` and `rg -n 'lap_has_observed_boundaries|validate_recorded_session|mSequenceNumber|read_tick|read_session_info' simcoach scripts`. PowerShell `Get-ChildItem` and `Select-String` are sufficient alternatives.

## 4. Execute the live gates

Follow [Live Simulator Validation](live-simulator-validation.md) in order. Coordinate the interactive prompts with the driver; do not invent completion of driving steps. Start with AMS2, Project CARS 2 shared memory, a stable practice context, and two to three clean timed laps. Capture raw candidates into ignored scratch storage. Stop recording after observing the final lap-number transition.

Use the exact Recorded Session directory printed by `record`; do not automatically choose the newest folder, which may be an incomplete or separate capture. Validate that directory before running deterministic analysis. Keep `complete: false` captures for diagnosis. Never patch captured artifacts to make validation pass. Fix source code and collect a new Recorded Session when necessary.

ACC has an adapter and capture script but no equivalent interactive acceptance script here. Do not claim ACC validated after an AMS2 pass. AC remains deferred.

## 5. Known limits to investigate with evidence

- Metadata, invalidation flags, and ticks are read separately; snapshot coherence is not guaranteed. AMS2's even sequence check does not establish a stable before/after copy. Record suspicious lap-boundary or mixed-context data as a failure or uncertainty.
- Lap eligibility uses a 1% start tolerance and a following lap transition or explicit 100% sample. Check first/last fragments, skipped lap numbers, and simulator counter behavior against the raw capture. Derived lap timing uses captured endpoints, not a verified simulator timing feed.
- Coach Refinement has no transaction lock across its output files. Do not run concurrent refinements; it is excluded from this test.
- The CRT website uses demonstration data. It is not a live view of the Windows capture and is not proof of telemetry quality.

## 6. Return a useful evidence packet

Create a timestamped `.scratch/live-validation/ams2-YYYY-MM-DD-HHMM.md` using the runbook's template. Include commit, dirty status, Windows/Python/simulator versions, shared-memory setting, actual track/car, commands with exit codes, failures, skip reasons, and exact artifact paths. Record Recorded Session ID, tick/lap/Valid Lap counts, and Analysis Run statuses. Include the human review of inputs, units, lap counters, positions, and eligibility.

Report each live gate as PASS, FAIL, or NOT RUN, with evidence. A complete `insufficient_data` Analysis Run is acceptable for this first short capture; zero Valid Laps is not. Preserve failed runs and rerun into fresh artifact directories. Summarize confirmed findings separately from unresolved risks.

Raw shared-memory pages can include driver names. Keep raw captures and private logs local until reviewed. Share a concise sanitized result with the project coordinator; do not push `data/`, `.scratch/`, credentials, or unreviewed raw fixtures. Any source fix needs a focused regression test and a fresh live run before claiming the affected gate passes.
