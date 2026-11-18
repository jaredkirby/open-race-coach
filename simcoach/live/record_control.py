"""Private standalone recorder ownership and cooperative stop, without signals."""

from __future__ import annotations

import os
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.ingest.recorder import Recorder, RecorderConfig, RecordingCancelled
from simcoach.live.supervisor import strict_json
from simcoach.live.visit_worker import close_recordings, local_path

TERMINAL = {"complete", "failed", "cancelled"}


def read_object(path):
    try:
        value = strict_json(path.read_text(encoding="utf-8"))
    except RecursionError as exc:
        raise ValueError("control_object_too_deep") from exc
    if not isinstance(value, dict):
        raise ValueError("control_object_required")
    return value


def identity(run):
    value = read_object(run / "run.json")
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or not isinstance(value.get("run_id"), str) or len(value["run_id"]) != 32
            or any(c not in "0123456789abcdef" for c in value["run_id"])):
        raise ValueError("invalid_record_control_identity")
    return value["run_id"]


def stop_matches(value, run_id):
    return (type(value.get("schema_version")) is int and value["schema_version"] == 1
            and value.get("run_id") == run_id and value.get("action") == "stop"
            and isinstance(value.get("request_id"), str) and bool(value["request_id"])
            and type(value.get("at")) in (int, float)
            and 0 <= value["at"] <= time.time())


def request_stop(run: Path):
    """Submit intent only. A stale heartbeat never authorizes a kill or restart."""
    run = local_path(run)
    run_id = identity(run)  # Missing/unknown paths must not create a control directory.
    lock = SourceLock(run / "request.lock")
    try:
        state = read_object(run / "status.json")
        if (state.get("run_id") != run_id or type(state.get("schema_version")) is not int
                or state["schema_version"] != 1):
            raise ValueError("record_control_identity_mismatch")
        if state.get("status") in TERMINAL:
            return f"Already terminal: {state['status']}; inspect status.json and closure.json"
        if state.get("status") not in {"starting", "recording", "stopping", "closing"}:
            raise ValueError("invalid_record_control_state")
        path = run / "stop.json"
        if path.exists():
            if not stop_matches(read_object(path), run_id):
                raise ValueError("conflicting_stop_request")
            return "Stop already requested; completion is not yet established"
        write_status(path, dict(schema_version=1, run_id=run_id, request_id=uuid4().hex,
                                action="stop", at=time.time()))
        return "Stop requested; inspect terminal status.json and closure.json for completion"
    finally:
        lock.close()


def controlled_record(run: Path, *, sim, adapter_factory, config=None):
    """Own capture through finalization. Private config override supports offline fixtures."""
    run = local_path(run)
    run.mkdir(parents=True, exist_ok=False)
    run_id = uuid4().hex
    write_status(run / "run.json", dict(schema_version=1, run_id=run_id, sim=sim,
                                        created_at=time.time(), pid=os.getpid()))
    stop = threading.Event()
    errors = []
    cancelled = []
    recorder = None
    thread = None
    lock = None
    request_error = None

    def publish(phase):
        write_status(run / "status.json", dict(
            schema_version=1, run_id=run_id, status=phase, updated_at=time.time(),
            pid=os.getpid(), stop_requested=stop.is_set(), request_error=request_error,
            error_type=errors[0] if errors else None,
            ticks_observed=recorder.ticks_observed if recorder else 0,
            capture_phase=recorder.capture_phase if recorder else "starting",
            sessions=[str(p) for p in recorder.session_paths] if recorder else [],
        ))

    def capture():
        try:
            recorder.run()
        except RecordingCancelled:
            cancelled.append(True)
        except BaseException as exc:
            errors.append(type(exc).__name__)
            (run / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")

    try:
        publish("starting")
        # Same ownership domain as visit workers when using their parent directory.
        lock = SourceLock(run.parent / "capture.lock")
        recorder = Recorder(adapter_factory(), config or RecorderConfig(
            out_dir=run / "sessions", continuous_source_gaps=True), stop_requested=stop.is_set)
        thread = threading.Thread(target=capture, name="controlled-recorder")
        thread.start()
        print(f"Recorder control: {run}", flush=True)
        while thread.is_alive():
            if (run / "stop.json").exists():
                try:
                    valid = stop_matches(read_object(run / "stop.json"), run_id)
                    request_error = None if valid else "invalid_stop_request"
                    if valid:
                        stop.set()
                except (OSError, ValueError, TypeError):
                    request_error = "unreadable_stop_request"
            publish("stopping" if stop.is_set() else "recording")
            thread.join(timeout=0.2)
        publish("closing")
        closure = close_recordings(run, recorder.session_paths)
        failed = bool(errors) or bool(closure["unregistered_artifacts"]) or any(
            row["validation"] != "PASS" or row["analysis"] != "PASS"
            for row in closure["recordings"])
        phase = "failed" if failed else "cancelled" if cancelled else "complete"
        if phase == "complete" and not recorder.session_paths:
            phase = "failed"
        publish(phase)
        return 1 if phase == "failed" else 0
    except BaseException as exc:
        errors.append(type(exc).__name__)
        (run / "control-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        stop.set()
        # Retain ownership until capture exits, even when control publication fails.
        if thread is not None:
            thread.join()
        publish("failed")
        raise
    finally:
        stop.set()
        if thread is not None:
            thread.join()
        if lock is not None:
            lock.close()
