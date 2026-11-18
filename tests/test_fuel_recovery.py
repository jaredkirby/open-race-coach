"""Bounded suspension of an existing approval; no simulator or audio access."""
import pytest

from simcoach.live.supervisor import FuelPlan, Journal, Supervisor
from tests.test_race_watch import BASE, CONTEXT, events, status


def sample(second, **fields):
    value = status(second, **fields)
    value["samples"] = round(second * 100) + 1
    value["latest"]["mSequenceNumber"] = 2 * value["samples"]
    return value


def step(engine, second, value=None, *, source="source-a", elapsed=None):
    return engine.observe(sample(second) if value is None else value, source_id=source,
                          now=BASE + second, elapsed=second if elapsed is None else elapsed)


def unstable(second):
    return sample(second) | dict(coverage="unstable_copy", latest_is_current=False)


@pytest.fixture
def engine(tmp_path):
    journal = Journal(tmp_path / "supervision.sqlite3")
    engine = Supervisor(FuelPlan(CONTEXT, .5, 6, 2, 2, "synthetic"), journal)
    step(engine, 0)
    step(engine, .2)
    assert engine.arm(cockpit_litres=8, verified_at=BASE + .2, now=BASE + .2,
                      source_id="source-a", solo_player_confirmed=True, note="synthetic")
    yield engine
    journal.close()


def test_short_copy_failure_recovers_original_approval_after_two_fresh_samples(engine):
    original = engine.arm_id
    lost = step(engine, .4, unstable(.4))
    assert lost["fuel_state"] == "suspended" and lost["supervision_health"] == "degraded"
    assert lost["fuel_litres"] is None and lost["notice"] is None
    assert lost["supervision_loss"]["resolution"] == "recovering"
    assert step(engine, .6)["reason"] == "awaiting_recovery_observation"
    assert step(engine, .7, sample(.6))["fuel_state"] == "suspended"
    recovered = step(engine, .8)
    assert recovered["fuel_state"] == "armed" and recovered["recovery"] is None
    assert recovered["supervision_loss"]["resolution"] == "recovered"
    assert recovered["supervision_loss"]["id"] == lost["supervision_loss"]["id"]
    assert engine.arm_id == original
    assert [e["event"] for e in events(engine)].count("fuel_armed") == 1
    assert [e["event"] for e in events(engine)].count("fuel_recovered") == 1


def test_unstable_again_resets_pair_without_extending_deadline(engine):
    deadline = step(engine, .4, unstable(.4))["recovery"]["expires_at"]
    step(engine, .6)
    assert step(engine, .8, unstable(.8))["recovery"]["expires_at"] == deadline
    assert step(engine, 1)["fuel_state"] == "suspended"
    assert step(engine, 1.2)["fuel_state"] == "armed"


def test_persistent_instability_expires_and_never_auto_rearms(engine):
    for second in (.4, 1, 2, 3):
        assert step(engine, second, unstable(second))["fuel_state"] == "suspended"
    expired = step(engine, 3.4)
    assert expired["reason"] == "recovery_expired" and expired["fuel_state"] == "unarmed"
    assert expired["supervision_loss"]["resolution"] == "requires_approval"
    step(engine, 3.6)
    assert step(engine, 3.8)["fuel_state"] == "unarmed"


def test_original_source_age_also_bounds_recovery(engine):
    # Identical polls do not refresh the last source timestamp.
    for second in (1, 2, 3):
        step(engine, second, sample(.2))
    suspended = step(engine, 3.1, unstable(3.1))
    assert suspended["recovery"]["expires_at"] == BASE + 5.2
    step(engine, 4, unstable(4))
    step(engine, 5, unstable(5))
    assert step(engine, 5.2)["reason"] == "recovery_expired"


@pytest.mark.parametrize("field,value,reason", [
    ("mCarName", "different", "context_changed"),
    ("mSessionState", 5, "context_changed"),
    ("mFuelCapacity", 40, "capacity_changed"),
    ("mFuelLevel", .9, "refuel_detected"),
    ("mCurrentLap", 0, "lap_counter_changed"),
    ("mGameState", 3, "cockpit_inactive"),
    ("mRaceState", 3, "race_not_active"),
    ("mVersion", 15, "unsupported_layout"),
])
def test_uncertainty_during_recovery_requires_fresh_approval(engine, field, value, reason):
    step(engine, .4, unstable(.4))
    result = step(engine, .6, sample(.6, **{field: value}))
    assert result["reason"] == reason and result["fuel_state"] == "unarmed"
    step(engine, .8)
    assert step(engine, 1)["fuel_state"] == "unarmed"


@pytest.mark.parametrize("change,reason", [
    ({"coverage": "stale_mapping"}, "coverage_stale_mapping"),
    ({"coverage": "disk_reserve"}, "coverage_disk_reserve"),
    ({"status": "failed"}, "capture_failed"),
    ({"updated_at": "bad"}, "invalid_updated_at"),
])
def test_other_source_failures_cannot_use_copy_recovery(engine, change, reason):
    step(engine, .4, unstable(.4))
    result = step(engine, .6, sample(.6) | change)
    assert result["reason"] == reason and result["fuel_state"] == "unarmed"


def test_source_change_even_during_unreadable_page_disarms(engine):
    step(engine, .4, unstable(.4))
    result = step(engine, .6, unstable(.6), source="source-b")
    assert result["reason"] == "source_changed" and result["fuel_state"] == "unarmed"


def test_clock_jump_cannot_be_hidden_by_unstable_copy(engine):
    step(engine, .4, unstable(.4))
    result = step(engine, 1.5, unstable(1.5), elapsed=.6)
    assert result["reason"] == "observation_clock_changed"
    assert result["fuel_state"] == "unarmed"


def test_small_observer_clock_changes_cannot_accumulate_under_approval(engine):
    # Each wall/monotonic delta differs by only .3s; total divergence exceeds .5s.
    assert step(engine, .6, elapsed=.3)["fuel_state"] == "armed"
    result = step(engine, 1.0, elapsed=.4)
    assert result["reason"] == "observation_clock_changed"
    assert result["fuel_state"] == "unarmed"
    assert engine.supervision_loss["resolution"] == "requires_approval"


@pytest.mark.parametrize("suspended", [False, True])
def test_source_clock_jump_revokes_approval_even_with_fresh_utc(engine, suspended):
    if suspended:
        step(engine, .3, unstable(.3))
    result = step(engine, .4, sample(.4) | {"offset_s": 20.4})
    assert result["reason"] == "source_clock_changed"
    assert result["fuel_state"] == "unarmed" and result["notice"] is None
    # Two coherent source readings cannot silently restore the old approval.
    step(engine, .6, sample(.6) | {"offset_s": 20.6})
    assert step(engine, .8, sample(.8) | {"offset_s": 20.8})["fuel_state"] == "unarmed"


def test_small_source_clock_changes_cannot_accumulate_under_approval(engine):
    assert step(engine, .4, sample(.4) | {"offset_s": .7})["fuel_state"] == "armed"
    result = step(engine, .6, sample(.6) | {"offset_s": 1.2})
    assert result["reason"] == "source_clock_changed"
    assert result["fuel_state"] == "unarmed"


def test_return_notice_is_never_resurrected_by_copy_recovery(engine):
    notice = step(engine, .4, sample(.4, litres=1.9))["notice"]
    assert notice
    assert step(engine, .6, unstable(.6))["fuel_state"] == "unarmed"
    assert not engine.notice_valid(notice, now=BASE + .6)
    step(engine, .8, sample(.8, litres=1.8))
    assert step(engine, 1, sample(1, litres=1.8))["notice"] is None


def test_crossing_threshold_during_suspension_waits_for_verified_pair(engine):
    step(engine, .4, unstable(.4))
    assert step(engine, .6, sample(.6, litres=1.9))["notice"] is None
    result = step(engine, .8, sample(.8, litres=1.8))
    assert result["fuel_state"] == "return_due" and result["notice"]
    assert engine.notice_valid(result["notice"], now=BASE + .8)


def test_recovery_journal_failure_cannot_expose_healthy_state(engine, monkeypatch):
    step(engine, .4, unstable(.4))
    step(engine, .6)
    append = engine.journal.append

    def fail(event):
        if event["event"] == "fuel_recovered":
            raise OSError("synthetic storage failure")
        append(event)

    monkeypatch.setattr(engine.journal, "append", fail)
    with pytest.raises(OSError):
        step(engine, .8)
    assert engine.fuel_state == "suspended" and engine.current is None


def test_small_refuel_increments_during_recovery_are_not_hidden(engine):
    step(engine, .4, unstable(.4))
    assert step(engine, .6, sample(.6, litres=8.04))["fuel_state"] == "suspended"
    assert step(engine, .8, sample(.8, litres=8.08))["reason"] == "refuel_detected"
    assert engine.arm_id is None


def test_new_explicit_approval_resolves_historical_loss(engine):
    step(engine, .4, sample(.4, litres=9))
    step(engine, .6, sample(.6, litres=9))
    assert engine.arm(cockpit_litres=9, verified_at=BASE + .6, now=BASE + .6,
                      source_id="source-a", solo_player_confirmed=True, note="new test check")
    assert engine.supervision_loss["resolution"] == "reapproved"
    assert engine.supervision_loss["replacement_arm_id"] == engine.arm_id


def test_radio_reports_suspension_without_playing_or_falsely_showing_armed(engine, tmp_path):
    from simcoach.live.fuel_radio import FuelPhrase, FuelRadio
    from tests.test_fuel_radio import FakeTransport, make_phrase

    now = [BASE + .4]

    def clock():
        return now[0]
    transport = FakeTransport(clock)
    radio = FuelRadio(engine, tmp_path / "radio.jsonl",
                      FuelPhrase.load(make_phrase(tmp_path / "phrase")),
                      lambda: ("source-a", sample(now[0] - BASE)), transport, clock=clock)
    try:
        step(engine, .4, unstable(.4))
        state = radio.tick()
        assert state["fuel_state"] == "suspended" and state["reason"] == "coverage_unstable_copy"
        assert state["supervision_loss"]["resolution"] == "recovering"
        assert not transport.started
        step(engine, .6)
        step(engine, .8)
        now[0] = BASE + .8
        assert radio.tick()["fuel_state"] == "armed"
        assert not transport.started
    finally:
        radio.close()


def test_sequence_regression_during_recovery_disarms(engine):
    step(engine, .4, unstable(.4))
    value = sample(.6)
    value["latest"]["mSequenceNumber"] = 2
    assert step(engine, .6, value)["reason"] == "sequence_not_advancing"
    assert engine.arm_id is None


def test_restart_during_suspension_cannot_restore_approval(engine, tmp_path):
    step(engine, .4, unstable(.4))
    fresh = Journal(tmp_path / "restart.sqlite3")
    try:
        restarted = Supervisor(engine.plan, fresh)
        step(restarted, .6)
        assert step(restarted, .8)["fuel_state"] == "unarmed"
        assert restarted.suspension is None and restarted.supervision_loss is None
    finally:
        fresh.close()


@pytest.mark.parametrize("failed_event", ["fuel_suspended", "supervision_loss"])
def test_suspension_storage_failure_revokes_approval_and_propagates(engine, monkeypatch,
                                                                  failed_event):
    append = engine.journal.append

    def fail(event):
        if event["event"] == failed_event:
            raise OSError("synthetic full disk")
        append(event)

    monkeypatch.setattr(engine.journal, "append", fail)
    with pytest.raises(OSError):
        step(engine, .4, unstable(.4))
    assert engine.current is None and engine.health == "degraded"
    assert engine.fuel_state == "unarmed" and engine.suspension is None
    assert engine.approval_result["reason"] == "suspension_journal_failed"

def test_missed_mapping_outage_revokes_fuel_on_next_healthy_publication(tmp_path):
    # The consumer saw advancing samples on both sides, but missed the invalidation.
    journal = Journal(tmp_path / "continuity.sqlite3")
    engine = Supervisor(FuelPlan(CONTEXT, .5, 6, 2, 2, "synthetic"), journal)
    try:
        step(engine, 0, sample(0) | {"continuity_generation": 0})
        step(engine, .2, sample(.2) | {"continuity_generation": 0})
        assert engine.arm(cockpit_litres=8, verified_at=BASE + .2, now=BASE + .2,
                          source_id="source-a", solo_player_confirmed=True, note="synthetic")
        result = step(engine, .6, sample(.6) | {"continuity_generation": 1})
        assert result["reason"] == "source_continuity_changed"
        assert result["fuel_state"] == "unarmed" and result["notice"] is None
        recovered = step(engine, .8, sample(.8) | {"continuity_generation": 1})
        assert recovered["supervision_health"] == "healthy" and recovered["fuel_state"] == "unarmed"
        assert not engine.arm(cockpit_litres=8, verified_at=BASE + .2, now=BASE + .8,
                              source_id="source-a", solo_player_confirmed=True, note="old check")
        assert engine.approval_result["reason"] == "approval_predates_invalidation"
        assert engine.arm(cockpit_litres=8, verified_at=BASE + .8, now=BASE + .8,
                          source_id="source-a", solo_player_confirmed=True, note="new check")
    finally:
        journal.close()

def test_sustained_unstable_generation_cannot_use_short_copy_recovery(tmp_path):
    journal = Journal(tmp_path / "continuity.sqlite3")
    engine = Supervisor(FuelPlan(CONTEXT, .5, 6, 2, 2, "synthetic"), journal)
    try:
        step(engine, 0, sample(0) | {"continuity_generation": 0})
        step(engine, .2, sample(.2) | {"continuity_generation": 0})
        assert engine.arm(cockpit_litres=8, verified_at=BASE + .2, now=BASE + .2,
                          source_id="source-a", solo_player_confirmed=True, note="synthetic")
        held = step(engine, .4, unstable(.4) | {"continuity_generation": 0})
        assert held["fuel_state"] == "suspended"
        broken = step(engine, 2.2, unstable(2.2) | {"continuity_generation": 1})
        assert broken["reason"] == "source_continuity_changed"
        assert broken["fuel_state"] == "unarmed"
        assert broken["supervision_loss"]["resolution"] == "requires_approval"
    finally:
        journal.close()
