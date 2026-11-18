"""Storage failure scenarios with generated ticks and no actual simulator."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from simcoach.adapters import ams2
from simcoach.cli.telemetry_watch import write_status
from simcoach.cli.visit import capture_readiness, read_json
from simcoach.ingest import recorder
from simcoach.live import recording_storage, visit_worker
from simcoach.live.recording_storage import MIN_FREE_BYTES, RecordingStorage, storage_deadline
from tests.test_session import build_ticks, session_info
from tests.test_visit_attempts import harness


class Clock:
    wall = 100.0
    mono = 10.0

    def probe(self):
        return RecordingStorage(clock=lambda: self.wall, elapsed=lambda: self.mono)

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


def test_probe_retains_sample_until_next_local_read(tmp_path, monkeypatch):
    sizes = iter([MIN_FREE_BYTES, MIN_FREE_BYTES - 1])
    monkeypatch.setattr(recording_storage, "free_bytes", lambda _: next(sizes))
    clock = Clock()
    probe = clock.probe()
    initial = probe.poll(tmp_path)
    assert initial["state"] == "available"
    clock.advance(.8)
    assert probe.poll(tmp_path) == initial
    assert storage_deadline(initial, now=clock.wall, elapsed=clock.mono) == 103
    clock.advance(.4)
    assert probe.poll(tmp_path)["state"] == "low"
    assert storage_deadline(probe.last, now=clock.wall, elapsed=clock.mono) is None


@pytest.mark.parametrize("problem", ["io", "slow", "clock"])
def test_unavailable_measurement_cannot_grant_storage(tmp_path, monkeypatch, problem):
    clock = Clock()

    def read(_):
        if problem == "io":
            raise OSError("generated query failure")
        if problem == "slow":
            clock.advance(3)
        else:
            clock.wall -= 1
        return MIN_FREE_BYTES

    monkeypatch.setattr(recording_storage, "free_bytes", read)
    value = clock.probe().poll(tmp_path)
    assert value["state"] == "unknown" and value["free_bytes"] is None
    assert storage_deadline(value, now=clock.wall, elapsed=clock.mono) is None


def test_launch_checks_storage_again_after_ownership_probe(tmp_path, monkeypatch):
    from simcoach.live import visit_attempts

    monkeypatch.setattr(recording_storage, "free_bytes", lambda _: MIN_FREE_BYTES)
    attempts, children, _ = harness(tmp_path)
    clock = Clock()
    attempts.clock, attempts.elapsed = lambda: clock.wall, lambda: clock.mono
    storage = clock.probe().poll(tmp_path)
    original = visit_attempts.SourceLock

    def blocking(path):
        clock.advance(3)
        return original(path)

    monkeypatch.setattr(visit_attempts, "SourceLock", blocking)
    attempts.step(source_ready=True, now=clock.wall, elapsed=clock.mono, storage=storage)
    assert not children
    assert attempts.state == "capture_storage_unavailable"
    assert not (attempts.run / "worker-intent.json").exists()


def test_replacement_waits_for_storage_without_spending_attempt(tmp_path, monkeypatch):
    remaining = [MIN_FREE_BYTES]
    monkeypatch.setattr(recording_storage, "free_bytes", lambda _: remaining[0])
    attempts, children, identities = harness(tmp_path)
    clock = Clock()
    attempts.clock, attempts.elapsed = lambda: clock.wall, lambda: clock.mono
    probe = clock.probe()

    def step():
        attempts.step(source_ready=True, now=clock.wall, elapsed=clock.mono,
                      storage=probe.poll(tmp_path))

    try:
        step()
        children[0].code = 1
        identities[200] = "exited"
        remaining[0] -= 1
        clock.advance(1)
        step()
        clock.advance(2)
        step()
        assert attempts.state == "capture_storage_unavailable" and len(children) == 1
        remaining[0] += 1
        clock.advance(1)
        step()
        assert len(children) == 2 and attempts.interrupted
        intent = read_json(attempts.run / "capture-attempts/0002/intent.json")
        assert intent["recording_storage"]["state"] == "available"
    finally:
        attempts.close_streams()


def test_readiness_needs_both_storage_proofs_and_earliest_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(recording_storage, "free_bytes", lambda _: MIN_FREE_BYTES)
    clock = Clock()
    owner = clock.probe().poll(tmp_path)
    clock.advance(.5)
    child = clock.probe().poll(tmp_path)
    source = dict(ready=True, health="healthy", expires_at=103.5)
    intake = dict(schema_version=1, status="recording", updated_at=clock.wall,
                  tick_age_s=.01, active_ticks=5, intake_active=True)
    options = dict(now=clock.wall, elapsed=clock.mono, process_alive=True, storage=owner)
    assert not capture_readiness(intake, source, **options)["capture_ready"]
    intake["recording_storage"] = child
    state = capture_readiness(intake, source, **options)
    assert state["capture_ready"] and state["capture_expires_at"] == 103


@pytest.mark.parametrize("case", ["prelaunch_low", "prelaunch_unknown", "intake_low"])
def test_worker_reserve_policy_leaves_control_and_preserves_recorded_prefix(
        tmp_path, monkeypatch, case):
    from simcoach.utils import llm_logger

    monkeypatch.setattr(llm_logger, "configure_logging", lambda *args, **kwargs: None)
    worker = tmp_path / "worker"
    worker.mkdir()
    write_status(tmp_path / "heartbeat.json", dict(schema_version=1, at=visit_worker.time.time()))
    ticks = iter(build_ticks(rows_per_lap=8))
    ready = Event()
    observations, connections = [], []

    class Generated:
        sim_name = "ams2"

        def connect(self):
            connections.append("open")

        def disconnect(self):
            connections.append("closed")

        def read_session_info(self):
            return session_info()

        def read_tick(self):
            result = next(ticks, None)
            if result is None:
                ready.set()
            return result

    config = recorder.RecorderConfig
    monkeypatch.setattr(recorder, "RecorderConfig",
                        lambda **values: replace(config(**values), metadata_stable_s=0))
    monkeypatch.setattr(ams2, "AMS2Adapter", Generated)

    def read(path):
        observations.append(Path(path).name)
        if case == "prelaunch_unknown":
            raise OSError("generated query failure")
        return MIN_FREE_BYTES - (case == "prelaunch_low" or ready.is_set())

    monkeypatch.setattr(recording_storage, "free_bytes", read)
    assert visit_worker.work(worker, tmp_path / "locks", startup_timeout=3) == 1
    state = read_json(worker / "status.json")
    assert state["status"] == "failed"
    assert not (tmp_path / "stop").exists()
    assert state["stop_reason"] == ("recording_storage_unavailable" if case.endswith("unknown")
                                   else "recording_disk_reserve")
    if case == "intake_low":
        assert connections == ["open", "closed"] and observations[-1] == "sessions"
        closure = read_json(worker / "closure.json")
        assert [r["validation"] for r in closure["recordings"]] == ["PASS"]
    else:
        assert not connections and not (worker / "sessions").exists()
