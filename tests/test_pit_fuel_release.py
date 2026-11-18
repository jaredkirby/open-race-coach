"""Synthetic pit handoffs. No simulator, headset or living process required."""

import hashlib
import json
from dataclasses import asdict

import pytest

from simcoach.live.fuel_release import check_visit_fuel
from simcoach.live.supervisor import source_identity
from tests.test_fuel_context import approve, make_engine, sample, view
from tests.test_race_watch import BASE


def garage(second, *, race=1, pit=5, speed=0, count=1, **changes):
    return sample(
        second,
        mode=1,
        participant=view(count=count, race_state=race, pit_mode=pit),
        mRaceState=race,
        mPitMode=pit,
        mSpeed=speed,
        **changes,
    ) | {"continuity_generation": 7}


def poll(engine, second, source="source-a", *, stamp=None, **changes):
    value = garage(second, **changes) if stamp is None else stamp
    return engine.observe(value, source_id=source, now=BASE + second, elapsed=second)


def test_garage_arm_survives_rollout_until_return_threshold(tmp_path):
    engine = make_engine(tmp_path, 1)
    try:
        poll(engine, 0)
        assert poll(engine, 1)["phase"] == "prestart"
        assert approve(engine)
        arm = engine.arm_id
        assert poll(engine, 2, speed=8)["fuel_state"] == "armed"
        assert poll(engine, 3, speed=10, race=2)["notice"] is None
        result = poll(engine, 4, speed=15, race=2, pit=0, litres=1.49)
        assert result["notice"]["arm_id"] == arm and engine.notice_valid(
            result["notice"], now=BASE + 4
        )
        assert poll(engine, 5)["reason"] == "race_reset"
        assert engine.arm_id is None
    finally:
        engine.journal.close()


@pytest.mark.parametrize(
    "changes", [dict(speed=0.2), dict(count=2), dict(pit=0), dict(mGameState=3)]
)
def test_only_stationary_solo_practice_exit_admits_initial_approval(tmp_path, changes):
    engine = make_engine(tmp_path, 1)
    try:
        poll(engine, 0, **changes)
        poll(engine, 1, **changes)
        assert not approve(engine)
    finally:
        engine.journal.close()


@pytest.mark.parametrize("fault", ["none", "refuel", "generation", "expiry", "movement", "source"])
def test_unarmed_checked_copy_bridge_needs_matching_advancing_pair(tmp_path, fault):
    engine = make_engine(tmp_path, 1)
    try:
        poll(engine, 0)
        poll(engine, 1)
        gap = garage(1) | dict(coverage="unstable_copy", latest_is_current=False)
        poll(engine, 1.2, stamp=gap)
        assert not approve(engine, now=BASE + 1.2)
        value = garage(
            2, litres=4.1 if fault == "refuel" else 4, speed=2 if fault == "movement" else 0
        )
        if fault == "generation":
            value["continuity_generation"] += 1
        if fault == "expiry":
            poll(engine, 3.5, stamp=gap)
        at = 4 if fault == "expiry" else 2
        first = poll(engine, at, source="other" if fault == "source" else "source-a", stamp=value)
        assert first["supervision_health"] == "degraded"
        poll(engine, at + 0.1, stamp=value)
        assert not approve(engine, now=BASE + at + 0.1)
        poll(engine, at + 1)
        assert approve(engine, now=BASE + at + 1) == (fault == "none")
    finally:
        engine.journal.close()


def test_read_only_pit_check_uses_bound_current_evidence_and_expires(tmp_path):
    def put(path, **fields):
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps(dict(schema_version=1, **fields)))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    run, archive = tmp_path / "visit", tmp_path / "archive"
    put(archive / "run.json", expected_version=14, struct_size_bytes=20700)
    identity = source_identity(archive)
    engine = make_engine(tmp_path, 1)
    now = BASE + 12
    try:
        for second in range(12):
            poll(engine, second, identity)
        assert approve(engine, verified_at=BASE + 11, now=BASE + 11, source_id=identity)
        summary = poll(engine, 12, identity)
        plan, selection = tmp_path / "plan.json", tmp_path / "selection.json"
        put(plan, **asdict(engine.plan))
        selection_hash = put(selection, device="test only")
        radio = dict(
            delivery_health="available",
            muted=False,
            closed=False,
            loss_warning_configured=True,
            output_ownership=dict(state="held"),
            configuration=dict(transport="selected_windows_wave", selection_sha256=selection_hash),
        )
        service = dict(
            status="running",
            service_id="service",
            updated_at=now,
            supervision=summary,
            radio=radio,
            chief_link=dict(state="connected", at=now),
        )
        put(run / "operations/status.json", **service)
        processes = [dict(pid=i, state="running", created_100ns=str(i)) for i in (11, 12, 13)]
        host, worker, owner = processes
        put(run / "host-process.json", process=host)
        put(run / "worker/worker-process.json", process=worker)
        put(
            run / "operations/run.json",
            service_id="service",
            process=owner,
            pinned_host=host,
            mode="local_operations",
            audio_enabled=True,
            visit_controls=True,
            visit=str(run),
            archive=str(archive),
        )
        put(
            run / "status.json",
            mode="live",
            status="running",
            updated_at=now,
            archive=str(archive),
            declared_presence=True,
            capture_ready=True,
            capture_expires_at=now + 2,
            source=dict(source_id=identity),
            current_worker="worker",
            worker_pid=worker["pid"],
            operations=dict(service_id="service", health="healthy", binding_valid=True),
        )
        put(run / "heartbeat.json", at=now)
        put(run / "worker/status.json", pid=worker["pid"], status="recording",
            active_ticks=8, updated_at=now, tick_age_s=.1, intake_active=True)
        put(run / "operations/heartbeat.json", at=now)
        command = "a" * 32
        digest = put(run / "mute.json", muted=False, command_id=command)
        put(
            run / f"receipts/{command}.json",
            applied=True,
            muted=False,
            command_fingerprint=digest,
            command_id=command,
        )
        (archive / "status.json").write_text(json.dumps(garage(12)))

        def check(at=now):
            return check_visit_fuel(
                run,
                plan,
                selection,
                clock=lambda: at,
                probe=lambda pid: next(p for p in processes if p["pid"] == pid),
            )

        assert check()["decision"] == "GO"
        assert check()["expires_at"] == now + 2
        assert check(now + 2)["decision"] == "HOLD"
        for change in (
            dict(muted=True),
            dict(closed=True),
            dict(loss_warning_configured=False),
            dict(delivery_health="degraded"),
            dict(output_ownership=dict(state="closed")),
        ):
            put(run / "operations/status.json", **(service | dict(radio=radio | change)))
            assert check()["reason"] == "fuel_radio_unavailable"
        for change, reason in [
            (dict(fuel_state="suspended"), "fuel_approval_required"),
            (dict(healthy_since=now - 1), "awaiting_stable_fuel_feed"),
        ]:
            put(run / "operations/status.json", **(service | dict(supervision=summary | change)))
            assert check()["reason"] == reason
        put(run / "operations/status.json", **service)
        (archive / "status.json").write_text(json.dumps(garage(12, speed=4)))
        assert check()["decision"] == "HOLD"
        (archive / "status.json").write_text(json.dumps(garage(12)))
        (run / "stop").touch()
        assert check()["reason"] == "stop_requested"
    finally:
        engine.journal.close()
