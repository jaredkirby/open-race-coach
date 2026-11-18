"""Viewed-slot fuel approval and explicit load components; synthetic evidence."""
import json
from dataclasses import asdict, replace

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli.telemetry_watch import snapshot_summary
from simcoach.live.fuel_radio import FuelPhrase, FuelRadio
from simcoach.live.supervisor import Context, FuelPlan, Journal, Supervisor
from tests.test_fuel_radio import BASE, CONTEXT, Clock, FakeTransport, make_phrase, status


def view(index=0, count=3, **fields):
    return dict(schema_version=1, index=index, count=count, active=True,
                car=CONTEXT.car, race_state=2, pit_mode=0) | fields


def sample(second, litres=4, mode=5, participant=None, **fields):
    return status(second, litres, mSessionState=mode,
                  participant_observation=participant or view(), **fields)


def make_engine(tmp_path, mode=5):
    journal = Journal(tmp_path / "supervisor.sqlite3")
    plan = FuelPlan(replace(CONTEXT, session_state=mode), .5, 5, 2, 1.5,
                    "synthetic conservative rate", out_laps=0, in_laps=1)
    return Supervisor(plan, journal)


def observe(engine, second, **kwargs):
    return engine.observe(sample(second, mode=engine.plan.context.session_state, **kwargs),
                          source_id="source-a", now=BASE + second, elapsed=second)


def approve(engine, **kwargs):
    return engine.arm(**(dict(cockpit_litres=4, verified_at=BASE + 1, now=BASE + 1,
                             source_id="source-a", note="Synthetic player-view check",
                             player_view_confirmed=True, participant_index=0) | kwargs))


def test_load_components_remove_assumed_outlap_but_preserve_return_reserve():
    plan = FuelPlan(Context("car", "track", "short", 5), .5, 5, 2, 1.5,
                    "synthetic", out_laps=0, in_laps=1)
    assert plan.required_load == 4 and plan.return_budget == 1.5
    assert replace(plan, out_laps=1).required_load == 4.5
    assert FuelPlan.from_dict(dict(schema_version=1, **asdict(plan))) == plan
    legacy = asdict(plan)
    del legacy["out_laps"], legacy["in_laps"]
    assert FuelPlan.from_dict(dict(schema_version=1, **legacy)).required_load == 4.5
    for changes in ({"out_laps": True}, {"in_laps": -1}, {"reserve_laps": 0},
                    {"return_threshold_litres": 1}):
        with pytest.raises(ValueError):
            replace(plan, **changes)


@pytest.mark.parametrize("mode,count", [(5, 3), (6, 2)])
def test_active_race_or_time_trial_requires_explicit_view_and_accepts_four_litres(
        tmp_path, mode, count):
    engine = make_engine(tmp_path, mode)
    try:
        observe(engine, 0, participant=view(count=count))
        observe(engine, 1, participant=view(count=count))
        assert not approve(engine, player_view_confirmed=False, participant_index=None,
                           solo_player_confirmed=True)
        assert engine.approval_result["reason"] == "explicit_player_view_required"
        assert approve(engine)
        result = observe(engine, 2, litres=1.4, participant=view(count=count))
        assert result["notice"]["kind"] == "fuel_return"
        assert result["participant"]["index"] == 0
        assert result["required_load_litres"] == 4
    finally:
        engine.journal.close()


@pytest.mark.parametrize("mutation", ["index", "count", "missing", "inactive", "car",
                                      "race", "pit", "partial", "version"])
def test_changed_view_disarms_without_automatic_reapproval(tmp_path, mutation):
    engine = make_engine(tmp_path)
    try:
        observe(engine, 0)
        observe(engine, 1)
        assert approve(engine)
        value = sample(2)
        evidence = value["latest"]["participant_observation"]
        if mutation == "missing":
            del value["latest"]["participant_observation"]
        elif mutation == "partial":
            del evidence["count"]
        else:
            key, changed = {"index": ("index", 1), "count": ("count", 4),
                            "inactive": ("active", False), "car": ("car", "another car"),
                            "race": ("race_state", 3), "pit": ("pit_mode", 1),
                            "version": ("schema_version", 2)}[mutation]
            evidence[key] = changed
        result = engine.observe(value, source_id="source-a", now=BASE+2, elapsed=2)
        assert result["fuel_state"] == "unarmed" and result["supervision_health"] == "degraded"
        observe(engine, 3)
        observe(engine, 4)
        assert engine.arm_id is None
    finally:
        engine.journal.close()


def test_legacy_status_cannot_confirm_view_or_arm_nonpractice(tmp_path):
    engine = make_engine(tmp_path)
    try:
        for second in (0, 1):
            engine.observe(status(second, 4, mSessionState=5), source_id="source-a",
                           now=BASE+second, elapsed=second)
        assert not approve(engine)
        assert engine.approval_result["reason"] == "confirmed_view_not_observed"
        assert not approve(engine, player_view_confirmed=False, participant_index=None,
                           solo_player_confirmed=True)
    finally:
        engine.journal.close()


def test_stationary_prestart_approval_survives_launch_but_not_observed_restart(tmp_path):
    engine = make_engine(tmp_path)
    grid = dict(participant=view(race_state=1), mRaceState=1, mGameState=4, mSpeed=0)
    try:
        observe(engine, 0, **grid)
        assert observe(engine, 1, **grid)["phase"] == "prestart"
        assert approve(engine)
        arm = engine.arm_id
        # Some fuel may be burned after grid confirmation; don't require the full
        # starting budget again when the race starts.
        assert observe(engine, 2, litres=3.99)["fuel_state"] == "armed"
        assert engine.arm_id == arm
        restarted = observe(engine, 3, litres=3.99, **grid)
        assert restarted["reason"] == "race_reset" and engine.arm_id is None
        assert observe(engine, 4, litres=3.99, **grid)["phase"] == "prestart"
        assert engine.arm_id is None
    finally:
        engine.journal.close()


@pytest.mark.parametrize("game,speed,pit", [(3, 0, 0), (4, 2, 0), (4, 0, 4)])
def test_prestart_does_not_admit_paused_moving_or_pit_context(tmp_path, game, speed, pit):
    engine = make_engine(tmp_path)
    grid = dict(participant=view(race_state=1, pit_mode=pit), mRaceState=1,
                mGameState=game, mSpeed=speed, mPitMode=pit)
    try:
        observe(engine, 0, **grid)
        assert observe(engine, 1, **grid)["supervision_health"] == "degraded"
        assert not approve(engine)
    finally:
        engine.journal.close()


def test_low_fuel_during_prestart_stays_silent_until_racing(tmp_path):
    engine = make_engine(tmp_path)
    grid = dict(participant=view(race_state=1), mRaceState=1, mGameState=2, mSpeed=0)
    try:
        observe(engine, 0, **grid)
        observe(engine, 1, **grid)
        assert approve(engine)
        assert observe(engine, 2, litres=1.4, **grid)["notice"] is None
        assert observe(engine, 3, litres=1.4)["notice"]["kind"] == "fuel_return"
    finally:
        engine.journal.close()


def test_fresh_radio_check_cancels_slot_change_even_before_supervisor_polls(tmp_path):
    engine = make_engine(tmp_path)
    radio = None
    try:
        observe(engine, 0)
        observe(engine, 1)
        assert approve(engine)
        observe(engine, 2, litres=1.4)
        clock = Clock()
        transport = FakeTransport(clock)
        latest = sample(2, litres=1.4, participant=view(index=1))
        radio = FuelRadio(engine, tmp_path / "radio.jsonl",
                          FuelPhrase.load(make_phrase(tmp_path / "phrase")),
                          lambda: ("source-a", latest), transport, clock=clock)
        assert radio.tick()["delivery_state"] == "suppressed"
        assert not transport.started
        assert engine.arm_id  # Fresh guard does not mutate the rule engine.
    finally:
        if radio:
            radio.close()
        engine.journal.close()


def test_summary_preserves_checked_slot_fields_without_names_or_other_participants():
    page = SharedMemory()
    page.mViewedParticipantIndex = 1
    page.mNumParticipants = 3
    page.mParticipantInfo[1].mIsActive = True
    page.mParticipantInfo[1].mName = b"PRIVATE DRIVER NAME"
    page.mCarNames[1].value = b"Example car"
    page.mRaceStates[1] = 2
    page.mPitModes[1] = 0
    value = snapshot_summary(page)
    assert value["participant_observation"] == dict(
        schema_version=1, index=1, count=3, active=True,
        car="Example car", race_state=2, pit_mode=0)
    assert "PRIVATE" not in json.dumps(value) and "mParticipantInfo" not in value
    page.mViewedParticipantIndex = -1
    assert snapshot_summary(page)["participant_observation"]["active"] is None


def test_visit_cli_preserves_explicit_view_confirmation(tmp_path, monkeypatch):
    from simcoach.cli import visit

    sent = []
    monkeypatch.setattr(visit, "read_json", lambda _: {"archive": str(tmp_path)})
    monkeypatch.setattr(visit, "source_identity", lambda _: "source-a")
    monkeypatch.setattr(visit, "request", lambda *args, **kwargs: sent.append(kwargs))
    assert visit.main(["fuel", str(tmp_path), "--cockpit-litres", "4", "--verified-at",
                       "2026-11-16T20:00:00+00:00", "--confirm-player-view",
                       "--participant-index", "2", "--note", "synthetic only"]) == 0
    assert sent[0]["solo_player_confirmed"] is False
    assert sent[0]["player_view_confirmed"] is True and sent[0]["participant_index"] == 2
