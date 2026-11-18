"""Explicit plan changes during a visit; no simulator or real audio."""
import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from simcoach.cli import visit
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.supervisor import FuelPlan, Journal, Supervisor
from tests.test_fuel_context import sample, view
from tests.test_fuel_radio import BASE, CONTEXT, status
from tests.test_operations_client import fake_client
from tests.test_operations_service import Rig


def replacement(mode=5):
    return FuelPlan(replace(CONTEXT, session_state=mode), .5, 5, 2, 1.5,
                    "synthetic transition", out_laps=0)


def changed(engine, now=BASE+2, **fields):
    return engine.change_plan(**(dict(plan=replacement(),
        expected_plan_id=engine.plan_status()["plan_id"], requested_at=now, now=now,
        note="Synthetic next stint") | fields))


def test_change_clears_old_notice_and_requires_new_pair_and_new_cockpit_check(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite3")
    engine = Supervisor(replacement(1), journal)
    try:
        for second in (0, 1):
            engine.observe(status(second, 8), source_id="source-a", now=BASE+second, elapsed=second)
        assert engine.arm(cockpit_litres=8, verified_at=BASE+1, now=BASE+1,
                          source_id="source-a", solo_player_confirmed=True, note="test")
        engine.observe(status(2, 1), source_id="source-a", now=BASE+2, elapsed=2)
        notice = engine.return_notice
        assert notice
        prior_id = engine.plan_status()["plan_id"]
        assert changed(engine)["accepted"]
        assert not engine.notice_valid(notice, now=BASE+2)
        assert engine.arm_id is None and engine.current is None and engine.previous is None
        assert engine.last_result["plan_id"] != prior_id
        first = engine.observe(sample(3, 4), source_id="source-a", now=BASE+3, elapsed=3)
        assert first["supervision_health"] == "degraded"
        second = engine.observe(sample(4, 4), source_id="source-a", now=BASE+4, elapsed=4)
        assert second["supervision_health"] == "healthy"
        approval = dict(cockpit_litres=4, verified_at=BASE+2, now=BASE+4,
                        source_id="source-a", player_view_confirmed=True,
                        participant_index=0, note="new synthetic cockpit check")
        assert not engine.arm(**approval)
        assert engine.approval_result["reason"] == "approval_predates_invalidation"
        assert engine.arm(**(approval | {"verified_at": BASE+4}))
        # Returning to original content must not revive an old compare-and-replace ID.
        assert changed(engine, now=BASE+4.1, plan=replacement(1))["accepted"]
        assert not changed(engine, now=BASE+4.2, expected_plan_id=prior_id)["accepted"]
    finally:
        journal.close()


@pytest.mark.parametrize("fields,reason", [
    ({"expected_plan_id": "old"}, "plan_binding_changed"),
    ({"requested_at": BASE-2}, "stale_plan_request"),
    ({"requested_at": BASE+3}, "stale_plan_request"),
    ({"note": ""}, "invalid_plan_note"),
    ({"plan": None}, "invalid_plan"),
])
def test_refused_change_preserves_plan_and_approval_state(tmp_path, fields, reason):
    journal = Journal(tmp_path / "journal.sqlite3")
    engine = Supervisor(replacement(1), journal)
    for second in (0, 1):
        engine.observe(status(second, 8), source_id="source-a", now=BASE+second, elapsed=second)
    assert engine.arm(cockpit_litres=8, verified_at=BASE+1, now=BASE+1,
                      source_id="source-a", solo_player_confirmed=True, note="test")
    prior_arm = engine.arm_id
    before = engine.plan_status()
    try:
        result = changed(engine, **fields)
        assert result["reason"] == reason and not result["accepted"]
        assert engine.plan_status() == before and engine.approval_floor is None
        assert engine.arm_id == prior_arm and engine.fuel_state == "armed"
    finally:
        journal.close()


def test_storage_failure_cannot_expose_changed_plan(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.sqlite3")
    engine = Supervisor(replacement(1), journal)
    before = engine.plan_status()
    def fail(event):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(journal, "append", fail)
    try:
        with pytest.raises(OSError):
            changed(engine)
        assert engine.plan_status() == before
    finally:
        journal.close()


def test_plan_forwarding_has_bound_receipt_and_never_replays(tmp_path):
    client, _, _, _ = fake_client(tmp_path)
    name = "b" * 32 + ".json"
    try:
        command = dict(at=1000, plan=dict(schema_version=1, **asdict(replacement())),
                       expected_plan_id="previous", note="Synthetic selection")
        client.forward_plan(name, command)
        sent = json.loads((client.directory / "requests" / name).read_text())
        assert sent["action"] == "plan" and sent["plan"] == command["plan"]
        assert not (client.run / "receipts" / name).exists()
        with pytest.raises(ValueError, match="already_exists"):
            client.forward_plan(name, command)
        write_status(client.directory / "receipts" / name, dict(
            schema_version=1, service_id=client.service_id, request_sha256=client.forwarded[name],
            accepted=True, action="plan", plan_id="next"))
        client.sync_receipts()
        receipt = json.loads((client.run / "receipts" / name).read_text())
        assert receipt["plan_id"] == "next" and receipt["authority"] == "operations_service"
    finally:
        client.close_streams()


def test_service_changes_plan_then_arms_with_new_observed_context(tmp_path):
    rig = Rig(tmp_path)
    original = rig.source
    def source():
        identity, value = original()
        if rig.seconds >= .3:
            value["latest"].update(mSessionState=5, participant_observation=view())
        return identity, value
    rig.source = source
    def commands():
        if rig.seconds == .2:
            rig.request()
        if rig.seconds == .3:
            state = json.loads((rig.out / "status.json").read_text())
            write_status(rig.out / "requests" / ("c"*32 + ".json"), dict(
                schema_version=1, service_id=state["service_id"], action="plan", at=rig.clock(),
                expected_plan_id=state["supervision"]["plan_id"],
                plan=dict(schema_version=1, **asdict(replacement())), note="Synthetic race"))
        if rig.seconds == .7:
            state = json.loads((rig.out / "status.json").read_text())
            assert state["supervision"]["fuel_state"] == "unarmed"
            write_status(rig.out / "requests" / ("d"*32 + ".json"), dict(
                schema_version=1, service_id=state["service_id"], action="fuel", at=rig.clock(),
                approval=dict(cockpit_litres=8, verified_at=rig.clock(), source_id="source-a",
                    player_view_confirmed=True, participant_index=0, note="Synthetic grid")))
    rig.callback = commands
    result = rig.run(duration=1)
    assert result["supervision"]["plan"]["context"]["session_state"] == 5
    assert result["supervision"]["fuel_state"] == "armed"
    assert len([e for e in rig.events() if e["event"] == "plan_change"]) == 1
    assert not rig.transport.started and not (rig.visit / "stop").exists()


def test_cli_plan_publishes_explicit_expected_id_and_schema(tmp_path, capsys):
    run = tmp_path / "visit"
    (run / "requests").mkdir(parents=True)
    write_status(run / "status.json", dict(status="running"))
    config = tmp_path / "plan.json"
    write_status(config, dict(schema_version=1, **asdict(replacement())))
    assert visit.main(["plan", str(run), "--plan", str(config),
                       "--expected-plan-id", "previous", "--note", "Synthetic"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "requested" and not Path(result["receipt_path"]).exists()
    sent = json.loads(next((run / "requests").glob("*.json")).read_text())
    assert sent["action"] == "plan" and sent["expected_plan_id"] == "previous"
    assert sent["plan"]["schema_version"] == 1


def test_embedded_visit_changes_plan_without_restarting_capture(tmp_path, monkeypatch):
    from simcoach.live import recording_storage

    monkeypatch.setattr(
        recording_storage, "free_bytes", lambda _: 4 * recording_storage.MIN_FREE_BYTES)
    workers, observations = [], []
    mode, counter, stage = [1], [0], [0]
    class Child:
        pid = 1234
        def __init__(self, directory):
            self.directory = directory
            self.storage = visit.RecordingStorage()
        def poll(self):
            if (self.directory.parent / "stop").exists():
                write_status(self.directory / "status.json", dict(
                    schema_version=1, status="complete", stop_reason="visit_stop_requested"))
                return 0
            write_status(self.directory / "status.json", dict(
                schema_version=1, status="recording", active_ticks=10, intake_active=True,
                updated_at=time.time(), tick_age_s=.01,
                recording_storage=self.storage.poll(self.directory)))
            return None
    def spawn(command, **kwargs):
        directory = Path(command[command.index("--run")+1])
        directory.mkdir()
        workers.append(directory)
        write_status(directory / "closure.json", dict(schema_version=1, recordings=[dict(
            path=str(directory / "synthetic"), validation="PASS", analysis="PASS",
            summary={"synthetic": True}, analysis_path="synthetic-no-analysis")]))
        return Child(directory)
    source_started = time.perf_counter()
    def source(_):
        def read():
            counter[0] += 1
            value = sample(counter[0], litres=8, mode=mode[0])
            value["updated_at"] = datetime.now(UTC).isoformat()
            value["offset_s"] = time.perf_counter() - source_started
            return "source-a", value
        return read
    original = visit.write_status
    def publish(path, value):
        original(path, value)
        if (path.name != "status.json" or path.parent.parent != tmp_path / "visits"
                or value.get("status") != "running"):
            return
        observations.append(value)
        supervision = value["supervision"]
        if stage[0] == 0 and value["capture_ready"]:
            visit.request(path.parent, "plan", plan=dict(schema_version=1, **asdict(replacement())),
                          expected_plan_id=supervision["plan_id"], note="Synthetic race")
            mode[0], stage[0] = 5, 1
        elif stage[0] == 1 and supervision["plan"]["context"]["session_state"] == 5:
            assert supervision["fuel_state"] == "unarmed"
            assert value["clock"]["presence"] == "declared_present"
            if supervision["supervision_health"] == "healthy":
                visit.request(path.parent, "fuel", cockpit_litres=8, verified_at=time.time(),
                              source_id="source-a", solo_player_confirmed=False,
                              player_view_confirmed=True, participant_index=0, note="Synthetic")
                stage[0] = 2
        elif stage[0] == 2 and supervision["fuel_state"] == "armed":
            stage[0] = 3
            visit.request(path.parent, "stop")
    monkeypatch.setattr(visit.subprocess, "Popen", spawn)
    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    monkeypatch.setattr(visit, "write_status", publish)
    code = visit.run_visit(tmp_path / "visits", intent="Synthetic embedded plan test",
                           present=True, archive=tmp_path / "unused", plan=replacement(1),
                           max_seconds=5)
    assert code == 0 and stage[0] == 3 and len(workers) == 1
    assert any(row["source"]["expected_context_match"] for row in observations
               if row["supervision"]["plan"]["context"]["session_state"] == 5)
