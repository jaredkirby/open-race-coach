"""Observe one explicit archive or replay; journal fuel decisions without audio."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.supervisor import FuelPlan, Journal, Supervisor, source_identity, strict_json


class SourceLock:
    """One cooperating observer per archive in a shared output root."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        if path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("archive_already_observed") from None

    def close(self):
        self.handle.close()


def timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp_requires_utc")
    return stamp.timestamp()


def replay(path, supervisor):
    """Historical virtual time only. No audio or live simulator connection."""
    start = None
    result = None
    rows = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = strict_json(line)
            now = timestamp(row["at"])
            if start is None:
                start = now
            result = supervisor.observe(row["status"], source_id=row["source_id"],
                                        now=now, elapsed=now - start)
            if "approval" in row:
                confirmed_view = row["approval"].get("player_view_confirmed", False)
                supervisor.arm(cockpit_litres=row["approval"]["cockpit_litres"],
                               verified_at=now, now=now, source_id=row["source_id"],
                               solo_player_confirmed=not confirmed_view,
                               player_view_confirmed=confirmed_view,
                               participant_index=row["approval"].get("participant_index"),
                               note="REPLAY TEST ONLY: " + row["approval"]["note"])
                result = dict(result, approval=supervisor.approval_result,
                              fuel_state=supervisor.fuel_state,
                              supervision_loss=supervisor.supervision_loss)
            rows += 1
    if not rows:
        raise ValueError("empty_replay")
    return dict(mode="replay", rows=rows, final=result, audio="disabled")


def monitor(archive, run, supervisor, *, duration, approval=None,
            clock=time.time, elapsed=time.perf_counter, sleep=time.sleep):
    start = elapsed()
    result = None
    approval_pending = approval is not None
    while elapsed() - start < duration and not (run / "stop").exists():
        now = clock()
        try:
            identity = source_identity(archive)
            status = strict_json((archive / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            identity, status = "unreadable", None
        result = supervisor.observe(status, source_id=identity, now=now,
                                    elapsed=elapsed() - start)
        if approval_pending and supervisor.current:
            supervisor.arm(now=now, **approval)
            approval_pending = False
            result = dict(result, approval=supervisor.approval_result,
                          fuel_state=supervisor.fuel_state,
                          supervision_loss=supervisor.supervision_loss)
        write_status(run / "status.json", dict(mode="shadow", status="running", **result))
        sleep(0.25)
    return dict(mode="shadow", final=result, audio="disabled")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--replay", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(".scratch/race-chief"))
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--cockpit-litres", type=float)
    parser.add_argument("--verified-at", help="UTC ISO time of actual loaded-fuel check")
    confirmation = parser.add_mutually_exclusive_group()
    confirmation.add_argument("--confirm-solo-player", action="store_true")
    confirmation.add_argument("--confirm-player-view", action="store_true")
    parser.add_argument("--participant-index", type=int)
    parser.add_argument("--approval-note")
    args = parser.parse_args(argv)
    if not 0 < args.duration <= 86400:
        parser.error("--duration must be finite, positive and at most one day")
    approval_flags = (args.cockpit_litres is not None, args.verified_at is not None,
                      args.confirm_solo_player or args.confirm_player_view,
                      args.approval_note is not None)
    if args.confirm_player_view != (args.participant_index is not None):
        parser.error("--confirm-player-view requires --participant-index together")
    if any(approval_flags) and (not all(approval_flags) or args.replay):
        parser.error("live approval requires all four approval options and --archive")
    try:
        plan = FuelPlan.from_dict(strict_json(args.plan.read_text(encoding="utf-8")))
        identity = source_identity(args.archive) if args.archive else None
        approval = None if not any(approval_flags) else dict(
            cockpit_litres=args.cockpit_litres, verified_at=timestamp(args.verified_at),
            source_id=identity, solo_player_confirmed=args.confirm_solo_player,
            note=args.approval_note, player_view_confirmed=args.confirm_player_view,
            participant_index=args.participant_index,
        )
    except (OSError, ValueError, TypeError, KeyError):
        parser.error("invalid plan, archive identity or approval; inspect local source files")
    lock = None
    if args.archive:
        # Path key also protects against run.json replacement while monitoring.
        key = hashlib.sha256(os.path.normcase(str(args.archive.resolve())).encode()).hexdigest()
        try:
            lock = SourceLock(args.out / "locks" / key)
        except (OSError, RuntimeError):
            parser.error("cannot acquire archive observer lock")
    run = args.out / f"watch-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    run.mkdir(parents=True)
    journal = Journal(run / "events.sqlite3")
    supervisor = Supervisor(plan, journal)
    mode = "replay" if args.replay else "shadow"
    journal.append(dict(event="started", at=time.time(), mode=mode,
                        source_id=identity, plan=asdict(plan), audio="disabled"))
    print(f"Race watch ({mode}, no audio): {run.resolve()}", flush=True)
    outcome, code = "failed", 1
    summary = dict(mode=mode, audio="disabled")
    try:
        summary = replay(args.replay, supervisor) if args.replay else monitor(
            args.archive, run, supervisor, duration=args.duration, approval=approval,
        )
        outcome, code = "complete", 0
    except KeyboardInterrupt:
        outcome, code = "interrupted", 1
    except Exception as exc:
        # Full diagnostics remain in this explicitly private run, not portable status.
        summary["error_type"] = type(exc).__name__
        with (run / "failure.txt").open("x", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
    finally:
        try:
            journal.append(dict(event="stopped", at=time.time(), outcome=outcome))
            write_status(run / "status.json", dict(status=outcome, **summary))
        finally:
            journal.close()
            if lock:
                lock.close()
    print(json.dumps(dict(status=outcome, **summary), allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
