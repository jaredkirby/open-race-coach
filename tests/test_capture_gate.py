from __future__ import annotations

from dataclasses import asdict

import pytest

from simcoach.live.capture_gate import CaptureGate
from tests.test_race_watch import BASE, CONTEXT, status


def observe(gate, second, value=None, *, source="archive-a", now=None, elapsed=None):
    return gate.observe(status(second) if value is None else value, source_id=source,
                        now=BASE + second if now is None else now,
                        elapsed=second if elapsed is None else elapsed)


def admitted():
    gate = CaptureGate(CONTEXT)
    assert not observe(gate, 0)["ready"]
    assert observe(gate, 1)["ready"]
    return gate


def test_two_advancing_samples_admit_without_fuel_or_motion():
    gate = CaptureGate(CONTEXT)
    for second in (0, 1):
        value = status(second, mSpeed=0)
        for name in ("mFuelLevel", "mFuelCapacity", "mFuelPressureKPa"):
            value["latest"].pop(name, None)
        result = observe(gate, second, value)
        assert result["ready"] == bool(second)
    assert result == dict(health="healthy", reason=None, ready=True,
                          source_id="archive-a", expires_at=BASE + 4,
                          observed_context=asdict(CONTEXT), expected_context_match=True)


def test_identical_polls_never_extend_original_expiry():
    gate = admitted()
    for offset in (1.5, 2.5, 3.9):
        result = observe(gate, offset, status(1))
        assert result["ready"] and result["expires_at"] == BASE + 4
    assert not observe(gate, 4, status(1))["ready"]
    assert not observe(gate, 4.1, status(4))["ready"]
    assert observe(gate, 5)["ready"]


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"samples": True}, {"offset_s": float("nan")},
    {"offset_s": float("inf")}, {"offset_s": -1}, {"target_hz": True},
    {"updated_at": "bad"}, {"latest_is_current": False}, {"coverage": "stale_mapping"},
    {"status": "failed"}, {"error": "failure"}, {"latest": None},
])
def test_malformed_or_unavailable_status_erases_baseline(change):
    gate = admitted()
    assert not observe(gate, 2, status(2) | change)["ready"]
    assert not observe(gate, 3)["ready"]
    assert observe(gate, 4)["ready"]


@pytest.mark.parametrize("change", [
    {"mVersion": True}, {"mVersion": 15}, {"mSequenceNumber": True},
    {"mSequenceNumber": 7}, {"mSequenceNumber": float("nan")},
    {"mGameState": True}, {"mGameState": 0}, {"mGameState": 5}, {"mGameState": 8},
    {"mCarName": None}, {"mCarName": ""}, {"mTrackLocation": ""},
    {"mTrackVariation": None},
    {"mSessionState": True}, {"mSessionState": 0}, {"mSessionState": 7},
])
def test_invalid_or_unexpected_latest_erases_baseline(change):
    gate = admitted()
    assert not observe(gate, 2, status(2, **change))["ready"]
    assert not observe(gate, 3)["ready"]
    assert observe(gate, 4)["ready"]


def test_source_change_needs_a_pair_from_new_source():
    gate = admitted()
    assert observe(gate, 2, source="archive-b")["reason"] == "source_changed"
    assert not observe(gate, 2.2, status(2), source="archive-b")["ready"]
    assert observe(gate, 3, source="archive-b")["ready"]


@pytest.mark.parametrize("field,value", [("samples", 1), ("offset_s", 0.0)])
def test_counter_reset_requires_new_advancing_pair(field, value):
    gate = admitted()
    assert not observe(gate, 2, status(2) | {field: value})["ready"]
    result = observe(gate, 3)
    if field == "offset_s":
        # The old fixture restores the offset immediately: this is another
        # discontinuity, not a coherent pair with the reset observation.
        assert result["reason"] == "source_clock_changed" and not result["ready"]
        assert observe(gate, 4)["ready"]
    else:
        assert result["ready"]


def test_sequence_reset_requires_new_advancing_pair():
    gate = admitted()
    assert observe(gate, 2, status(2, mSequenceNumber=2))["reason"] == "sequence_not_advancing"
    assert observe(gate, 3)["ready"]


def test_observer_gap_and_backward_clock_require_new_pair():
    gate = admitted()
    assert observe(gate, 5)["reason"] == "observation_gap"
    assert observe(gate, 6)["ready"]
    assert observe(gate, 5)["reason"] == "observation_clock_regressed"
    assert observe(gate, 6)["ready"]


def test_wall_clock_jump_requires_new_pair():
    gate = admitted()
    assert observe(gate, 2, elapsed=1.1)["reason"] == "observation_clock_changed"
    assert observe(gate, 3, elapsed=2.1)["ready"]


def test_future_source_rejected_then_two_fresh_samples_required():
    gate = admitted()
    assert observe(gate, 2, status(3))["reason"] == "future_heartbeat"
    assert not observe(gate, 3)["ready"]
    assert observe(gate, 4)["ready"]


@pytest.mark.parametrize("field", ["now", "elapsed"])
@pytest.mark.parametrize("value", [True, None, "1", -1, float("nan"), float("inf"), 10**1000])
def test_invalid_observer_times_fail_closed(field, value):
    gate = admitted()
    result = gate.observe(status(2), source_id="archive-a",
                          **(dict(now=BASE + 2, elapsed=2) | {field: value}))
    assert result["ready"] is False
    assert not observe(gate, 3)["ready"]
    assert observe(gate, 4)["ready"]


@pytest.mark.parametrize("value", [None, True, "", " ", 1])
def test_missing_source_identity_fails_closed(value):
    gate = admitted()
    assert not observe(gate, 2, source=value)["ready"]


@pytest.mark.parametrize("value", [True, 0, -1, 3.1, float("nan"), float("inf")])
def test_invalid_age_rejected(value):
    with pytest.raises(ValueError):
        CaptureGate(CONTEXT, max_age=value)


def test_unknown_expected_context_rejected():
    with pytest.raises(ValueError):
        CaptureGate(None)


@pytest.mark.parametrize("game_state", [1, 2, 3, 4, 6, 7])
@pytest.mark.parametrize("session_state", [1, 2, 3, 4, 5, 6])
def test_record_before_departure_in_supported_modes(game_state, session_state):
    gate = CaptureGate(CONTEXT)
    for second in (0, 1):
        value = status(second, mGameState=game_state, mSessionState=session_state,
                       mRaceState=1, mSpeed=0)
        result = observe(gate, second, value)
        assert result["ready"] == bool(second)
    assert result["expected_context_match"] == (session_state == CONTEXT.session_state)
    assert result["observed_context"]["session_state"] == session_state


@pytest.mark.parametrize("change", [
    {"mCarName": "another car"}, {"mTrackLocation": "another track"},
    {"mTrackVariation": "long"}, {"mSessionState": 5}, {"mSessionState": 6},
])
def test_context_change_reacquires_without_changing_plan(change):
    gate = admitted()
    result = observe(gate, 2, status(2, **change))
    assert not result["ready"] and result["reason"] == "context_changed"
    assert result["expected_context_match"] is False
    assert not observe(gate, 2.5, status(2, **change))["ready"]
    assert observe(gate, 3, status(3, **change))["ready"]
    assert gate.expected_context == CONTEXT


def test_race_and_fuel_fields_cannot_block_evidence_capture():
    gate = CaptureGate(CONTEXT)
    for second in (0, 1):
        value = status(second)
        for field in ("mRaceState", "mFuelCapacity", "mFuelLevel"):
            value["latest"].pop(field)
        assert observe(gate, second, value)["ready"] == bool(second)


def test_capture_admission_refuses_accumulated_observer_clock_drift():
    gate = admitted()
    assert observe(gate, 2, elapsed=1.8)["ready"]
    assert observe(gate, 3, elapsed=2.6)["ready"]
    result = observe(gate, 4, elapsed=3.4)
    assert result["ready"] is False
    assert result["reason"] == "observation_clock_changed"


def test_capture_admission_refuses_source_offset_jump():
    gate = admitted()
    result = observe(gate, 2, status(2) | {"offset_s": 22.0})
    assert result["ready"] is False
    assert result["reason"] == "source_clock_changed"


def test_capture_admission_refuses_accumulated_source_clock_drift():
    gate = admitted()
    assert observe(gate, 2, status(2) | {"offset_s": 2.2})["ready"]
    assert observe(gate, 3, status(3) | {"offset_s": 3.4})["ready"]
    result = observe(gate, 4, status(4) | {"offset_s": 4.6})
    assert result["ready"] is False
    assert result["reason"] == "source_clock_changed"


def test_capture_reacquires_coherent_changed_source_clock_without_approving_fuel():
    gate = admitted()
    assert not observe(gate, 2, status(2) | {"offset_s": 22.0})["ready"]
    assert not observe(gate, 2.1, status(2) | {"offset_s": 22.0})["ready"]
    result = observe(gate, 3, status(3) | {"offset_s": 23.0})
    assert result["ready"] and "fuel_state" not in result


def test_capture_reacquires_after_observer_clock_fault_without_sliding_anchor():
    gate = admitted()
    assert not observe(gate, 2, elapsed=1.2)["ready"]
    assert not observe(gate, 2.1, status(2), elapsed=1.3)["ready"]
    assert observe(gate, 3, elapsed=2.2)["ready"]
    assert observe(gate, 4, elapsed=3.0)["ready"]
    assert observe(gate, 5, elapsed=3.8)["ready"]
    assert not observe(gate, 6, elapsed=4.6)["ready"]

def test_capture_requires_fresh_pair_after_missed_continuity_change():
    gate = CaptureGate(CONTEXT)
    assert not observe(gate, 0, status(0) | {"continuity_generation": 0})["ready"]
    assert observe(gate, 1, status(1) | {"continuity_generation": 0})["ready"]
    resumed = status(2) | {"continuity_generation": 1}
    assert observe(gate, 2, resumed)["reason"] == "source_continuity_changed"
    assert not observe(gate, 2.1, resumed)["ready"]
    assert observe(gate, 2.5, status(2.5, mSequenceNumber=8) | {
        "continuity_generation": 1, "samples": 150,
    })["ready"]
