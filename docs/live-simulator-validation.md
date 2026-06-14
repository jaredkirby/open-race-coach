# Live Simulator Validation Runbook

This runbook is for the first AMS2 Minimal Clean-Lap Live Simulator Validation. It proves Open Race Coach can observe, preserve, validate, and analyze a real simulator session from a configured rig before judging coaching quality.

This runbook does not validate ACC, realistic stint behavior, Coach Refinement, or whether a Coaching Instruction is useful.

## Scenario

- Simulator: AMS2
- Shared memory mode: Project CARS 2
- Session type: practice
- First target: Formula Trainer Advanced at Brands Hatch Indy, or the closest installed equivalent with stable metadata and simple clean laps
- Driving target: 2 to 3 clean timed laps with no intentional pause, pit entry, replay, restart, setup/context change, or deliberate invalidation

Use the deliberate invalidation only during the shared-memory validator gate, not during the canonical Recorded Session.

## Pass Criteria

First Live Simulator Validation passes when all of these are true:

- `scripts/validate_ams2.py` passes on the Windows rig.
- Raw AMS2 fixture candidates are captured as `.bin` files with `.json` sidecars.
- One canonical Recorded Session finalizes with `complete: true` and `failure_reason: null`.
- `scripts/validate_session.py` passes for the canonical Recorded Session.
- Deterministic analysis exits cleanly and writes a complete Analysis Run.
- At least one Valid Lap exists.
- Human review finds no obvious false telemetry, such as impossible speed, stuck controls, broken lap numbers, or nonsensical track/car labels.

`analysis_status=insufficient_data` can still pass this validation when `run_status=complete` and the reason is honest. `run_status=failed` fails validation. Do not run Coach Refinement during first acceptance.

## Gate 1: Environment

On the Windows rig:

1. Start AMS2.
2. Enable Project CARS 2 shared memory mode.
3. Start a practice session with the selected track and car.
4. Install dependencies from the repository root:

```bash
uv sync
```

If `uv sync` fails, stop. The rig is not ready for validation.

## Gate 2: Shared Memory

Run:

```bash
uv run python scripts/validate_ams2.py
```

Follow each interactive prompt exactly:

- stationary in pit
- released throttle and brake
- full throttle
- full brake
- before start/finish
- after crossing start/finish
- deliberate cut after the sim marks the lap invalid
- smooth driving sample

If this gate fails, do not record a canonical session. Record the failure in the evidence packet and fix the adapter or setup first.

## Gate 3: Raw Fixture Candidates

After the shared-memory validator passes, capture raw AMS2 fixture candidates:

```bash
uv run python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5
```

Review before committing any capture:

- Sidecar JSON exists for each `.bin`.
- `sha256` is present.
- `struct_size_bytes` matches the expected adapter struct size.
- `raw_path` points to the matching `.bin`.
- `sim` is `ams2`.
- `shared_memory_name` is `$pcars2$`.
- Important offsets are present.
- Snapshot labels and sanity fields describe the intended scenario.
- No obvious private or machine-specific junk is present beyond intended simulator metadata.

Commit only a small representative set after review. Keep unreviewed captures local.

## Gate 4: Canonical Recorded Session

Restart or reset the AMS2 practice context if needed so the canonical capture is clean. Then run:

```bash
uv run python scripts/record.py --sim ams2 --out data/sessions
```

Drive 2 to 3 clean timed laps. Stop recording with `Ctrl+C` after the final lap has completed.

Do not intentionally invalidate a lap during this gate. Do not enter pits, pause, replay, restart, change track, change car, or change session type.

If recording fails or finalizes as incomplete, stop. Record the session path and failure in the evidence packet.

## Gate 5: Artifact Validation

Run:

```bash
uv run python scripts/validate_session.py data/sessions/<session_dir>
```

This gate must pass for first validation. If it fails, do not run coaching or judge product quality from the session.

## Gate 6: Deterministic Analysis

Run:

```bash
uv run python scripts/analyze.py data/sessions/<session_dir>
```

Review the created `analysis/<timestamp>_best/analysis.yaml`, `selected_delta.json`, `corner_segments.json`, and `coach_report.md`.

This gate passes when deterministic analysis completes and the Analysis Run is complete. A non-reportable result can pass. A failed run cannot pass.

## Gate 7: Human Review

Inspect the artifacts for obvious telemetry falsehoods:

- track and car labels match the scenario
- tick count and lap count are plausible
- speed, throttle, brake, steering, gear, and rpm move plausibly
- lap numbers increment correctly
- Lap Progress moves through the lap instead of freezing or jumping wildly
- Valid Lap decisions are plausible for the clean laps
- Coach Report does not imply evidence that artifacts do not contain

If these checks fail, treat it as telemetry validation failure, not coaching feedback.

## Evidence Packet Template

Create a local evidence packet under `.scratch/live-validation/`, for example:

```text
.scratch/live-validation/ams2-YYYY-MM-DD.md
```

Use this template:

````md
# AMS2 Live Simulator Validation Evidence

Date:
Rig:
OS:
AMS2 version/build:
Open Race Coach commit:
Shared memory mode: Project CARS 2

## Scenario

Simulator: AMS2
Track:
Car:
Session type: practice
Driving target: 2 to 3 clean timed laps

## Gate Results

- Environment:
- Shared Memory:
- Raw Fixture Candidates:
- Canonical Recorded Session:
- Artifact Validation:
- Deterministic Analysis:
- Human Review:

## Commands

```bash
uv sync
uv run python scripts/validate_ams2.py
uv run python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5
uv run python scripts/record.py --sim ams2 --out data/sessions
uv run python scripts/validate_session.py data/sessions/<session_dir>
uv run python scripts/analyze.py data/sessions/<session_dir>
```

## Fixture Candidates

Paths:
Review result:
Commit decision:

## Recorded Session

Path:
Recorded Session ID:
Complete:
Failure reason:
Tick count:
Lap count:
Valid lap count:

## Analysis Run

Path:
run_status:
analysis_status:
Reference mode:
Reportable:

## Human Review Notes

## Explicitly Not Tested

- ACC
- realistic stint behavior
- pits, pauses, replay, restarts, setup changes
- Coach Refinement
- coaching quality
````
