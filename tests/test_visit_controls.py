import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.visit_controls import VisitControls, main


def make_visit(tmp_path, now=1000):
    run = tmp_path/"visit"
    run.mkdir()
    (run/"requests").mkdir()
    (run/"receipts").mkdir()
    write_status(run/"status.json", dict(schema_version=1, status="running",
                                         updated_at=now, declared_presence=True))
    write_status(run/"heartbeat.json", dict(schema_version=1, at=now))
    return run


def controller(run):
    return VisitControls(run, clock=lambda: 1000, elapsed=lambda: 500)


def read(path):
    return json.loads(path.read_text())


def test_capture_banner_expires_during_control_status_read(tmp_path, monkeypatch):
    from simcoach.live import visit_controls

    run = make_visit(tmp_path)
    state = read(run / "status.json")
    write_status(run / "status.json", dict(state, capture_ready=True, capture_expires_at=1000.5))
    now = [1000.0]
    client = VisitControls(run, clock=lambda: now[0], elapsed=lambda: 500)
    assert client.status()["capture_ready"] is True
    read_status = visit_controls._read

    def slow_heartbeat(path):
        result = read_status(path)
        if path.name == "heartbeat.json":
            now[0] = 1000.5
        return result

    monkeypatch.setattr(visit_controls, "_read", slow_heartbeat)
    result = client.status()
    assert result["controllable"] is True
    assert result["capture_ready"] is False


def test_priority_mute_and_unmute_preserve_each_intent_without_queued_replay(tmp_path):
    run = make_visit(tmp_path)
    client = controller(run)
    first = client.send("mute", muted=True)
    first_bytes = (run/"controls"/f"{first['command_id']}.intent.json").read_bytes()
    assert read(run/"mute.json")["muted"] is True
    client.clock = lambda: 1000.1
    second = client.send("mute", muted=False)
    assert read(run/"mute.json")["command_id"] == second["command_id"]
    assert read(run/"mute.json")["muted"] is False
    assert not list((run/"requests").iterdir())
    assert (run/"controls"/f"{first['command_id']}.intent.json").read_bytes() == first_bytes
    assert first["state"] == second["state"] == "requested"
    assert client.receipt(second["command_id"]) is None


@pytest.mark.parametrize("mutation", [
    "stale_status", "stale_heartbeat", "future", "closing", "absent",
])
def test_unmute_reason_departure_refused_but_inhibition_available(tmp_path, mutation):
    run = make_visit(tmp_path)
    status, heartbeat = read(run/"status.json"), read(run/"heartbeat.json")
    if mutation == "stale_status":
        status["updated_at"] = 997
    elif mutation == "stale_heartbeat":
        heartbeat["at"] = 997
    elif mutation == "future":
        heartbeat["at"] = 1001
    elif mutation == "closing":
        status["status"] = "closing"
    else:
        status["declared_presence"] = False
    write_status(run/"status.json", status)
    write_status(run/"heartbeat.json", heartbeat)
    client = controller(run)
    for action, fields in (("mute", {"muted": False}), ("stop", {}),
                           ("reason", {"reason": "setup"})):
        result = client.send(action, **fields)
        assert result["state"] == "refused"
        assert read(run/"controls"/f"{result['command_id']}.result.json") == result
    assert not (run/"stop").exists()
    assert not (run/"mute.json").exists()
    assert client.send("mute", muted=True)["state"] == "requested"
    assert read(run/"mute.json")["muted"] is True


def test_departure_preserves_first_explicit_boundary(tmp_path):
    run = make_visit(tmp_path)
    client = controller(run)
    result = client.send("stop")
    boundary = read(run/"stop")
    assert boundary == dict(at=1000, elapsed=500, declaration="driver_left",
                            command_id=result["command_id"])
    later = VisitControls(run, clock=lambda: 1001, elapsed=lambda: 501).send("stop")
    assert later["state"] == "refused"
    assert later["reason"] == "departure_already_requested"
    assert read(run/"stop") == boundary
    assert client.send("mute", muted=False)["state"] == "refused"
    assert client.send("mute", muted=True)["state"] == "requested"


def test_reason_queue_and_acknowledgment_are_distinct(tmp_path):
    run = make_visit(tmp_path)
    client = controller(run)
    result = client.send("reason", reason="intentional_rest")
    command_id = result["command_id"]
    assert read(run/"requests"/f"{command_id}.json")["reason"] == "intentional_rest"
    assert client.receipt(command_id) is None
    write_status(run/"receipts"/f"{command_id}.json", dict(accepted=True, reason=None))
    assert client.receipt(command_id)["accepted"] is True
    with pytest.raises(ValueError, match="invalid_command_id"):
        client.receipt("../status")


def test_departure_uses_locked_boundary_and_preserves_earlier_intent(tmp_path, monkeypatch):
    from simcoach.live import visit_controls

    run = make_visit(tmp_path)
    now = [1000.0]
    original = visit_controls.write_status

    def delayed_audit(path, value):
        original(path, value)
        if path.name.endswith(".intent.json"):
            now[0] += .2  # A host observation could commit during this audit write.

    monkeypatch.setattr(visit_controls, "write_status", delayed_audit)
    client = VisitControls(run, clock=lambda: now[0], elapsed=lambda: now[0] - 500)
    result = client.send("stop")
    intent = read(run / "controls" / f"{result['command_id']}.intent.json")
    boundary = read(run / "stop")
    assert intent["at"] == 1000 and intent["elapsed"] == 500
    assert boundary["at"] == result["boundary_at"] == 1000.2
    assert boundary["elapsed"] == result["boundary_elapsed"] == pytest.approx(500.2)


def test_mute_status_requires_fresh_coordinator_report(tmp_path):
    run = make_visit(tmp_path)
    status = read(run/"status.json")
    status["radio"] = {"muted": True}
    write_status(run/"status.json", status)
    assert controller(run).status()["reported_mute"] is True
    status["updated_at"] = 990
    write_status(run/"status.json", status)
    assert controller(run).status()["reported_mute"] is None


def test_separate_process_respects_request_lock_and_never_imports_gui(tmp_path):
    run = make_visit(tmp_path, now=time.time())
    code = (
        "import json, sys; from pathlib import Path; "
        "from simcoach.live.visit_controls import VisitControls; "
        "assert 'tkinter' not in sys.modules; "
        "print(json.dumps(VisitControls(Path(sys.argv[1])).send('mute', muted=True)))"
    )
    lock = SourceLock(run/"request.lock")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        refused = subprocess.run([sys.executable, "-c", code, str(run)], capture_output=True,
                                 text=True, timeout=15, check=True, creationflags=flags)
    finally:
        lock.close()
    assert json.loads(refused.stdout)["reason"] == "control_writer_busy"
    assert not (run/"mute.json").exists()
    allowed = subprocess.run([sys.executable, "-c", code, str(run)], capture_output=True,
                             text=True, timeout=15, check=True, creationflags=flags)
    assert json.loads(allowed.stdout)["state"] == "requested"
    assert read(run/"mute.json")["muted"] is True


def test_invalid_commands_and_unknown_directory_cannot_create_visit(tmp_path):
    with pytest.raises(FileNotFoundError):
        VisitControls(tmp_path/"missing")
    assert not (tmp_path/"missing").exists()
    run = make_visit(tmp_path)
    client = controller(run)
    for action, fields in (("mute", {"muted": 1}), ("reason", {"reason": "wasted"}),
                           ("start", {}), ("stop", {"elapsed": 1})):
        with pytest.raises(ValueError):
            client.send(action, **fields)
    assert not (run/"controls").exists()


def test_explicit_cli_opens_injected_window_without_starting_visit(tmp_path, monkeypatch):
    from simcoach.live import visit_controls

    run = make_visit(tmp_path, now=time.time())
    opened = []
    monkeypatch.setattr(visit_controls, "show_window", lambda client: opened.append(client.run))
    assert main(["--visit", str(run)]) == 0
    assert opened == [run.resolve()]
    assert not (run/"controls").exists()


def test_failed_priority_write_keeps_audit_and_previous_state(tmp_path, monkeypatch):
    from simcoach.live import visit_controls

    run = make_visit(tmp_path)
    client = controller(run)
    client.send("mute", muted=True)
    before = (run/"mute.json").read_bytes()
    write = visit_controls.write_status

    def fail_priority(path, value):
        if Path(path).name == "mute.json":
            raise OSError("synthetic write failure")
        write(path, value)

    monkeypatch.setattr(visit_controls, "write_status", fail_priority)
    client.clock = lambda: 1000.1
    result = client.send("mute", muted=False)
    assert result["state"] == "write_failed"
    assert result["reason"] == "OSError"
    assert read(run/"controls"/f"{result['command_id']}.result.json") == result
    assert (run/"mute.json").read_bytes() == before


def test_delayed_unmute_cannot_overwrite_newer_mute_before_host_observes(tmp_path, monkeypatch):
    from simcoach.live import visit_controls

    run = make_visit(tmp_path)
    older = controller(run)
    newer = VisitControls(run, clock=lambda: 1000.1, elapsed=lambda: 500.1)
    write = visit_controls.write_status
    newer_result = []

    def interleave(path, value):
        write(path, value)
        if str(path).endswith(".intent.json") and value.get("muted") is False:
            newer_result.append(newer.send("mute", muted=True))

    monkeypatch.setattr(visit_controls, "write_status", interleave)
    result = older.send("mute", muted=False)
    assert result["state"] == "refused"
    assert result["reason"] == "obsolete_unmute_intent"
    assert read(run/"mute.json")["command_id"] == newer_result[0]["command_id"]
    assert read(run/"mute.json")["muted"] is True


def test_older_inhibit_preserves_high_watermark(tmp_path):
    run = make_visit(tmp_path)
    VisitControls(run, clock=lambda: 1000.2).send("mute", muted=True)
    assert controller(run).send("mute", muted=True)["state"] == "requested"
    assert read(run/"mute.json")["at"] == 1000
    assert read(run/"mute.json")["intent_watermark"] == 1000.2
    result = VisitControls(run, clock=lambda: 1000.1).send("mute", muted=False)
    assert result["reason"] == "obsolete_unmute_intent"
    fresh = VisitControls(run, clock=lambda: 1000.3).send("mute", muted=False)
    assert fresh["state"] == "requested"


@pytest.mark.parametrize("contents", ["{", '{"schema_version":1,"muted":true}'])
def test_corrupt_priority_state_refuses_unmute_but_allows_inhibition(tmp_path, contents):
    run = make_visit(tmp_path)
    (run/"mute.json").write_text(contents)
    assert controller(run).send("mute", muted=False)["reason"] == "mute_state_unreadable"
    assert controller(run).send("mute", muted=True)["state"] == "requested"
    assert read(run/"mute.json")["muted"] is True
