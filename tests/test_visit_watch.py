"""Offline liveness monitor evidence; no simulator or audio control."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live import visit_watch
from simcoach.live.visit_recovery import inspect_visit
from simcoach.live.visit_watch import VisitLiveness, watch_visit
from tests.test_visit_recovery import CREATED, process
from tests.test_visit_recovery import make_visit as make_receipts


def make_visit(path):
    path.mkdir(exist_ok=True)
    return make_receipts(path)


def report(run, at=1001, probe=process):
    return inspect_visit(run, probe=probe, clock=lambda: at)


def beat(run, at):
    write_status(run / "heartbeat.json", dict(schema_version=1, at=at))


def test_fresh_progress_required_and_identical_heartbeat_expires(tmp_path):
    run = make_visit(tmp_path)
    policy = VisitLiveness()
    assert policy.observe(report(run))["state"] == "awaiting_progress"
    beat(run, 1001)
    assert policy.observe(report(run, 1002))["state"] == "responsive"
    assert policy.observe(report(run, 1003))["state"] == "responsive"
    assert policy.observe(report(run, 1004))["state"] == "unresponsive"
    beat(run, 1004)
    assert policy.observe(report(run, 1004))["state"] == "awaiting_progress"
    beat(run, 1005)
    assert policy.observe(report(run, 1005))["state"] == "responsive"


def test_dead_recorded_host_not_hidden_by_fresh_heartbeat_or_live_worker(tmp_path):
    run = make_visit(tmp_path)
    value = VisitLiveness().observe(report(run, probe=lambda pid: process(
        pid, "absent" if pid == 10 else "running", None if pid == 10 else CREATED)))
    assert value["state"] == "stopped" and value["worker"]["state"] == "running"
    assert value["driver_departure_inferred"] is False and value["actions_taken"] == []


def test_access_denial_is_unknown_not_exit(tmp_path):
    run = make_visit(tmp_path)
    result = VisitLiveness().observe(report(run, probe=lambda pid: dict(
        process(pid), state="unknown", reason="open_process_error_5")))
    assert result["state"] == "unknown"


def test_reused_pid_and_changed_receipt_have_distinct_evidence(tmp_path):
    run = make_visit(tmp_path)
    policy = VisitLiveness()
    policy.observe(report(run))
    reused = policy.observe(report(
        run, probe=lambda pid: process(pid, created=str(int(CREATED)+1))))
    assert reused["state"] == "stopped" and reused["host"]["reason"] == "pid_reused"
    write_status(run / "host-process.json", dict(schema_version=1, process=process(30)))
    assert policy.observe(report(run))["state"] == "binding_changed"
    write_status(run / "host-process.json", dict(schema_version=1, process=process(10)))
    assert policy.observe(report(run))["state"] == "binding_changed"


@pytest.mark.parametrize("status,expected", [("closing", "closing_reported"),
                                             ("complete", "terminal_reported"),
                                             ("incomplete", "terminal_reported")])
def test_reported_completion_does_not_claim_exit_or_capture_success(tmp_path, status, expected):
    run = make_visit(tmp_path)
    write_status(run / "status.json", dict(schema_version=1, status=status))
    result = VisitLiveness().observe(report(run, at=1010))
    assert result["state"] == expected
    assert result["host"]["state"] == "running" and result["capture_health_verified"] is False


def test_missing_receipt_after_binding_does_not_follow_unverified_owner(tmp_path):
    run = make_visit(tmp_path)
    policy = VisitLiveness()
    policy.observe(report(run))
    (run / "host-process.json").unlink()
    assert policy.observe(report(run))["state"] == "unknown"
    assert policy.identity == dict(pid=10, created_100ns=CREATED)


def fake_clock():
    value = [0.0]
    return value, dict(clock=lambda: 1001 + value[0], elapsed=lambda: value[0],
                      sleep=lambda seconds: value.__setitem__(0, value[0] + seconds))


def test_bounded_monitor_retains_hashed_transitions_without_touching_visit(tmp_path):
    run = make_visit(tmp_path / "visit")
    before = {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    elapsed, clocks = fake_clock()
    def probe(pid):
        return process(pid, "absent" if pid == 10 and elapsed[0] >= 1 else "running")

    out = tmp_path / "monitor"
    result = watch_visit(run, out, duration=2, interval=.25, probe=probe, **clocks)
    assert result["status"] == "complete" and result["state"] == "stopped"
    assert result["observations"] == 9
    assert before == {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with sqlite3.connect(out / "events.sqlite3") as c:
        rows = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events")]
    transitions = [r for r in rows if r["event"] == "liveness_transition"]
    assert [r["result"]["state"] for r in transitions] == ["awaiting_progress", "stopped"]
    for row in transitions:
        digest = hashlib.sha256((out / row["evidence"]).read_bytes()).hexdigest()
        assert digest == row["evidence_sha256"]
    assert rows[-1]["reason"] == "duration_elapsed"


def test_inspection_timeout_or_read_failure_does_not_become_process_death(tmp_path):
    run = make_visit(tmp_path / "visit")
    _, clocks = fake_clock()
    def fail(*args, **kwargs):
        raise OSError("synthetic read failed")

    result = watch_visit(run, tmp_path / "monitor", duration=.5, inspect=fail, **clocks)
    assert result["state"] == "unknown" and result["reason"] == "inspection_failed"


def test_output_failure_propagates_without_mutating_source(tmp_path):
    run = make_visit(tmp_path / "visit")
    _, clocks = fake_clock()
    def fail(path, value):
        if path.name == "status.json":
            raise OSError("synthetic full disk")
        write_status(path, value)

    with pytest.raises(OSError):
        watch_visit(run, tmp_path / "monitor", duration=1, probe=process, publish=fail, **clocks)
    assert (tmp_path / "monitor/observation-00001.json").exists()
    assert json.loads((run / "status.json").read_text())["status"] == "running"


def test_terminal_journal_failure_does_not_publish_completed_monitor(tmp_path, monkeypatch):
    run = make_visit(tmp_path / "visit")
    out = tmp_path / "monitor"
    _, clocks = fake_clock()
    original_append = visit_watch.Journal.append

    def append(self, event):
        if event["event"] == "monitor_stopped":
            raise OSError("synthetic journal storage failure")
        return original_append(self, event)

    monkeypatch.setattr(visit_watch.Journal, "append", append)
    with pytest.raises(OSError, match="synthetic journal"):
        watch_visit(run, out, duration=.5, probe=process, **clocks)
    assert json.loads((out / "status.json").read_text())["status"] == "watching"
    with sqlite3.connect(out / "events.sqlite3") as connection:
        rows = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM events")]
    assert rows[-1]["event"] == "monitor_failed"
    assert (out / rows[0]["evidence"]).is_file()


def test_monitor_observation_gap_retained_without_inventing_host_failure(tmp_path):
    run = make_visit(tmp_path / "visit")
    elapsed, clocks = fake_clock()
    original_sleep = clocks["sleep"]
    calls = [0]

    def sleep(seconds):
        calls[0] += 1
        original_sleep(4 if calls[0] == 1 else seconds)
        beat(run, 1001 + elapsed[0])

    clocks["sleep"] = sleep
    result = watch_visit(run, tmp_path / "monitor", duration=5, interval=.25,
                         probe=process, **clocks)
    states = [json.loads(p.read_text())["result"]
              for p in sorted((tmp_path / "monitor").glob("observation-*.json"))]
    gap = next(s for s in states if s["reason"] == "monitor_observation_gap")
    assert gap["state"] == "unknown" and gap["observation_gap_s"] == 4
    assert gap["host"]["state"] == "running" and result["state"] == "responsive"


def test_worker_exit_is_retained_even_while_host_state_does_not_change(tmp_path):
    run = make_visit(tmp_path / "visit")
    elapsed, clocks = fake_clock()

    def probe(pid):
        return process(pid, "absent" if pid == 20 and elapsed[0] >= .5 else "running")

    watch_visit(run, tmp_path / "monitor", duration=1, interval=.25, probe=probe, **clocks)
    states = [json.loads(p.read_text())["result"]
              for p in sorted((tmp_path / "monitor").glob("observation-*.json"))]
    assert [s["worker"]["state"] for s in states] == ["running", "absent"]


def test_wait_past_deadline_cannot_republish_pre_gap_responsiveness(tmp_path):
    run = make_visit(tmp_path / "visit")
    elapsed, clocks = fake_clock()
    calls = [0]

    def sleep(seconds):
        calls[0] += 1
        elapsed[0] += 10 if calls[0] == 2 else seconds
        beat(run, 1001 + elapsed[0])

    clocks["sleep"] = sleep
    out = tmp_path / "monitor"
    result = watch_visit(run, out, duration=1, interval=.25, probe=process, **clocks)
    states = [json.loads(p.read_text())["result"]
              for p in sorted(out.glob("observation-*.json"))]
    assert states[1]["state"] == "responsive"
    assert result["status"] == "complete" and result["state"] == "unknown"
    assert result["reason"] == "monitor_observation_gap"
    assert result["observation_gap_s"] == 10
    assert states[-1]["state"] == "unknown" and result["observations"] == 3


def test_heartbeat_regression_requires_new_progress(tmp_path):
    run = make_visit(tmp_path)
    policy = VisitLiveness()
    policy.observe(report(run))
    beat(run, 1001)
    assert policy.observe(report(run, 1002))["state"] == "responsive"
    beat(run, 1000.5)
    assert policy.observe(report(run, 1002))["reason"] == "heartbeat_regressed"
    assert policy.observe(report(run, 1002))["state"] == "awaiting_progress"
    beat(run, 1002)
    assert policy.observe(report(run, 1002))["state"] == "responsive"


@pytest.mark.parametrize("destination", ["self", "inside", "parent"])
def test_output_must_not_write_in_or_over_visit_tree(tmp_path, destination):
    run = make_visit(tmp_path / "visit")
    out = {"self": run, "inside": run / "monitor", "parent": tmp_path}[destination]
    with pytest.raises(ValueError, match="monitor_output_must_be_separate"):
        watch_visit(run, out)


@pytest.mark.skipif(os.name != "nt", reason="Windows owned-child process identity integration")
def test_observes_owned_child_crash_without_restarting_or_controlling_worker(tmp_path):
    run = tmp_path / "visit"
    run.mkdir()
    script = """
import os, sys, time
from pathlib import Path
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.visit_recovery import probe_process
p = Path(sys.argv[1])
write_status(p/'host-process.json', dict(schema_version=1, process=probe_process(os.getpid())))
write_status(p/'status.json', dict(schema_version=1, status='running'))
deadline=time.perf_counter()+20
while time.perf_counter()<deadline:
    write_status(p/'heartbeat.json', dict(schema_version=1, at=time.time()))
    if (p/'crash-test-only').exists(): os._exit(7)
    time.sleep(.05)
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(run)],
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.perf_counter() + 10
        while not (run / "heartbeat.json").exists() and time.perf_counter() < deadline:
            assert child.poll() is None
            time.sleep(.05)
        assert (run / "heartbeat.json").exists()
        started = time.perf_counter()
        def inspect(*args, **kwargs):
            value = inspect_visit(*args, **kwargs)
            if time.perf_counter() - started >= .5:
                (run / "crash-test-only").touch()  # Test fixture requests its own crash.
            return value

        result = watch_visit(run, tmp_path / "monitor", duration=1.5, interval=.1, inspect=inspect)
        assert child.wait(timeout=5) == 7
        assert result["state"] == "stopped" and result["audio_output_attempted"] is False
        states = [json.loads(p.read_text())["result"]["state"]
                  for p in sorted((tmp_path / "monitor").glob("observation-*.json"))]
        assert "responsive" in states and states[-1] == "stopped"
    finally:
        if child.poll() is None:
            (run / "crash-test-only").touch()
            child.wait(timeout=25)
        child.communicate()
