"""Synthetic owner failures, never simulator or headset operation."""

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.cli.visit import worker_stopped_for_departure
from simcoach.live.visit_attempts import CaptureAttempts


def harness(tmp_path):
    run = tmp_path / "visit"
    run.mkdir()
    children, identities = [], {}

    def probe(pid):
        return dict(pid=pid, created_100ns=str(pid * 100),
                    state=identities.get(pid, "unknown"), reason=None)

    def popen(command, **kwargs):
        directory = Path(command[command.index("--run") + 1])
        directory.mkdir()
        child = SimpleNamespace(pid=100 + len(children), code=None)
        child.poll = lambda: child.code
        actual_pid = 200 + len(children)
        identities[child.pid] = identities[actual_pid] = "running"
        write_status(directory / "worker-process.json",
                     dict(schema_version=1, process=probe(actual_pid)))
        children.append(child)
        return child

    attempts = CaptureAttempts(run, tmp_path, popen=popen, probe=probe)
    return attempts, children, identities


def step(attempts, seconds, **kwargs):
    attempts.step(source_ready=True, now=1000 + seconds, elapsed=seconds, **kwargs)


def test_first_worker_waits_for_existing_capture_owner(tmp_path):
    attempts, children, _ = harness(tmp_path)
    lock = SourceLock(tmp_path / "capture.lock")
    try:
        step(attempts, 0)
        assert attempts.state == "capture_lock_unavailable"
        assert not children and not attempts.attempts
        assert not (attempts.run / "worker-intent.json").exists()
    finally:
        lock.close()
        attempts.close_streams()

    attempts.clock = lambda: 1004
    step(attempts, 4, source_expires_at=1004)
    assert attempts.state == "awaiting_source" and not children
    try:
        step(attempts, 4, source_expires_at=1005)
        assert len(children) == 1 and attempts.directory.name == "worker"
        assert attempts.snapshot()["capture_interrupted"] is False
    finally:
        attempts.close_streams()


def test_dead_launcher_is_not_proof_actual_worker_ended(tmp_path):
    attempts, children, identities = harness(tmp_path)
    step(attempts, 0)
    children[0].code = 1
    step(attempts, 1)
    step(attempts, 3)
    assert len(children) == 1
    assert attempts.state == "worker_exit_unconfirmed"
    identities[200] = "unknown"
    step(attempts, 4)
    assert attempts.state == "worker_exit_unconfirmed"
    identities[200] = "absent"
    step(attempts, 5)
    assert len(children) == 2
    assert attempts.directory.name == "worker-0002"
    assert attempts.snapshot()["capture_interrupted"] is True
    admission = json.loads((attempts.run / "capture-attempts/0002/intent.json").read_text())
    assert admission["recovery_basis"]["actual_worker"]["state"] == "absent"
    assert admission["recovery_basis"]["launcher_exit_code"] == 1
    assert admission["recovery_basis"]["cooldown_elapsed_s"] == 4
    attempts.close_streams()


@pytest.mark.parametrize("receipt", [None, {}, {"schema_version": 2, "process": {
    "pid": 200, "created_100ns": "20000"}}, {"schema_version": True}])
def test_missing_or_unsupported_actual_worker_identity_prevents_replacement(tmp_path, receipt):
    attempts, children, identities = harness(tmp_path)
    step(attempts, 0)
    path = attempts.directory / "worker-process.json"
    path.unlink()
    if receipt is not None:
        write_status(path, receipt)
    children[0].code = 1
    identities[200] = "absent"
    step(attempts, 1)
    step(attempts, 4)
    assert len(children) == 1 and attempts.state == "worker_exit_unconfirmed"


def test_recovery_requires_source_lock_cooldown_and_preserves_failed_artifacts(tmp_path):
    attempts, children, identities = harness(tmp_path)
    step(attempts, 0)
    original = attempts.directory / "failure.txt"
    original.write_text("failed capture, preserve")
    original_intent = (attempts.run / "worker-intent.json").read_bytes()
    ambiguous = attempts.directory / "closure-intent.json"
    ambiguous.write_text('{"unfinished":"do not retry analysis"}')
    children[0].code = 1
    identities[200] = "exited"
    step(attempts, 1)
    step(attempts, 2)
    assert attempts.state == "recovery_cooldown"
    attempts.step(source_ready=False, now=1003, elapsed=3)
    assert attempts.state == "awaiting_source"
    lock = SourceLock(tmp_path / "capture.lock")
    try:
        step(attempts, 4)
        assert attempts.state == "capture_lock_unavailable"
        assert len(children) == 1
    finally:
        lock.close()
    step(attempts, 5)
    step(attempts, 6)
    assert len(children) == 2
    assert original.read_text() == "failed capture, preserve"
    assert ambiguous.read_text() == '{"unfinished":"do not retry analysis"}'
    assert not (ambiguous.parent / "closure.json").exists()
    assert (attempts.run / "worker-intent.json").read_bytes() == original_intent
    receipt = json.loads((attempts.run / "capture-attempts/0001/exit.json").read_text())
    assert receipt["exit_code"] == 1
    attempts.close_streams()


def test_three_start_limit_retains_every_failure_and_clock_does_not_affect_budget(tmp_path):
    attempts, children, identities = harness(tmp_path)
    step(attempts, 0)
    for index in range(3):
        children[index].code = 1
        identities[200 + index] = "absent"
        step(attempts, 1 + index * 3)
        step(attempts, 3 + index * 3)
    assert len(children) == 3 and attempts.state == "attempt_limit"
    step(attempts, 10000)
    assert len(children) == 3
    assert all(row["exit_code"] == 1 for row in attempts.snapshot()["attempts"])


def test_departure_wins_replacement_and_fixture_does_not_retry(tmp_path):
    attempts, children, identities = harness(tmp_path)
    step(attempts, 0)
    children[0].code = 0
    identities[200] = "exited"
    step(attempts, 1, allow_recovery=False)
    assert attempts.state == "recovery_disabled"
    (attempts.run / "stop").touch()
    step(attempts, 4)
    assert attempts.state == "departure_requested" and len(children) == 1


def test_source_admission_expiring_during_owner_probes_does_not_start_replacement(tmp_path):
    attempts, children, identities = harness(tmp_path)
    step(attempts, 0)
    children[0].code = 1
    identities[200] = "absent"
    step(attempts, 1)
    attempts.clock = lambda: 1005
    attempts.step(source_ready=True, now=1003, elapsed=3, source_expires_at=1004)
    assert len(children) == 1 and attempts.state == "awaiting_source"
    attempts.step(source_ready=True, now=1005, elapsed=5, source_expires_at=1007)
    assert len(children) == 2
    attempts.close_streams()


def test_ambiguous_spawn_is_latched_and_never_retried(tmp_path):
    attempts, children, identities = harness(tmp_path)
    calls = []

    def uncertain(*args, **kwargs):
        calls.append(args)
        raise OSError("synthetic spawn failure")

    attempts.popen = uncertain
    with pytest.raises(OSError, match="synthetic spawn failure"):
        step(attempts, 0)
    step(attempts, 10)
    assert len(calls) == 1 and attempts.state == "launch_uncertain"
    assert (attempts.run / "capture-attempts/0001/intent.json").exists()
    assert not (attempts.run / "capture-attempts/0001/spawn.json").exists()
    attempts.close_streams()


@pytest.mark.parametrize("reason", [None, "coordinator_lease_expired", "first_tick_timeout",
                                   "coordinator_lease_unavailable", "visit_stop_requested"])
def test_exit_zero_at_departure_requires_explicit_worker_stop_reason(reason):
    # Covers departure racing the host's first observation of a clean worker exit.
    value = dict(schema_version=1, status="complete", stop_reason=reason)
    assert worker_stopped_for_departure(value) == (reason == "visit_stop_requested")


def test_departure_racing_first_exit_observation_does_not_hide_lease_loss(tmp_path, monkeypatch):
    from simcoach.cli import visit
    from simcoach.live.supervisor import FuelPlan
    from tests.test_race_watch import CONTEXT

    # The first process poll occurs after launch within a loop iteration. Departure
    # arrives during publication, before the next recovery step sees that exit.
    original_write = visit.write_status
    launches = []

    def spawn(command, **kwargs):
        directory = Path(command[command.index("--run") + 1])
        directory.mkdir()
        launches.append(directory)
        write_status(directory / "status.json", dict(
            schema_version=1, status="complete", stop_reason="coordinator_lease_expired"))
        write_status(directory / "closure.json", dict(schema_version=1, recordings=[dict(
            path=str(directory / "sessions/synthetic"), validation="PASS", analysis="PASS",
            summary={"synthetic": True}, analysis_path="synthetic-no-analysis-executed")]))
        return SimpleNamespace(pid=1000, poll=lambda: 0)

    def publish(path, value):
        original_write(path, value)
        if path.name == "status.json" and value.get("status") == "running":
            visit.request(path.parent, "stop")

    monkeypatch.setattr(visit.subprocess, "Popen", spawn)
    monkeypatch.setattr(visit, "write_status", publish)
    monkeypatch.setattr(visit, "CaptureGate", lambda _: SimpleNamespace(
        observe=lambda *args, **kwargs: dict(ready=True, health="healthy")))
    monkeypatch.setattr(visit, "ArchiveStatusSource", lambda _: lambda: (None, None))
    plan = FuelPlan(CONTEXT, .5, 6, 2, 2., "synthetic race of departure and exit")
    result = visit.run_visit(tmp_path / "visits", intent="test only", present=True,
                             archive=tmp_path / "unused", plan=plan, max_seconds=2)
    assert result == 1 and len(launches) == 1
    state = json.loads((launches[0].parent / "status.json").read_text())
    assert state["status"] == "incomplete"
    assert state["problem"] == "capture_interrupted"
    assert state["clock"]["presence"] == "declared_absent"
    assert state["recovery"]["capture_interrupted"] is True


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows worker creation identity")
def test_host_recovers_real_failed_fixture_worker_without_restarting_clock(tmp_path, monkeypatch):
    from simcoach.cli import visit
    from simcoach.live.recording_storage import RecordingStorage
    from simcoach.live.supervisor import FuelPlan
    from tests.test_race_watch import CONTEXT, status
    from tests.test_visit import fixture_file

    first = tmp_path / "first"
    first.mkdir()
    bad = fixture_file(first, bad=True)
    second = tmp_path / "second"
    second.mkdir()
    good = fixture_file(second)
    value = json.loads(good.read_text())
    tick = value["ticks"][0]
    value["ticks"] = [dict(tick, t=i * .02, lap_dist_pct=.1 + i * .00001)
                      for i in range(30000)]
    good.write_text(json.dumps(value))
    launched, published, preserved = [], [], {}
    original_spawn, original_write = subprocess.Popen, visit.write_status
    original_read = visit.read_json
    storage = RecordingStorage()
    start = time.perf_counter()

    def read(path, **kwargs):
        value = original_read(path, **kwargs)
        if (value and path.name == "status.json" and path.parent in launched
                and value.get("mode") == "replay"):
            # Replay workers bypass live storage policy. Supply the current test
            # volume observation for this host recovery exercise.
            value["recording_storage"] = storage.poll(path.parent)
        return value

    def source(_):
        def observe():
            offset = time.perf_counter() - start
            snapshot = status(offset, mSpeed=20)
            snapshot.update(updated_at=datetime.fromtimestamp(time.time()-.5, UTC).isoformat(),
                            samples=int(offset * 1000)+1)
            snapshot["latest"]["mSequenceNumber"] = 2 * snapshot["samples"]
            return "a" * 64, snapshot
        return observe

    def spawn(command, **kwargs):
        # Only the child uses a fixture; the host exercises its ordinary recovery
        # policy. Neither child opens simulator shared memory or audio.
        assert len(launched) < 2
        run = Path(command[command.index("--run") + 1])
        if launched:
            for path in (launched[0] / "failure.txt", launched[0] / "closure.json"):
                preserved[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        launched.append(run)
        return original_spawn([*command, "--fixture", str(bad if len(launched) == 1 else good)],
                              **kwargs)

    def publish(path, payload):
        original_write(path, payload)
        if path.name == "status.json" and path.parent.parent == tmp_path / "visits":
            published.append(payload.copy())
            if (payload.get("status") == "running"
                    and payload.get("current_worker") == "worker-0002"
                    and payload.get("capture_ready")):
                visit.request(path.parent, "stop")  # Explicit synthetic departure.

    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    monkeypatch.setattr(visit, "read_json", read)
    monkeypatch.setattr(visit.subprocess, "Popen", spawn)
    monkeypatch.setattr(visit, "write_status", publish)
    plan = FuelPlan(CONTEXT, .5, 6, 2, 2., "synthetic source only")
    result = visit.run_visit(tmp_path / "visits", intent="synthetic recovery test", present=True,
                             archive=tmp_path / "unused", plan=plan, max_seconds=25)
    assert result == 1  # Recovery never erases the failed first attempt.
    assert len(launched) == 2
    run = launched[0].parent
    state = json.loads((run / "status.json").read_text())
    assert state["problem"] == "capture_interrupted"
    assert state["clock"]["status"] == "complete"
    assert state["clock"]["presence"] == "declared_absent"
    assert state["current_worker"] == "worker-0002"
    assert state["worker_exit"] == 0
    assert state["closure"]["recordings"][0]["validation"] == "PASS"
    assert len({row["clock"]["visit_id"] for row in published}) == 1
    assert any(row.get("capture_ready") and row.get("current_worker") == "worker-0002"
               for row in published)
    assert all(row["audio"] == "disabled" for row in published)
    assert all(row["supervision"]["fuel_state"] != "armed" for row in published
               if row.get("supervision"))
    with sqlite3.connect(run / "clock/events.sqlite3") as connection:
        events = [json.loads(row[0]) for row in connection.execute("select payload from events")]
    assert sum(e["event"] == "started" for e in events) == 1
    assert sum(e["event"] == "ended" for e in events) == 1
    receipt = json.loads((run / "outcome-result.json").read_text())
    outcome = json.loads(Path(receipt["report_path"]).read_text())
    assert len(outcome["worker_attempts"]) == 2
    assert [a["worker_exit"] for a in outcome["worker_attempts"]] == [1, 0]
    assert {r["worker_directory"] for r in outcome["recordings"]} == {"worker", "worker-0002"}
    first_ready = json.loads((run / "capture-attempts/0002/capture-ready.json").read_text())
    assert first_ready["launch_to_ready_s"] >= 0
    assert first_ready["observed_exit_to_ready_s"] >= 2
    for path, digest in preserved.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert not list(run.rglob("coach_response.json"))
