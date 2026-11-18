from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.cli.visit import capture_readiness, classify, closure_success, recorder_ready, request
from simcoach.live.supervisor import FuelPlan
from simcoach.live.visit_worker import close_recordings, lease_valid
from tests.test_race_watch import BASE, CONTEXT, status
from tests.test_session import build_ticks, session_info

ROOT = Path(__file__).resolve().parents[1]


def fixture_file(tmp_path, *, bad=False):
    values = [t.as_record() for t in build_ticks()]
    if bad:
        values[2]["t"] = values[1]["t"]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(dict(info=session_info(), ticks=values)), encoding="utf-8")
    return path


def execute(command, timeout=25):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run([sys.executable, *command], cwd=ROOT, text=True, capture_output=True,
                          timeout=timeout, creationflags=flags)


def test_fixture_visit_records_validates_analyzes_and_ends_test_clock(tmp_path):
    fixture = fixture_file(tmp_path)
    root = tmp_path / "v"
    result = execute(["-m", "simcoach.cli.visit", "start", "--present", "--intent", "test only",
                      "--fixture", str(fixture), "--out", str(root), "--max-seconds", "15"])
    assert result.returncode == 0, result.stdout + result.stderr
    run = next(p for p in root.iterdir() if p.is_dir())
    state = json.loads((run / "status.json").read_text())
    assert state["mode"] == "replay" and state["status"] == "complete"
    assert state["audio"] == "disabled" and state["ready_to_drive"] is False
    assert state["worker_exit"] == 0
    assert state["clock"]["status"] == "complete"
    assert state["clock"]["activity_s"]["driving"] == 0
    rows = state["closure"]["recordings"]
    assert len(rows) == 1 and rows[0]["validation"] == rows[0]["analysis"] == "PASS"
    assert rows[0]["summary"]["valid_lap_count"] == 1
    assert (run / "worker/debrief.md").is_file()
    assert not list(run.rglob("coach_response.json"))
    original = (run / "worker/closure.json").read_bytes()
    with pytest.raises(FileExistsError):
        close_recordings(run / "worker", [Path(rows[0]["path"])])
    assert (run / "worker/closure.json").read_bytes() == original


def test_invalid_capture_is_retained_and_not_analyzed(tmp_path):
    fixture = fixture_file(tmp_path, bad=True)
    root = tmp_path / "v"
    result = execute(["-m", "simcoach.cli.visit", "start", "--present", "--intent", "bad test",
                      "--fixture", str(fixture), "--out", str(root), "--max-seconds", "15"])
    assert result.returncode == 1, result.stdout + result.stderr
    run = next(p for p in root.iterdir() if p.is_dir())
    state = json.loads((run / "status.json").read_text())
    assert state["status"] == "incomplete" and state["worker_exit"] == 1
    row = state["closure"]["recordings"][0]
    assert row["validation"] == "FAIL" and row["analysis"] == "NOT RUN"
    assert Path(row["path"]).exists()
    assert (run / "worker/failure.txt").exists()
    assert not (Path(row["path"]) / "analysis").exists()


def test_worker_without_coordinator_lease_stops_without_capture(tmp_path):
    fixture = fixture_file(tmp_path)
    result = execute(["-m", "simcoach.live.visit_worker", "--run", str(tmp_path / "w"),
                      "--lock-root", str(tmp_path / "locks"), "--fixture", str(fixture)])
    assert result.returncode == 1, result.stdout + result.stderr
    state = json.loads((tmp_path / "w/status.json").read_text())
    assert state["stop_reason"] == "coordinator_lease_unavailable"
    assert state["sessions"] == [] and state["status"] == "failed"


@pytest.mark.parametrize("value", [None, {}, {"at": 1}, {"schema_version": True, "at": 1},
                                   {"schema_version": 1, "at": float("nan")},
                                   {"schema_version": 1, "at": 20},
                                   {"schema_version": 1, "at": 1}])
def test_missing_malformed_future_stale_lease_is_refused(tmp_path, value):
    path = tmp_path / "heartbeat.json"
    if value is not None:
        path.write_text(json.dumps(value), encoding="utf-8")
    assert lease_valid(path, 10) is False


def test_current_lease_valid(tmp_path):
    path = tmp_path / "heartbeat.json"
    write_status(path, dict(schema_version=1, at=10))
    assert lease_valid(path, 14.9)
    assert not lease_valid(path, 15)


@pytest.mark.parametrize("heartbeat_at,expected", [(100, True), (90, False), (101, False)])
def test_lease_transient_read_does_not_refresh_or_extend_lease(heartbeat_at, expected):
    import errno

    class Heartbeat:
        calls = 0

        def read_text(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise PermissionError(errno.EACCES, "synthetic Windows read race")
            return json.dumps(dict(schema_version=1, at=heartbeat_at))

    diagnostics = []
    path = Heartbeat()
    assert lease_valid(path, 100.1, diagnostic=diagnostics.append) is expected
    assert path.calls == 2
    assert diagnostics[0]["reads"][0]["failures"][0]["error_type"] == "PermissionError"


def test_live_lease_samples_time_after_reading_concurrent_heartbeat(monkeypatch):
    from simcoach.live import visit_worker

    observed = []

    class HeartbeatPublishedDuringRead:
        def read_text(self, **kwargs):
            observed.append("read")
            return json.dumps(dict(schema_version=1, at=100.001))

    def clock():
        assert observed == ["read"]
        return 100.002

    monkeypatch.setattr(visit_worker.time, "time", clock)
    assert lease_valid(HeartbeatPublishedDuringRead())


@pytest.mark.parametrize("at", [100.003, 95.002, float("inf")])
def test_post_read_lease_check_still_refuses_future_stale_nonfinite(monkeypatch, at):
    from simcoach.live import visit_worker

    class Heartbeat:
        def read_text(self, **kwargs):
            return json.dumps(dict(schema_version=1, at=at))

    monkeypatch.setattr(visit_worker.time, "time", lambda: 100.002)
    assert not lease_valid(Heartbeat())


def test_capture_readiness_requires_live_worker_ticks_and_freshness():
    current = dict(schema_version=1, status="recording", active_ticks=10, intake_active=True,
                   updated_at=100.0, tick_age_s=0.1)
    assert recorder_ready(current, now=100.2, process_alive=True)
    assert not recorder_ready(current, now=104, process_alive=True)
    assert not recorder_ready(current, now=100.2, process_alive=False)
    assert not recorder_ready(current | {"active_ticks": 0}, now=100.2, process_alive=True)
    assert not recorder_ready(current | {"intake_active": False}, now=100.2, process_alive=True)
    assert not recorder_ready(current | {"tick_age_s": 10}, now=100.2, process_alive=True)
    assert not recorder_ready(current | {"status": "closing"}, now=100.2, process_alive=True)


def test_fresh_recorder_polling_does_not_certify_stale_simulator_source():
    current = dict(schema_version=1, status="recording", active_ticks=10, intake_active=True,
                   updated_at=100.0, tick_age_s=0.1)
    for source in (None, dict(health="degraded", ready=False), dict(health="healthy", ready=True)):
        flags = capture_readiness(current, source, now=100.2, process_alive=True)
        assert flags == dict(recorder_intake_ready=True, capture_ready=False,
                             capture_expires_at=None)
    flags = capture_readiness(current, dict(health="healthy", ready=True, expires_at=103),
                              now=100.2, process_alive=True)
    assert flags["capture_ready"] is True
    assert flags["capture_expires_at"] == 102.9


def test_worker_status_does_not_extend_the_age_of_its_last_tick():
    worker = dict(schema_version=1, status="recording", active_ticks=10,
                  intake_active=True, updated_at=100.0, tick_age_s=2.5)
    assert recorder_ready(worker, now=100.49, process_alive=True)
    assert not recorder_ready(worker, now=100.5, process_alive=True)


def test_archive_expiry_between_admission_and_readiness_prevents_handoff():
    worker = dict(schema_version=1, status="recording", active_ticks=10,
                  intake_active=True, updated_at=100.0, tick_age_s=.1)
    admitted = dict(health="healthy", ready=True, expires_at=100.2)
    assert capture_readiness(worker, admitted, now=100.19, process_alive=True)["capture_ready"]
    assert not capture_readiness(worker, admitted, now=100.2, process_alive=True)["capture_ready"]


@pytest.mark.parametrize("closure", [None, {}, {"schema_version": 1, "recordings": []},
    {"schema_version": 1, "recordings": [dict(validation="PASS", analysis="NOT RUN")]},
    {"schema_version": 1, "recordings": [dict(validation="PASS", analysis="PASS")]}])
def test_zero_exit_without_successful_outputs_cannot_complete_visit(closure):
    assert not closure_success(closure)


def test_stop_request_first_boundary_is_immutable(tmp_path):
    (tmp_path / "requests").mkdir()
    write_status(tmp_path / "status.json", dict(status="running"))
    request(tmp_path, "stop")
    first = (tmp_path / "stop").read_bytes()
    request(tmp_path, "stop")
    assert (tmp_path / "stop").read_bytes() == first
    declaration = json.loads(first)
    assert declaration["elapsed"] <= time.perf_counter()
    assert declaration["at"] <= time.time()


def test_activity_classification_does_not_turn_missing_data_into_driving():
    assert classify(None, BASE) == "unknown"
    assert classify(status(0, mSpeed=30), BASE) == "driving"
    assert classify(status(0, mSpeed=30, mPitMode=2), BASE) == "pit_moving"
    assert classify(status(0, mSpeed=0), BASE) == "not_driving"
    assert classify(status(0, mSpeed=30, mGameState=3), BASE) == "not_driving"
    assert classify(status(0, mSpeed=30), BASE + 3) == "unknown"


def test_control_requests_preserve_originals_and_refuse_closed_visit(tmp_path):
    (tmp_path / "requests").mkdir()
    write_status(tmp_path / "status.json", dict(status="running"))
    request(tmp_path, "reason", reason="intentional_rest")
    path = next((tmp_path / "requests").iterdir())
    assert json.loads(path.read_text())["reason"] == "intentional_rest"
    request(tmp_path, "stop")
    assert json.loads((tmp_path / "stop").read_text())["declaration"] == "driver_left"
    write_status(tmp_path / "status.json", dict(status="complete"))
    with pytest.raises(ValueError, match="not_running"):
        request(tmp_path, "reason", reason="setup")


def test_worker_expiring_lease_finalizes_owned_capture(tmp_path):
    fixture = fixture_file(tmp_path)
    # A long synthetic stream keeps capture alive while the lease expires.
    value = json.loads(fixture.read_text())
    one = value["ticks"][0]
    value["ticks"] = [dict(one, t=i * 0.02, lap_dist_pct=min(0.9, i * 0.00001))
                      for i in range(30000)]
    fixture.write_text(json.dumps(value), encoding="utf-8")
    write_status(tmp_path / "heartbeat.json", dict(schema_version=1, at=time.time()))
    result = execute(["-m", "simcoach.live.visit_worker", "--run", str(tmp_path / "w"),
                      "--lock-root", str(tmp_path / "locks"), "--fixture", str(fixture)],
                     timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((tmp_path / "w/status.json").read_text())
    assert state["stop_reason"] == "coordinator_lease_expired"
    assert 0 < state["ticks_observed"] < 30000
    assert state["status"] == "complete"
    closure = json.loads((tmp_path / "w/closure.json").read_text())
    assert closure["recordings"][0]["validation"] == "PASS"


@pytest.mark.parametrize("identity", ["missing", "corrupt", "changing"])
def test_missing_or_corrupt_identity_waits_with_clock_and_never_spawns(tmp_path, monkeypatch,
                                                                   identity):
    from simcoach.cli import visit

    archive = tmp_path / "archive"
    archive.mkdir()
    if identity == "corrupt":
        (archive / "run.json").write_text("{broken", encoding="utf-8")
    if identity == "changing":
        write_status(archive / "run.json", dict(schema_version=1, expected_version=14,
                                                struct_size_bytes=20700))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(dict(schema_version=1, **asdict(
        FuelPlan(CONTEXT, 0.5, 6, 2, 2.0, "synthetic test")))), encoding="utf-8")
    elapsed = [0.0]
    monkeypatch.setattr(visit, "time", SimpleNamespace(
        time=lambda: BASE + elapsed[0], perf_counter=lambda: elapsed[0],
        sleep=lambda _: elapsed.__setitem__(0, elapsed[0] + 0.25)))
    real_read = visit.read_json

    source_class = visit.ArchiveStatusSource

    def source(directory):
        def read():
            # Good advancing status is insufficient without a coherent run identity.
            value = status(elapsed[0])
            value["samples"] = int(elapsed[0] * 100) + 1
            value["latest"]["mSequenceNumber"] = 2 * value["samples"]
            write_status(directory / "status.json", value)
            return source_class(directory)()
        return read

    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    if identity == "changing":
        real_read_text = Path.read_text

        def changing_read(path, *args, **kwargs):
            text = real_read_text(path, *args, **kwargs)
            if path == archive / "status.json":
                write_status(archive / "run.json", dict(schema_version=1, expected_version=14,
                             struct_size_bytes=20700, generation=elapsed[0]))
            return text

        monkeypatch.setattr(Path, "read_text", changing_read)
    launches = []

    def popen(*args, **kwargs):
        launches.append(args)
        raise AssertionError("No recorder may start with unverified archive identity")

    monkeypatch.setattr(visit.subprocess, "Popen", popen)
    published = []
    real_write = visit.write_status

    def write(path, value):
        real_write(path, value)
        if path.name == "status.json" and value.get("status") == "running":
            published.append(value.copy())
            if elapsed[0] >= 0.5:
                # Stop remains available in waiting_for_source; use the real control API.
                visit.request(path.parent, "stop")

    monkeypatch.setattr(visit, "write_status", write)
    root = tmp_path / "visits"
    assert visit.main(["start", "--present", "--intent", "test waiting",
                       "--archive", str(archive), "--plan", str(plan),
                       "--out", str(root), "--max-seconds", "2"]) == 1
    assert not launches and len(published) >= 3
    assert all(row["phase"] == "waiting_for_source" for row in published)
    assert all(not row["capture_ready"] and row["worker_pid"] is None for row in published)
    assert all(row["source"]["source_id"] is None for row in published)
    assert published[-1]["clock"]["elapsed_s"] >= 0.5
    run = next(p for p in root.iterdir() if p.is_dir())
    final = real_read(run / "status.json")
    assert final["clock"]["status"] == "complete"
    assert final["status"] == "incomplete" and final["problem"] is None
    assert not (run / "worker-intent.json").exists()


def test_worker_no_first_tick_times_out_and_preserves_capture(tmp_path):
    fixture = fixture_file(tmp_path)
    value = json.loads(fixture.read_text())
    value["ticks"] = []
    fixture.write_text(json.dumps(value), encoding="utf-8")
    write_status(tmp_path / "heartbeat.json", dict(schema_version=1, at=time.time()))
    result = execute(["-m", "simcoach.live.visit_worker", "--run", str(tmp_path / "w"),
                      "--lock-root", str(tmp_path / "locks"), "--fixture", str(fixture),
                      "--startup-timeout", "0.4"], timeout=10)
    assert result.returncode == 1, result.stdout + result.stderr
    state = json.loads((tmp_path / "w/status.json").read_text())
    assert state["stop_reason"] == "first_tick_timeout" and state["status"] == "failed"
    assert state["ticks_observed"] == 0
    closure = json.loads((tmp_path / "w/closure.json").read_text())
    assert closure["recordings"][0]["validation"] == "FAIL"
    assert Path(closure["recordings"][0]["path"]).exists()


def test_timeout_cannot_become_success_when_tick_arrives_during_stop(tmp_path, monkeypatch):
    from simcoach.ingest import recorder as recorder_module
    from simcoach.live import visit_worker as worker
    from simcoach.utils import llm_logger

    monkeypatch.setattr(llm_logger, "configure_logging", lambda _: None)
    run = tmp_path / "w"
    run.mkdir()
    recorders = []

    class LateRecorder:
        def __init__(self, adapter, config, *, stop_requested):
            self.stop_requested = stop_requested
            self.session_paths = (run / "sessions" / "late",)
            self.ticks_observed = self.active_ticks = 0
            self.intake_active = False
            self.last_tick_at = None
            recorders.append(self)

        def run(self):
            assert self.stop_requested()
            self.ticks_observed = self.active_ticks = 1

    class ControlledThread:
        def __init__(self, *, target, name):
            self.target, self.alive = target, False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            if self.alive:
                self.target()
                self.alive = False

    monkeypatch.setattr(recorder_module, "Recorder", LateRecorder)
    monkeypatch.setattr(worker, "threading", SimpleNamespace(
        Thread=ControlledThread, Event=worker.threading.Event))
    monkeypatch.setattr(worker, "fixture_adapter", lambda _: object())
    clock = iter([0.0, 1.0])
    monkeypatch.setattr(worker, "time", SimpleNamespace(time=time.time,
                                                       perf_counter=lambda: next(clock)))
    def valid_lease(*args, diagnostic=None):
        diagnostic(dict(valid=True, reads=[]))
        return True

    monkeypatch.setattr(worker, "lease_valid", valid_lease)
    closed = []

    def close(path, sessions):
        closed.extend(sessions)
        return dict(unregistered_artifacts=[], recordings=[dict(validation="PASS",
                                                                analysis="PASS")])

    monkeypatch.setattr(worker, "close_recordings", close)
    assert worker.work(run, tmp_path / "locks", fixture=tmp_path / "fake",
                       startup_timeout=0.5) == 1
    state = json.loads((run / "status.json").read_text())
    assert recorders[0].ticks_observed == 1 and closed == list(recorders[0].session_paths)
    assert state["stop_reason"] == "first_tick_timeout" and state["status"] == "failed"


@pytest.mark.parametrize("context", [
    dict(mGameState=4, mSessionState=1, mRaceState=1),  # garage/menu
    dict(mGameState=2, mSessionState=5, mRaceState=1),  # grid
    dict(mGameState=2, mSessionState=6, mRaceState=2),  # Time Trial
])
def test_parent_admits_once_without_fuel_and_keeps_clock_after_capture_failure(tmp_path,
                                                                             monkeypatch,
                                                                             context):
    from simcoach.cli import visit
    from simcoach.live import recording_storage

    monkeypatch.setattr(recording_storage, "free_bytes", lambda _: recording_storage.MIN_FREE_BYTES)

    archive = tmp_path / "archive"
    archive.mkdir()
    write_status(archive / "run.json", dict(schema_version=1, expected_version=14,
                                           struct_size_bytes=20700))
    plan = FuelPlan(CONTEXT, 0.5, 6, 2, 2.0, "synthetic test")
    elapsed = [0.0]
    monkeypatch.setattr(visit, "time", SimpleNamespace(
        time=lambda: BASE + elapsed[0], perf_counter=lambda: elapsed[0],
        sleep=lambda _: elapsed.__setitem__(0, elapsed[0] + 0.25)))
    original_source = visit.ArchiveStatusSource

    def source(directory):
        def read():
            second = elapsed[0]
            value = status(second, mSpeed=0, **context)
            if second < 0.5:
                value["latest"]["mGameState"] = 0
            value["samples"] = int(second * 100) + 1
            value["latest"]["mSequenceNumber"] = 2 * value["samples"]
            value["latest"].pop("mFuelCapacity")
            value["latest"]["mFuelLevel"] = None
            if second == 1:
                value.update(coverage="stale_mapping", latest_is_current=False)
            write_status(directory / "status.json", value)
            return original_source(directory)()
        return read

    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    launches = []

    class Child:
        pid = 12345

        def poll(self):
            return 1 if elapsed[0] >= 1.5 else None

    def popen(*args, **kwargs):
        launches.append(elapsed[0])
        return Child()

    monkeypatch.setattr(visit.subprocess, "Popen", popen)
    real_read = visit.read_json

    storage = recording_storage.RecordingStorage(clock=lambda: BASE + elapsed[0],
                                                  elapsed=lambda: elapsed[0])

    def read(path, **kwargs):
        if path.parent.name == "worker" and path.name == "status.json" and launches:
            waiting = elapsed[0] == 1.25
            return dict(schema_version=1, status="recording", active_ticks=0 if waiting else 10,
                        intake_active=not waiting, updated_at=BASE + elapsed[0], tick_age_s=0.01,
                        recording_storage=storage.poll(path.parent),
                        capture_phase="waiting_for_source" if waiting else "recording")
        return real_read(path, **kwargs)

    monkeypatch.setattr(visit, "read_json", read)
    published = []
    real_write = visit.write_status

    def write(path, value):
        real_write(path, value)
        if path.name == "status.json" and value.get("status") == "running":
            published.append(value.copy())
            if elapsed[0] >= 2:
                visit.request(path.parent, "stop")

    monkeypatch.setattr(visit, "write_status", write)
    assert visit.run_visit(tmp_path / "visits", intent="synthetic admission", present=True,
                           archive=archive, plan=plan, max_seconds=3) == 1
    assert launches == [0.75]
    by_time = {round(row["updated_at"] - BASE, 2): row for row in published}
    assert by_time[0.25]["phase"] == "waiting_for_source"
    assert by_time[0.75]["capture_ready"]
    assert by_time[0.75]["supervision"]["supervision_health"] == "degraded"
    assert not by_time[1]["capture_ready"]
    assert by_time[1.25]["phase"] == "waiting_for_source"
    assert not by_time[1.25]["capture_ready"]
    assert by_time[1.75]["source"]["ready"]
    assert by_time[1.75]["phase"] == "capture_failed"
    assert not by_time[1.75]["capture_ready"]
    assert by_time[2]["clock"]["elapsed_s"] == 2


@pytest.mark.parametrize("invalid", [dict(fixture=Path("synthetic.json")), dict(archive=None),
                                     dict(phrase=None), dict(selection=None)])
def test_direct_visit_api_rejects_replay_or_incomplete_audio_before_side_effects(
        tmp_path, monkeypatch, invalid):
    from simcoach.cli import visit

    effects = []

    def forbidden(*args, **kwargs):
        effects.append(args)
        raise AssertionError("invalid audio request must fail before ownership or output")

    monkeypatch.setattr(visit, "VisitAudio", forbidden)
    monkeypatch.setattr(visit, "SourceLock", forbidden)
    with pytest.raises(ValueError, match="audio_requires_live_archive"):
        visit.run_visit(tmp_path, intent="synthetic guard", present=True, enable_audio=True,
                        **(dict(archive=Path("archive"), phrase=Path("phrase"),
                                selection=Path("selection.json")) | invalid))
    assert effects == [] and list(tmp_path.iterdir()) == []
