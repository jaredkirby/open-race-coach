"""Owned recorder child with cooperative stop, lease and exact output inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import threading
import time
import traceback
from pathlib import Path

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.json_read import read_with_retry
from simcoach.live.recording_storage import RecordingStorage, storage_deadline
from simcoach.live.supervisor import strict_json
from simcoach.live.worker_status import WorkerStatusPublisher


def lease_valid(path: Path, now: float | None = None, *, max_age=5.0, diagnostic=None):
    """Validate against post-read time; explicit time is for deterministic checks.

    A publisher can replace the heartbeat while it is being read. Sampling time
    before that read can falsely reject the new heartbeat as future-dated.
    Missing/unreadable, malformed, truly future and expired leases still fail.
    """
    reads = []
    reason, age, error_type = None, None, None
    try:
        lease = read_with_retry(lambda: strict_json(path.read_text(encoding="utf-8")),
                                role="coordinator_heartbeat", diagnostic=reads.append)
        if now is None:
            now = time.time()
        at = lease["at"]
        if (type(now) not in (int, float) or not math.isfinite(now)
                or type(lease.get("schema_version")) is not int or lease["schema_version"] != 1
                or type(at) not in (int, float) or not math.isfinite(at)):
            reason = "invalid_lease"
        else:
            age = now - at
            reason = "future_lease" if age < 0 else "lease_expired" if age >= max_age else None
    except OSError as exc:
        reason, error_type = "lease_read_unavailable", type(exc).__name__
    except (ValueError, TypeError, KeyError) as exc:
        reason, error_type = "invalid_lease", type(exc).__name__
    if diagnostic is not None:
        diagnostic(dict(schema_version=1, at=time.time() if now is None else now,
                        valid=reason is None, reason=reason, age_s=age,
                        error_type=error_type, reads=reads))
    return reason is None


def local_path(path: Path):
    """Use Windows extended paths internally; keep ordinary paths in locators."""
    resolved = str(path.resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        prefix = "\\\\?\\UNC\\" if resolved.startswith("\\\\") else "\\\\?\\"
        resolved = prefix + (resolved[2:] if resolved.startswith("\\\\") else resolved)
    return Path(resolved)


def close_recordings(run: Path, sessions, *, previous=None):
    from simcoach.analysis.run import analyze_session
    from simcoach.validation import validate_recorded_session

    # A surviving intent means the previous effect might have completed; never guess/retry.
    with (run / "closure-intent.json").open("x", encoding="utf-8") as handle:
        intent = dict(schema_version=1, at=time.time(), sessions=[str(p) for p in sessions])
        json.dump(intent, handle)
        handle.flush()
        os.fsync(handle.fileno())
    rows = []
    for path in sessions:
        if previous and str(path) in previous:
            # Checkpoint owns this effect, including failed/uncertain attempts.
            # Never repeat an Analysis Run automatically at departure.
            rows.append(dict(previous[str(path)]))
            write_status(run / "closure-progress.json", dict(schema_version=1, recordings=rows))
            continue
        row = dict(path=str(path), validation="FAIL", analysis="NOT RUN", summary=None,
                   analysis_path=None, error_type=None)
        try:
            row["summary"] = validate_recorded_session(local_path(path))
            row["validation"] = "PASS"
        except Exception as exc:
            row["error_type"] = type(exc).__name__
        else:
            try:
                result = analyze_session(local_path(path))
                row["analysis_path"] = str(result)
                row["analysis"] = "PASS"
            except Exception as exc:
                row["analysis"] = "FAIL"
                row["error_type"] = type(exc).__name__
        rows.append(row)
        write_status(run / "closure-progress.json", dict(schema_version=1, recordings=rows))
    owned_root = run / "sessions"
    known = {path.name for path in sessions}
    unregistered = [str(p) for p in owned_root.iterdir() if p.name not in known] \
        if owned_root.is_dir() else []
    closure = dict(schema_version=1, recordings=rows, closed_at=time.time(),
                   unregistered_artifacts=unregistered)
    write_status(run / "closure.json", closure)
    lines = ["# Visit recording results", "",
             "These are canonical output results; the chief still reviews the debrief.", ""]
    if not rows:
        lines.append("Recording did not start. No driving or coaching result is established.")
    if unregistered:
        lines.append("Additional unregistered capture artifacts require chief inspection.")
    for row in rows:
        lines.append(f"- Capture validation: {row['validation']}; analysis: {row['analysis']}.")
        if row["summary"]:
            lines.append(f"  {row['summary']['valid_lap_count']} Valid Laps recorded.")
        if row["analysis_path"]:
            report = Path(row["analysis_path"]) / "coach_report.md"
            lines.extend(["", report.read_text(encoding="utf-8"), ""])
    temporary = run / "debrief.md.partial"
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.rename(run / "debrief.md")
    return closure


def fixture_adapter(path: Path):
    from simcoach.adapters.base import NormalizedTick

    value = strict_json(path.read_text(encoding="utf-8"))

    class Replay:
        sim_name = "ams2"

        def __init__(self):
            self.index = 0

        def connect(self):
            pass

        def disconnect(self):
            pass

        def read_session_info(self):
            return dict(value["info"])

        def read_tick(self):
            if self.index == len(value["ticks"]):
                return None
            result = NormalizedTick(**value["ticks"][self.index])
            self.index += 1
            return result

    return Replay()


def work(run: Path, lock_root: Path, *, fixture: Path | None = None, startup_timeout=15.0):
    from simcoach.live.visit_recovery import probe_process
    from simcoach.live.visit_release import active_binding
    from simcoach.utils.llm_logger import configure_logging

    # The Windows venv launcher PID can differ from this actual recorder owner.
    try:
        identity = probe_process(os.getpid())
    except Exception as exc:
        identity = dict(pid=os.getpid(), state="unknown", created_100ns=None,
                        reason=f"probe_failed_{type(exc).__name__}")
    write_status(run / "worker-process.json", dict(schema_version=1, at=time.time(),
            process=identity, source_release=active_binding()))
    configure_logging(run / "logs")
    from simcoach.ingest.recorder import Recorder, RecorderConfig

    lease = run.parent / "heartbeat.json"
    stop = threading.Event()
    errors = []
    stop_reason = None
    lease_diagnostic = None
    startup_deadline = time.perf_counter() + startup_timeout
    if fixture:
        adapter = fixture_adapter(fixture)
    else:
        from simcoach.adapters.ams2 import AMS2Adapter
        adapter = AMS2Adapter()
    config = RecorderConfig(
        out_dir=local_path(run / "sessions"), metadata_stable_s=0 if fixture else 1,
        tick_rate_hz=10000 if fixture else 60, tick_missing_grace_s=0 if fixture else 2,
        continuous_source_gaps=fixture is None,
    )
    recorder = Recorder(adapter, config, stop_requested=stop.is_set)
    from simcoach.live.stint_checkpoint import StintCheckpoints
    checkpoints, checkpoint_error = None, None
    try:
        checkpoints = StintCheckpoints(run, recorder)
    except (OSError, ValueError) as exc:
        checkpoint_error = type(exc).__name__
    thread = None
    status_publisher = WorkerStatusPublisher(run, write_status)
    storage_probe = (RecordingStorage(clock=lambda: time.time(),
                                      elapsed=lambda: time.perf_counter())
                     if fixture is None else None)
    storage = None

    def check_storage():
        nonlocal storage
        if storage_probe is None:
            return True
        try:
            destination = config.out_dir if config.out_dir.is_dir() else config.out_dir.parent
        except OSError:
            destination = config.out_dir
        storage = storage_probe.poll(destination)
        return storage_deadline(storage, now=time.time(), elapsed=time.perf_counter()) is not None

    def capture():
        try:
            recorder.run()
        except BaseException as exc:
            errors.append(type(exc).__name__)
            (run / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")

    def status(state):
        return status_publisher.publish(dict(
            schema_version=1, status=state, updated_at=time.time(), pid=os.getpid(),
            ticks_observed=recorder.ticks_observed,
            sessions=[str(p) for p in recorder.session_paths],
            active_ticks=recorder.active_ticks,
            intake_active=recorder.intake_active,
            tick_age_s=(time.perf_counter() - recorder.last_tick_at
                        if recorder.last_tick_at is not None else None),
            stop_reason=stop_reason, error_type=errors[0] if errors else None,
            lease_diagnostic=lease_diagnostic,
            recording_storage=storage,
            mode="replay" if fixture else "live",
            continuous_source_gaps=config.continuous_source_gaps,
            capture_phase=getattr(recorder, "capture_phase", None),
            source_gap_reason=getattr(recorder, "source_gap_reason", None),
            source_gap_count=getattr(recorder, "source_gap_count", None),
            checkpoints=checkpoints.snapshot() if checkpoints else dict(error=checkpoint_error),
        ))

    def check_lease():
        nonlocal lease_diagnostic
        observations = []
        valid = lease_valid(lease, diagnostic=observations.append)
        observation = observations[-1]
        if not valid or observation["reads"]:
            lease_diagnostic = observation
            try:
                with (run / "lease-diagnostics.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(observation, allow_nan=False) + "\n")
                write_status(run / "lease-diagnostic.json", observation)
            except OSError as exc:
                # Diagnostics cannot turn a recovered read into a capture failure.
                lease_diagnostic = dict(observation, publication_error=type(exc).__name__)
        return valid

    lock = SourceLock(lock_root / "capture.lock")
    try:
        if not check_storage():
            stop_reason = ("recording_disk_reserve" if storage["state"] == "low"
                           else "recording_storage_unavailable")
            status("failed")
            return 1
        status("starting")
        if not check_lease():
            stop_reason = "coordinator_lease_unavailable"
            stop.set()
        thread = threading.Thread(target=capture, name="visit-recorder")
        thread.start()
        while thread.is_alive():
            if recorder.ticks_observed == 0 and time.perf_counter() >= startup_deadline:
                stop_reason = stop_reason or "first_tick_timeout"
                stop.set()
            if (run.parent / "stop").exists():
                stop_reason = stop_reason or "visit_stop_requested"
                stop.set()
            if not check_lease():
                reason = lease_diagnostic["reason"]
                stop_reason = stop_reason or (
                    "coordinator_lease_expired" if reason == "lease_expired"
                    else f"coordinator_{reason}")
                stop.set()
            storage_ready = not stop.is_set() and check_storage()
            if not stop.is_set() and storage is not None and storage["state"] == "low":
                stop_reason = "recording_disk_reserve"
                stop.set()
            if checkpoints:
                checkpoints.tick(admission_block=None if storage_ready
                                 else "capture_unavailable_or_recording_storage_unverified")
            status("closing" if stop.is_set() else "recording")
            thread.join(timeout=0.2)
        status("closing")
        if checkpoints:
            checkpoints.finish()
        closure = close_recordings(run, recorder.session_paths, previous=checkpoints.rows) \
            if checkpoints and checkpoints.rows else close_recordings(run, recorder.session_paths)
        failed = (stop_reason in {"first_tick_timeout", "recording_disk_reserve"}
                  or bool(errors)) or bool(
            closure["unregistered_artifacts"]) or any(
            r["validation"] != "PASS" or r["analysis"] != "PASS" for r in closure["recordings"])
        if not recorder.session_paths:
            failed = True
        if checkpoints and checkpoints.errors:
            failed = True
        if not status("failed" if failed else "complete"):
            failed = True
        return 1 if failed else 0
    finally:
        stop.set()
        if thread is not None:
            # Retain capture ownership until the recorder actually exits. A hung adapter
            # leaves an inspectable live worker, not permission to launch a duplicate.
            thread.join()
        if checkpoints:
            checkpoints.finish()
        lock.close()


def main(argv=None):
    from simcoach.live.visit_release import require_guarded_entry
    require_guarded_entry()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--lock-root", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, help="Explicit test-only replay input")
    parser.add_argument("--startup-timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.startup_timeout) or not 0 < args.startup_timeout <= 300:
        parser.error("--startup-timeout must be finite, positive and at most 300 seconds")
    args.run.mkdir(parents=True, exist_ok=False)
    if args.fixture:
        write_status(args.run / "replay-provenance.json", dict(
            schema_version=1, mode="synthetic_replay", source=str(args.fixture.resolve()),
            sha256=hashlib.sha256(args.fixture.read_bytes()).hexdigest(), live_validation="NOT RUN",
        ))
    try:
        return work(args.run, args.lock_root, fixture=args.fixture,
                    startup_timeout=args.startup_timeout)
    except BaseException as exc:
        (args.run / "worker-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        # A dead worker cannot claim finalized capture; existing evidence stays untouched.
        write_status(args.run / "worker-failure.json", dict(
            schema_version=1, at=time.time(), error_type=type(exc).__name__, pid=os.getpid(),
        ))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
