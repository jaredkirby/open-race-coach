"""Visit/service binding, handoff receipts and bounded real child closure."""
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.operations_client import OperationsClient
from simcoach.live.supervisor import FuelPlan, source_identity
from simcoach.live.visit_outcome import build_visit_outcome
from simcoach.live.visit_recovery import probe_process
from tests.test_fuel_radio import CONTEXT, status
from tests.test_visit_outcome import visit as outcome_visit
from tests.test_visit_recovery import CREATED, process


def plan():
    return FuelPlan(CONTEXT, .5, 6, 2, 2, "synthetic client test")


def fake_client(tmp_path):
    run = tmp_path / "visit"
    run.mkdir()
    now = [1000.0]
    code = [None]
    calls = []
    def spawn(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=10, poll=lambda: code[0])
    client = OperationsClient(run, archive=tmp_path, plan=plan(), duration=10,
                              popen=spawn, probe=process, clock=lambda: now[0])
    (run / "receipts").mkdir()
    (run / "operations/requests").mkdir(parents=True)
    (run / "operations/receipts").mkdir()
    identity = "a" * 32
    write_status(run / "operations/run.json", dict(schema_version=1, service_id=identity,
                                                  visit=str(run), process=process(20)))
    write_status(run / "operations/status.json", dict(
        schema_version=1, service_id=identity, status="running", updated_at=now[0],
        chief_link={"state": "connected"}, radio={"closed": False}))
    write_status(run / "operations/heartbeat.json", dict(schema_version=1, at=now[0]))
    return client, now, code, calls


def command(now=1000):
    return dict(at=now, cockpit_litres=8, verified_at=now, source_id="source-a",
                solo_player_confirmed=True, note="Synthetic command only")


def test_forwarded_fuel_is_not_accepted_until_matching_service_receipt(tmp_path):
    client, now, _, calls = fake_client(tmp_path)
    try:
        assert client.poll()["health"] == "healthy"
        name = "b" * 32 + ".json"
        client.forward_fuel(name, command())
        assert not (client.run / "receipts" / name).exists()
        sent = client.directory / "requests" / name
        digest = hashlib.sha256(sent.read_bytes()).hexdigest()
        receipt = dict(schema_version=1, service_id=client.service_id,
                       request_sha256="wrong", accepted=True)
        write_status(client.directory / "receipts" / name, receipt)
        client.sync_receipts()
        assert name in client.forwarded
        receipt["request_sha256"] = digest
        write_status(client.directory / "receipts" / name, receipt)
        client.sync_receipts()
        value = json.loads((client.run / "receipts" / name).read_text())
        assert value["accepted"] and value["authority"] == "operations_service"
        assert len(calls) == 1 and "--visit-controls" in calls[0][0]
        if os.name == "nt":
            assert calls[0][1]["creationflags"]
    finally:
        client.close_streams()


@pytest.mark.parametrize("fault", ["stale", "denied", "exited", "rebound"])
def test_stale_or_unverified_service_cannot_take_new_approval(tmp_path, fault):
    client, now, code, calls = fake_client(tmp_path)
    try:
        client.poll()
        if fault == "stale":
            now[0] += 4
        elif fault == "denied":
            client.probe = lambda pid: dict(process(pid), state="unknown")
        elif fault == "exited":
            code[0] = 1
        else:
            write_status(client.directory / "run.json", dict(schema_version=1,
                         service_id="a" * 32, visit=str(client.run),
                         process=process(20, created=str(int(CREATED) + 1))))
        with pytest.raises(ValueError, match="unavailable"):
            client.forward_fuel("c.json", command(now[0]))
        assert len(calls) == 1 and not (client.directory / "requests/c.json").exists()
    finally:
        client.close_streams()


def test_terminal_requires_actual_exit_and_matching_service_identity(tmp_path):
    client, _, code, _ = fake_client(tmp_path)
    try:
        client.poll()
        write_status(client.directory / "status.json", dict(
            schema_version=1, service_id="a" * 32, status="complete", radio={"closed": True}))
        code[0] = 0
        assert not client.terminal()  # Launcher exit is not actual service exit.
        client.probe = lambda pid: process(pid, "absent")
        assert client.terminal()
        write_status(client.directory / "run.json", dict(schema_version=1,
                     service_id="a" * 32, visit="another-visit", process=process(20)))
        assert not client.terminal()
    finally:
        client.close_streams()


def test_ambiguous_launch_is_retained_and_never_retried(tmp_path):
    calls = []
    def spawn(*args, **kwargs):
        calls.append(1)
        raise OSError("synthetic ambiguous launch")
    with pytest.raises(OSError):
        OperationsClient(tmp_path, archive=tmp_path, plan=plan(), duration=10,
                         popen=spawn, probe=process)
    assert calls == [1] and (tmp_path / "operations-intent.json").exists()
    assert not (tmp_path / "operations-spawn.json").exists()


def test_ambiguous_command_publication_is_never_retried_or_reported_rejected(tmp_path, monkeypatch):
    from simcoach.live import operations_client
    client, _, _, _ = fake_client(tmp_path)
    original = operations_client._publish
    name = "d" * 32 + ".json"
    calls = []
    def uncertain(path, value):
        original(path, value)
        if path == client.directory / "requests" / name:
            calls.append(path)
            raise OSError("synthetic post-publication failure")
    monkeypatch.setattr(operations_client, "_publish", uncertain)
    try:
        assert client.forward_fuel(name, command()) is False
        assert name in client.forwarded and name in client.submission_errors
        assert not (client.run / "receipts" / name).exists()
        with pytest.raises(ValueError, match="already_exists"):
            client.forward_fuel(name, command())
        assert len(calls) == 1
        assert hashlib.sha256((client.directory / "requests" / name).read_bytes()).hexdigest() == (
            client.forwarded[name])
    finally:
        client.close_streams()


def test_receipt_failure_stays_pending_without_resubmitting(tmp_path, monkeypatch):
    from simcoach.live import operations_client
    client, _, _, _ = fake_client(tmp_path)
    name = "e" * 32 + ".json"
    try:
        client.forward_fuel(name, command())
        source = client.directory / "requests" / name
        original_request = source.read_bytes()
        receipt = dict(schema_version=1, service_id=client.service_id, accepted=True,
                       request_sha256=client.forwarded[name])
        write_status(client.directory / "receipts" / name, receipt)
        original = operations_client._publish
        def fail(path, value):
            if path == client.run / "receipts" / name:
                raise PermissionError("synthetic receipt lock")
            return original(path, value)
        monkeypatch.setattr(operations_client, "_publish", fail)
        client.sync_receipts()
        assert client.last["receipt_errors"][name] == "PermissionError"
        assert name in client.forwarded and source.read_bytes() == original_request
        monkeypatch.setattr(operations_client, "_publish", original)
        client.sync_receipts()
        assert not client.forwarded and source.read_bytes() == original_request
        assert json.loads((client.run / "receipts" / name).read_text())["accepted"] is True
    finally:
        client.close_streams()


def test_outcome_binds_service_radio_and_keeps_forwarding_and_failed_evidence(tmp_path):
    run = outcome_visit(tmp_path)
    (run / "operations").mkdir()
    (run / "operations-forwarded").mkdir()
    parent = json.loads((run / "status.json").read_text())
    parent.update(operations_mode="service", operations=dict(
        service_id="service", binding_valid=True))
    write_status(run / "status.json", parent)
    write_status(run / "operations/run.json", dict(schema_version=1, service_id="service",
                                                  visit=str(run)))
    write_status(run / "operations/status.json", dict(schema_version=1, service_id="service",
                                                     status="incomplete"))
    (run / "operations/supervision.sqlite3").write_bytes(b"synthetic failed database")
    (run / "operations/radio.jsonl").write_text("corrupt retained journal")
    write_status(run / "operations-forwarded/a.json", dict(schema_version=1, request_sha256="a"))
    report = build_visit_outcome(run)
    assert report["operations"]["binding_reported"]
    assert report["operations"]["status"]["status"] == "incomplete"
    assert len(report["operations"]["forwarding"]) == 1
    assert report["operations"]["supervision_journal"]["sha256"]
    assert not report["radio"]["known"]


@pytest.mark.skipif(os.name != "nt", reason="Windows actual child identity")
@pytest.mark.parametrize("player_view", [False, True])
def test_actual_client_launches_service_routes_approval_and_observes_closure(tmp_path, player_view):
    run, archive = tmp_path / "visit", tmp_path / "archive"
    run.mkdir()
    (run / "receipts").mkdir()
    archive.mkdir()
    write_status(run / "host-process.json", dict(schema_version=1,
                                                process=probe_process(os.getpid())))
    write_status(run / "status.json", dict(schema_version=1, status="running",
                                          declared_presence=True))
    write_status(archive / "run.json", dict(schema_version=1, expected_version=14,
                                            struct_size_bytes=20700))
    source_id = source_identity(archive)
    started = time.time()
    def publish():
        now = time.time()
        write_status(run / "heartbeat.json", dict(schema_version=1, at=now))
        stamp = datetime.fromtimestamp(now, UTC)
        if stamp.timestamp() > now:
            stamp -= timedelta(microseconds=1)
        seconds = now - started
        value = status(seconds, 8)
        if player_view:
            value["latest"].update(mSessionState=5, participant_observation=dict(
                schema_version=1, index=0, count=3, active=True, car=CONTEXT.car,
                race_state=2, pit_mode=0))
        value.update(updated_at=stamp.isoformat(), samples=int(seconds * 10000) + 1)
        value["latest"]["mSequenceNumber"] = value["samples"] * 2
        write_status(archive / "status.json", value)
    publish()
    config = replace(plan(), context=replace(CONTEXT, session_state=5), out_laps=0) \
        if player_view else plan()
    client = OperationsClient(run, archive=archive, plan=config, duration=8)
    try:
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            publish()
            observed = client.poll()
            if (observed["health"] == "healthy"
                    and observed["state"]["chief_link"]["state"] == "connected"
                    and observed["state"]["supervision"]["supervision_health"] == "healthy"):
                break
            time.sleep(.05)
        else:
            pytest.fail("actual operations service did not become observable")
        request = command(time.time())
        request["source_id"] = source_id
        if player_view:
            request.update(solo_player_confirmed=False, player_view_confirmed=True,
                           participant_index=0)
        name = "a" * 32 + ".json"
        client.forward_fuel(name, request)
        deadline = time.perf_counter() + 3
        while name in client.forwarded and time.perf_counter() < deadline:
            publish()
            client.sync_receipts()
            time.sleep(.05)
        receipt = json.loads((run / "receipts" / name).read_text())
        assert receipt["accepted"] and receipt["authority"] == "operations_service"
        write_status(run / "stop", dict(at=time.time(), declaration="driver_left"))
        assert client.process.wait(timeout=5) == 0
        assert client.terminal()
    finally:
        if client.process.poll() is None:
            write_status(run / "stop", dict(at=time.time(), declaration="driver_left"))
            client.process.wait(timeout=10)
        client.close_streams()
