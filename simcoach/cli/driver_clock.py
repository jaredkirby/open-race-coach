"""Measure elapsed wall time and control ranges during an AMS2 test."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from simcoach.adapters.ams2 import SharedMemory

CONTROL_FIELDS = {
    "speed": "mSpeed",
    "throttle": "mThrottle",
    "brake": "mBrake",
    "steering": "mSteering",
    "gear": "mGear",
    "rpm": "mRpm",
}


def activity(snapshot: SharedMemory | None, *, stale: bool) -> str:
    from simcoach.adapters import ams2

    if snapshot is None or snapshot.mVersion != ams2.SHARED_MEMORY_VERSION or stale:
        return "unknown"
    if snapshot.mGameState in {
        ams2.GAME_FRONT_END, ams2.GAME_INGAME_INMENU_TIME_TICKING,
        ams2.GAME_INGAME_PAUSED, ams2.GAME_FRONT_END_REPLAY, ams2.GAME_INGAME_REPLAY,
    }:
        return "not_driving"
    if snapshot.mGameState != ams2.GAME_INGAME_PLAYING:
        return "unknown"
    speed = snapshot.mSpeed
    if not math.isfinite(speed) or speed < 0:
        return "unknown"
    if speed <= 1:
        return "not_driving"
    return "pit_moving" if snapshot.mPitMode != ams2.PIT_MODE_NONE else "driving"


def interval_activity(seconds: float, previous: str, current: str) -> str:
    if seconds > 1 or "unknown" in (previous, current):
        return "unknown"
    return current if previous == current else "transition"


def save_output(run: Path, source: Path, verdict: str, note: str) -> Path:
    """Preserve the exact reviewed output, including failed or poor results."""
    from simcoach.utils.llm_logger import configure_logging

    configure_logging(run / "logs")
    from simcoach.ingest.session import atomic_write_text

    content = source.read_bytes()
    message = content.decode("utf-8-sig")
    receipt = {
        "schema_version": 1,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "verdict": verdict,
        "note": note,
        "source": str(source.resolve()),
        "sha256": hashlib.sha256(content).hexdigest(),
        "output": message,
    }
    destination = run / "reviews" / f"{uuid4().hex}.json"
    atomic_write_text(destination, json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    return destination


def track(run: Path, *, adapter=None, monotonic=time.monotonic, sleep=time.sleep) -> int:
    # Configure before imports that initialize loggers. Each CLI run is a fresh process.
    from simcoach.utils.llm_logger import configure_logging

    configure_logging(run / "logs")
    from simcoach.adapters.ams2 import AMS2Adapter
    from simcoach.ingest.session import atomic_write_text

    if adapter is None:
        adapter = AMS2Adapter()
    totals: Counter[str] = Counter()
    pending: Counter[str] = Counter()
    controls: dict[str, list[float]] = {}
    start = previous_at = last_sequence_at = last_saved_at = monotonic()
    started_at = datetime.now(UTC).isoformat()
    previous, sequence = "unknown", None
    status = "interrupted"

    def checkpoint(observed_at: float, recorded_status: str) -> None:
        updated_at = datetime.now(UTC).isoformat()
        timeline.write(json.dumps({
            "at": updated_at, "offset_s": observed_at - start,
            "activity_s": dict(pending), "status": recorded_status,
            "controls_min_max": controls,
        }) + "\n")
        timeline.flush()
        pending.clear()
        summary = {
            "schema_version": 1,
            "status": recorded_status,
            "started_at": started_at,
            "updated_at": updated_at,
            "elapsed_s": round(sum(totals.values()), 3),
            "activity_s": {key: round(value, 3) for key, value in sorted(totals.items())},
            "controls_min_max": controls,
        }
        atomic_write_text(run / "clock.json", json.dumps(summary, indent=2) + "\n")

    with (run / "timeline.jsonl").open("x", encoding="utf-8") as timeline:
        checkpoint(start, "running")
        try:
            adapter.connect()
            while not (run / "stop").exists():
                snapshot = adapter._read_snapshot()
                now = monotonic()
                if snapshot is not None and snapshot.mSequenceNumber != sequence:
                    sequence, last_sequence_at = snapshot.mSequenceNumber, now
                current = activity(snapshot, stale=now - last_sequence_at > 2)
                elapsed = now - previous_at
                bucket = interval_activity(elapsed, previous, current)
                totals[bucket] += elapsed
                pending[bucket] += elapsed
                if snapshot is not None and current == "driving":
                    for name, field in CONTROL_FIELDS.items():
                        value = getattr(snapshot, field)
                        if math.isfinite(value):
                            bounds = controls.setdefault(name, [value, value])
                            bounds[:] = [min(bounds[0], value), max(bounds[1], value)]
                previous, previous_at = current, now
                if now - last_saved_at >= 1:
                    checkpoint(now, "running")
                    last_saved_at = now
                sleep(0.2)
            status = "complete"
        except KeyboardInterrupt:
            status = "complete"
        except Exception:
            status = "failed"
            raise
        finally:
            adapter.disconnect()
            now = monotonic()
            totals["unknown"] += now - previous_at
            pending["unknown"] += now - previous_at
            checkpoint(now, status)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(".scratch/live-validation"))
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--stop", type=Path, metavar="RUN", help="Finish an existing clock")
    actions.add_argument("--review", type=Path, metavar="RUN", help="Save a review for a clock run")
    parser.add_argument("--output", type=Path, help="UTF-8 output file to preserve with a review")
    parser.add_argument("--verdict", choices=("pass", "fail", "incomplete", "unreviewed"))
    parser.add_argument("--note", default="", help="What the output got right or wrong")
    args = parser.parse_args()
    if args.review and (args.output is None or args.verdict is None):
        parser.error("--review requires --output and --verdict")
    if not args.review and (args.output or args.verdict or args.note):
        parser.error("--output, --verdict, and --note require --review")
    existing = args.stop or args.review
    if existing:
        summary = existing / "clock.json"
        if not summary.is_file():
            parser.error(f"No clock.json in {existing}")
    if args.stop:
        (args.stop / "stop").touch()
        print(f"Stop requested: {args.stop.resolve()}")
        return 0
    if args.review:
        try:
            result = save_output(args.review, args.output, args.verdict, args.note)
        except (OSError, UnicodeError) as exc:
            parser.error(str(exc))
        print(f"Review saved: {result.resolve()}")
        return 0
    if sys.platform != "win32":
        parser.error("AMS2 driver clock requires Windows")
    run = args.out / f"driver-clock-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    run.mkdir(parents=True)
    print(f"Driver clock: {run.resolve()}", flush=True)
    return track(run)


if __name__ == "__main__":
    raise SystemExit(main())
