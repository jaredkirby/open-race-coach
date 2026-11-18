"""Real cooperative lifecycle and durable output tests, without simulator access."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from simcoach.cli import record
from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.ingest.recorder import RecorderConfig
from simcoach.live import record_control as control
from tests.test_recorder import tick
from tests.test_session import session_info


class IdleAdapter:
    sim_name = "ams2"

    def __init__(self, metadata=True):
        self.metadata = metadata
        self.index = 0
        self.disconnected = False

    def connect(self):
        pass

    def disconnect(self):
        self.disconnected = True

    def read_session_info(self):
        return session_info() if self.metadata else None

    def read_tick(self):
        if self.index >= 4:
            return None
        i = self.index
        self.index += 1
        return tick(lap=1, pct=i / 3, t=float(i))


def config(run):
    return RecorderConfig(out_dir=run / "sessions", metadata_stable_s=0,
                          tick_rate_hz=1000, tick_missing_grace_s=0.05,
                          continuous_source_gaps=True)


def wait_for(predicate):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            if predicate():
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def state(run):
    return json.loads((run / "status.json").read_text())


@pytest.fixture
def owner(tmp_path):
    owners = []

    def start(adapter=None, name="run"):
        run = tmp_path / name
        adapter = adapter or IdleAdapter()
        result = []
        thread = threading.Thread(target=lambda: result.append(control.controlled_record(
            run, sim="ams2", adapter_factory=lambda: adapter, config=config(run))))
        thread.start()
        owners.append((run, thread))
        wait_for(lambda: state(run)["status"] == "recording")
        return run, thread, result, adapter

    yield start
    for run, thread in owners:
        if thread.is_alive():
            # Test cleanup only: replace deliberately injected invalid request.
            write_status(run / "stop.json", dict(schema_version=1, run_id=control.identity(run),
                                                 request_id="cleanup", action="stop",
                                                 at=time.time()))
            thread.join(10)
        assert not thread.is_alive()


def test_idle_source_stop_preserves_ticks_and_closes_analysis(owner):
    run, thread, result, adapter = owner()
    wait_for(lambda: state(run)["capture_phase"] == "waiting_for_source")
    assert "requested" in control.request_stop(run)
    request = (run / "stop.json").read_bytes()
    control.request_stop(run)
    assert (run / "stop.json").read_bytes() == request
    thread.join(10)
    assert result == [0] and adapter.disconnected
    assert state(run)["status"] == "complete"
    closure = control.read_object(run / "closure.json")
    row = closure["recordings"][0]
    assert row["validation"] == row["analysis"] == "PASS"
    assert len(pd.read_parquet(Path(row["path"]) / "ticks.parquet")) == 4
    assert (run / "debrief.md").exists()
    before = (run / "status.json").read_bytes()
    assert "terminal" in control.request_stop(run)
    assert (run / "status.json").read_bytes() == before


def test_stop_before_metadata_is_cancelled_not_capture_success(owner):
    run, thread, result, adapter = owner(IdleAdapter(metadata=False))
    control.request_stop(run)
    thread.join(10)
    assert result == [0] and adapter.disconnected
    assert state(run)["status"] == "cancelled"
    assert state(run)["sessions"] == []
    assert control.read_object(run / "closure.json")["recordings"] == []


def test_identity_and_shared_owner_block_duplicates(owner):
    run, thread, _, _ = owner(IdleAdapter(metadata=False))
    manifest = (run / "run.json").read_bytes()
    with pytest.raises(FileExistsError):
        control.controlled_record(run, sim="ams2", adapter_factory=lambda: pytest.fail("connected"))
    assert (run / "run.json").read_bytes() == manifest
    with pytest.raises(RuntimeError, match="already_observed"):
        control.controlled_record(run.parent / "other", sim="ams2",
                                  adapter_factory=lambda: pytest.fail("connected"))
    assert state(run.parent / "other")["status"] == "failed"
    assert thread.is_alive()


def test_cancel_does_not_hide_adapter_cleanup_failure(owner):
    class BadDisconnect(IdleAdapter):
        def disconnect(self):
            raise OSError("injected disconnect failure")

    run, thread, result, _ = owner(BadDisconnect(metadata=False))
    control.request_stop(run)
    thread.join(10)
    assert result == [1]
    assert state(run)["status"] == "failed"
    assert state(run)["error_type"] == "OSError"


def test_stale_heartbeat_request_is_intent_not_completion(tmp_path):
    run = tmp_path / "stale"
    run.mkdir()
    run_id = "a" * 32
    write_status(run / "run.json", dict(schema_version=1, run_id=run_id))
    write_status(run / "status.json", dict(schema_version=1, run_id=run_id,
                                          status="recording", updated_at=1))
    before = (run / "status.json").read_bytes()
    assert "Stop requested" in control.request_stop(run)
    assert (run / "status.json").read_bytes() == before
    assert control.read_object(run / "stop.json")["run_id"] == run_id


def test_blocked_adapter_cannot_be_declared_stopped(owner):
    entered, release = threading.Event(), threading.Event()

    class Blocked(IdleAdapter):
        def read_session_info(self):
            entered.set()
            assert release.wait(10)
            return None

    run, thread, result, _ = owner(Blocked())
    try:
        assert entered.wait(5)
        control.request_stop(run)
        wait_for(lambda: state(run)["status"] == "stopping")
        assert thread.is_alive() and not (run / "closure.json").exists()
    finally:
        release.set()
    thread.join(10)
    assert result == [0] and state(run)["status"] == "cancelled"


@pytest.mark.parametrize("change", [{"run_id": "0" * 32}, {"schema_version": True},
                                    {"at": float("inf")}, {"action": "resume"},
                                    {"request_id": ""}])
def test_invalid_stop_cannot_stop_capture(owner, change):
    run, thread, _, _ = owner(IdleAdapter(metadata=False))
    value = dict(schema_version=1, run_id=control.identity(run), request_id="test",
                 action="stop", at=time.time())
    value.update(change)
    # Includes deliberately malformed/nonstandard JSON as hostile control input.
    (run / "stop.json").write_text(json.dumps(value))
    wait_for(lambda: state(run)["request_error"] is not None)
    assert thread.is_alive() and not state(run)["stop_requested"]
    with pytest.raises(ValueError):
        control.request_stop(run)


def test_deeply_nested_request_cannot_terminate_capture(owner):
    run, thread, _, _ = owner(IdleAdapter(metadata=False))
    (run / "stop.json").write_text("[" * 3000 + "0" + "]" * 3000)
    wait_for(lambda: state(run)["request_error"] == "unreadable_stop_request")
    assert thread.is_alive() and not state(run)["stop_requested"]
    with pytest.raises(ValueError, match="too_deep"):
        control.request_stop(run)


def test_blocked_finalization_retains_owner_until_written(owner, monkeypatch):
    from simcoach.ingest import recorder
    entered, release = threading.Event(), threading.Event()
    original = recorder.finalize_recorded_session

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(recorder, "finalize_recorded_session", blocked)
    run, thread, result, _ = owner()
    try:
        assert entered.wait(5)
        control.request_stop(run)
        wait_for(lambda: state(run)["status"] == "stopping")
        assert thread.is_alive()
        with pytest.raises(RuntimeError):
            SourceLock(run.parent / "capture.lock")
    finally:
        release.set()
    thread.join(10)
    assert result == [0] and state(run)["status"] == "complete"


def test_finalization_failure_is_failed_and_preserved(owner, monkeypatch):
    from simcoach.ingest import recorder

    def fail(*args, **kwargs):
        raise OSError("injected flush failure")

    monkeypatch.setattr(recorder, "finalize_recorded_session", fail)
    run, thread, result, _ = owner()
    thread.join(10)
    assert result == [1] and state(run)["status"] == "failed"
    assert (run / "failure.txt").exists()
    row = control.read_object(run / "closure.json")["recordings"][0]
    assert row["validation"] == "FAIL" and row["analysis"] == "NOT RUN"
    assert (Path(row["path"]) / "session.yaml").exists()


def test_separate_cli_process_submits_stop_to_running_owner(owner):
    run, thread, result, _ = owner()
    proc = subprocess.run([sys.executable, "-m", "simcoach.cli.record", "--stop", str(run)],
                          capture_output=True, text=True, timeout=15,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert proc.returncode == 0, proc.stderr
    assert "Stop requested" in proc.stdout
    thread.join(10)
    assert result == [0] and state(run)["status"] == "complete"


def test_cli_owner_and_stop_in_separate_processes(tmp_path):
    run = tmp_path / "process-run"
    script = (
        "import sys; from simcoach.cli import record; "
        "from tests.test_record_control import IdleAdapter; "
        "record.AMS2Adapter=IdleAdapter; "
        "raise SystemExit(record.main(['--sim','ams2','--control-dir',sys.argv[1]]))"
    )
    with (tmp_path / "owner.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-c", script, str(run)],
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=(subprocess.CREATE_NO_WINDOW
                                                  if os.name == "nt" else 0))
        try:
            wait_for(lambda: state(run)["ticks_observed"] == 4)
            stopped = subprocess.run(
                [sys.executable, "-m", "simcoach.cli.record", "--stop", str(run)],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            assert stopped.returncode == 0, stopped.stderr
            assert process.wait(timeout=15) == 0
            assert state(run)["status"] == "complete"
            assert state(run)["ticks_observed"] == 4
            assert control.read_object(run / "closure.json")["recordings"][0]["analysis"] == "PASS"
        finally:
            # This is the test-owned synthetic process only, never a simulator owner.
            if process.poll() is None:
                control.request_stop(run)
                process.wait(timeout=15)


@pytest.mark.parametrize("args", [["--stop", "x", "--sim", "ams2"],
                                    ["--stop", "x", "--out", "y"],
                                    ["--sim", "ams2", "--control-dir", "x", "--max-seconds", "2"],
                                    ["--sim", "ams2", "--max-seconds", "nan"]])
def test_cli_rejects_ambiguous_or_timed_managed_run(args):
    with pytest.raises(SystemExit):
        record.parse_args(args)


def test_legacy_cli_keeps_finite_mode_and_default_output(monkeypatch):
    seen = {}

    class Legacy:
        def __init__(self, adapter, cfg):
            seen["config"] = cfg

        def run(self):
            return Path("unused")

    monkeypatch.setattr(record, "AMS2Adapter", lambda: object())
    monkeypatch.setattr(record, "Recorder", Legacy)
    assert record.main(["--sim", "ams2"]) == 0
    assert seen["config"].out_dir == Path("data/sessions")
    assert seen["config"].continuous_source_gaps is False
