from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from simcoach.cli.race_watch import SourceLock, main, replay
from simcoach.live.supervisor import Context, FuelPlan, Journal, Supervisor, strict_json

BASE = 1_800_000_000.0
CONTEXT = Context("test car", "test track", "short", 1)


def status(second, *, litres=8.0, **latest):
    return dict(
        schema_version=1, status="running", updated_at=datetime.fromtimestamp(
            BASE + second, UTC).isoformat(), offset_s=float(second), samples=int(second) * 60 + 1,
        coverage="available", latest_is_current=True, target_hz=60, error=None,
        latest=dict(mVersion=14, mSequenceNumber=2 * int(second) + 2,
                    mFuelCapacity=33, mFuelLevel=litres / 33, mCarName=CONTEXT.car,
                    mTrackLocation=CONTEXT.track, mTrackVariation=CONTEXT.variation,
                    mSessionState=1, mGameState=2, mRaceState=2, mPitMode=0,
                    mCurrentLap=1) | latest,
    )


def observe(engine, second, value=None, **kwargs):
    return engine.observe(status(second) if value is None else value, source_id="source-a",
                          now=BASE + second, elapsed=float(second), **kwargs)


def arm(engine, second=1, **overrides):
    return engine.arm(**(dict(cockpit_litres=8, verified_at=BASE + second, now=BASE + second,
                             source_id="source-a", solo_player_confirmed=True,
                             note="synthetic cockpit check")
                         | overrides))


@pytest.fixture
def engine(tmp_path):
    journal = Journal(tmp_path / "events.sqlite3")
    plan = FuelPlan(CONTEXT, 0.5, 6, 2, 2.0, "synthetic conservative estimate")
    value = Supervisor(plan, journal)
    yield value
    journal.close()


@pytest.fixture
def armed(engine):
    assert observe(engine, 0)["supervision_health"] == "degraded"
    assert not arm(engine, 0)
    assert observe(engine, 1)["supervision_health"] == "healthy"
    assert arm(engine)
    return engine


def events(engine):
    return [json.loads(row[0]) for row in
            engine.journal.connection.execute("SELECT payload FROM events ORDER BY sequence")]


def test_fuel_budget_warning_dedup_and_expiry(armed):
    assert armed.plan.required_load == 5
    assert armed.plan.return_budget == 1.5
    assert observe(armed, 2, status(2, litres=2.01))["notice"] is None
    result = observe(armed, 3, status(3, litres=2))
    assert result["fuel_state"] == "return_due"
    notice = result["notice"]
    assert notice["kind"] == "fuel_return"
    assert armed.notice_valid(notice, now=BASE + 3.5)
    assert not armed.notice_valid(notice, now=BASE + 6)
    assert observe(armed, 4, status(4, litres=1.9))["notice"] is None
    assert len([e for e in events(armed) if e["event"] == "notice"]) == 1
    assert events(armed)[-2]["event"] == "notice"


@pytest.mark.parametrize("changes,reason", [
    ({"coverage": "disk_reserve"}, "coverage_disk_reserve"),
    ({"coverage": "mapping_unavailable"}, "coverage_mapping_unavailable"),
    ({"latest_is_current": False}, "latest_not_current"),
    ({"status": "failed"}, "capture_failed"),
    ({"status": "complete"}, "capture_stopped"),
    ({"schema_version": 2}, "unsupported_schema_version"),
    ({"samples": True}, "invalid_sample_count"),
    ({"offset_s": float("nan")}, "invalid_offset"),
])
def test_coverage_or_status_failure_cancels_approval(armed, changes, reason):
    result = observe(armed, 2, status(2) | changes)
    assert result["fuel_state"] == "unarmed"
    assert result["reason"] == reason
    assert result["notice"] is None
    assert any(e["event"] == "fuel_disarmed" for e in events(armed))


@pytest.mark.parametrize("fields,reason", [
    ({"mCarName": "other"}, "context_changed"),
    ({"mSessionState": 2}, "context_changed"),
    ({"mGameState": 4}, "cockpit_inactive"),
    ({"mFuelCapacity": 35}, "capacity_changed"),
    ({"mCurrentLap": 0}, "lap_counter_changed"),
    ({"mCurrentLap": 3}, "lap_counter_changed"),
    ({"mSequenceNumber": 2}, "sequence_not_advancing"),
    ({"mSequenceNumber": 5}, "unstable_sequence"),
    ({"mFuelLevel": 0.9}, "refuel_detected"),
    ({"mFuelLevel": True}, "invalid_number"),
    ({"mFuelLevel": float("nan")}, "invalid_number"),
    ({"mFuelCapacity": 0}, "invalid_number"),
    ({"mVersion": 13}, "unsupported_layout"),
    ({"mRaceState": 4}, "race_not_active"),
    ({"mRaceState": None}, "invalid_integer"),
    ({"mRaceState": True}, "invalid_integer"),
])
def test_context_and_fuel_uncertainty_cancels_approval(armed, fields, reason):
    result = observe(armed, 2, status(2, **fields))
    assert result["reason"] == reason
    assert result["fuel_state"] == "unarmed" and result["notice"] is None


def test_small_refuel_steps_accumulate_against_minimum(armed):
    assert observe(armed, 2, status(2, litres=8.04))["fuel_state"] == "armed"
    assert observe(armed, 3, status(3, litres=8.08))["reason"] == "refuel_detected"


def test_stale_duplicate_does_not_refresh_fuel_or_expiry(armed):
    stale = status(1)
    result = observe(armed, 2, stale)
    assert result["supervision_health"] == "healthy"
    assert result["expires_at"] == BASE + 4
    assert observe(armed, 3, stale)["fuel_state"] == "armed"
    assert observe(armed, 4, stale)["reason"] == "stale_heartbeat"
    assert armed.arm_id is None


def test_counter_change_cannot_relabel_same_sequence_fresh(armed):
    result = observe(armed, 2, status(2, mSequenceNumber=4))
    assert result["reason"] == "sequence_not_advancing"
    assert result["fuel_state"] == "unarmed"


def test_future_source_time_and_clock_jumps_disarm(armed):
    assert observe(armed, 2, status(5))["reason"] == "future_heartbeat"
    observe(armed, 3)
    observe(armed, 4)
    assert arm(armed, 4)
    result = armed.observe(status(5), source_id="source-a", now=BASE + 5, elapsed=4.1)
    assert result["reason"] == "observation_clock_changed"


def test_source_change_gap_and_recovery_require_new_approval(armed):
    result = armed.observe(status(2), source_id="source-b", now=BASE + 2, elapsed=2)
    assert result["reason"] == "source_changed"
    result = armed.observe(status(3), source_id="source-b", now=BASE + 3, elapsed=3)
    assert result["supervision_health"] == "healthy" and result["fuel_state"] == "unarmed"
    assert arm(armed, 3, source_id="source-b")
    result = armed.observe(status(7), source_id="source-b", now=BASE + 7, elapsed=7)
    assert result["reason"] == "observation_gap"


@pytest.mark.parametrize("fields,reason", [
    ({"cockpit_litres": 7}, "cockpit_fuel_mismatch"),
    ({"cockpit_litres": float("inf")}, "invalid_number"),
    ({"solo_player_confirmed": False}, "player_context_not_confirmed"),
    ({"verified_at": BASE - 30}, "approval_not_fresh"),
    ({"verified_at": BASE + 2}, "approval_not_fresh"),
    ({"note": ""}, "missing_approval_note"),
])
def test_arming_requires_actual_fresh_context_and_fuel(engine, fields, reason):
    observe(engine, 0)
    observe(engine, 1)
    assert not arm(engine, **fields)
    assert events(engine)[-1]["reason"] == reason


def test_no_automatic_rearm_after_refuel(armed):
    observe(armed, 2, status(2, litres=9))
    result = observe(armed, 3, status(3, litres=8.99))
    assert result["supervision_health"] == "healthy"
    assert result["fuel_state"] == "unarmed"
    assert not arm(armed, 3)  # Old loaded amount cannot approve a new tank.


def test_approval_cannot_cross_source_or_reset_epoch(armed):
    observe(armed, 2, status(2, litres=9))
    observe(armed, 3, status(3, litres=8.99))
    assert not arm(armed, 3, cockpit_litres=9, verified_at=BASE + 1)
    assert armed.approval_result["reason"] == "approval_predates_invalidation"
    assert not arm(armed, 3, cockpit_litres=9, source_id="source-b")
    assert armed.approval_result["reason"] == "approval_source_changed"
    assert arm(armed, 3, cockpit_litres=9)


def test_pit_state_suppresses_return_call_and_invalidates_queued_one(armed):
    assert observe(armed, 2, status(2, litres=1.9, mPitMode=2))["notice"] is None
    notice = observe(armed, 3, status(3, litres=1.8))["notice"]
    assert notice is not None
    observe(armed, 4, status(4, litres=1.8, mPitMode=2))
    assert not armed.notice_valid(notice, now=BASE + 4)


def test_no_notice_when_journal_commit_fails(armed, monkeypatch):
    original = armed.journal.append

    def fail_notice(event):
        if event["event"] == "notice":
            raise OSError("injected full disk")
        original(event)

    monkeypatch.setattr(armed.journal, "append", fail_notice)
    with pytest.raises(OSError):
        observe(armed, 2, status(2, litres=1.9))
    assert armed.return_notice is None


def test_journal_excludes_raw_and_participant_data_and_refuses_overwrite(engine, tmp_path):
    value = status(0)
    value["raw_base64"] = "private-raw-marker"
    value["latest"]["mName"] = "private-name-marker"
    observe(engine, 0, value)
    assert "private-" not in json.dumps(events(engine))
    with pytest.raises(FileExistsError):
        Journal(tmp_path / "events.sqlite3")
    assert engine.journal.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restart_is_unarmed_and_does_not_replay_previous_notice(armed, tmp_path):
    observe(armed, 2, status(2, litres=1.9))
    fresh = Journal(tmp_path / "restart.sqlite3")
    other = Supervisor(armed.plan, fresh)
    try:
        observe(other, 3, status(3, litres=1.9))
        result = observe(other, 4, status(4, litres=1.8))
        assert result["fuel_state"] == "unarmed" and result["notice"] is None
    finally:
        fresh.close()


@pytest.mark.parametrize("change", [
    {"litres_per_lap": float("nan")}, {"timed_laps": True}, {"reserve_laps": 1},
    {"return_threshold_litres": 1}, {"estimate_source": ""},
])
def test_invalid_plan_is_rejected(engine, change):
    with pytest.raises(ValueError):
        replace(engine.plan, **change)


def test_two_cooperating_watchers_cannot_share_source_lock(tmp_path):
    first = SourceLock(tmp_path / "lock")
    try:
        with pytest.raises(RuntimeError, match="already_observed"):
            SourceLock(tmp_path / "lock")
    finally:
        first.close()
    SourceLock(tmp_path / "lock").close()


def test_replay_keeps_historical_clock_and_labels_test_approval(engine, tmp_path):
    path = tmp_path / "replay.jsonl"
    rows = [dict(at=status(n)["updated_at"], source_id="source-a", status=status(n))
            for n in range(3)]
    rows[1]["approval"] = dict(cockpit_litres=8, note="synthetic")
    rows[2]["status"] = status(2, litres=1.9)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    result = replay(path, engine)
    assert result["audio"] == "disabled" and result["final"]["notice"]
    approval = next(e for e in events(engine) if e["event"] == "fuel_armed")
    assert approval["note"].startswith("REPLAY TEST ONLY")
    assert approval["at"] == BASE + 1


def test_replay_final_approval_is_visible_immediately(engine, tmp_path):
    path = tmp_path / "final-approval.jsonl"
    rows = [dict(at=status(n)["updated_at"], source_id="source-a", status=status(n))
            for n in range(2)]
    rows[-1]["approval"] = dict(cockpit_litres=8, note="synthetic final-row approval")
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    result = replay(path, engine)
    assert result["final"]["fuel_state"] == "armed"
    assert result["final"]["approval"]["accepted"] is True


def test_cli_retains_failed_run_and_returns_nonzero(tmp_path):
    from dataclasses import asdict
    plan = FuelPlan(CONTEXT, 0.5, 6, 2, 2, "synthetic")
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(dict(schema_version=1, **asdict(plan))), encoding="utf-8")
    bad = tmp_path / "broken.jsonl"
    bad.write_text('{"at":', encoding="utf-8")
    out = tmp_path / "runs"
    assert main(["--replay", str(bad), "--plan", str(plan_file), "--out", str(out)]) == 1
    run = next(out.iterdir())
    assert json.loads((run / "status.json").read_text())["status"] == "failed"
    assert "JSONDecodeError" in (run / "failure.txt").read_text()
    with sqlite3.connect(run / "events.sqlite3") as connection:
        records = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM events")]
    assert records[-1]["outcome"] == "failed"


@pytest.mark.parametrize("value", ['{"a":1,"a":2}', '{"n":NaN}', '{"n":Infinity}'])
def test_ambiguous_or_nonstandard_json_refused(value):
    with pytest.raises(ValueError):
        strict_json(value)
