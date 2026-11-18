from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from simcoach.live.activity_evidence import ActivityEvidence

BASE = 1_800_000_000
SOURCE = "a" * 64


def status(second, **latest):
    return dict(schema_version=1, status="running",
                updated_at=datetime.fromtimestamp(BASE + second, UTC).isoformat(),
                offset_s=second, samples=int(second * 60) + 1,
                coverage="available", latest_is_current=True, target_hz=60, error=None,
                latest=dict(mVersion=14, mSequenceNumber=int(second * 60) * 2 + 2,
                            mCarName="synthetic car", mTrackLocation="synthetic track",
                            mTrackVariation="", mSessionState=1, mGameState=2,
                            mPitMode=0, mSpeed=20) | latest)


def observe(observer, second, value=None, **kwargs):
    return observer.observe(status(second) if value is None else value,
                            **(dict(source_id=SOURCE, now=BASE + second, elapsed=second) | kwargs))


def admitted():
    observer = ActivityEvidence()
    assert observe(observer, 0)["activity"] == "unknown"
    assert observe(observer, 1)["evidence"] == "advancing"
    return observer


def test_heartbeat_cadence_holds_original_expiry_without_new_independent_samples():
    observer = ActivityEvidence()
    for second in (0, .2, .4, .6, .8):
        assert observe(observer, second, status(0))["activity"] == "unknown"
    result = observe(observer, 1)
    assert result["activity"] == "driving" and result["evidence"] == "advancing"
    for second in (1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8):
        held = observe(observer, second, status(1))
        assert held["activity"] == "driving" and held["evidence"] == "held"
        assert held["expires_at"] == BASE + 3 and held["expires_elapsed"] == 3
    assert observe(observer, 3, status(1))["activity"] == "unknown"
    assert observe(observer, 3.2, status(1))["activity"] == "unknown"
    assert observe(observer, 3.4, status(3))["activity"] == "unknown"
    assert observe(observer, 4)["evidence"] == "advancing"


@pytest.mark.parametrize("game", [1, 3, 4, 6, 7])
def test_fresh_menu_pause_replay_do_not_require_capture_or_fuel_approval(game):
    observer = ActivityEvidence()
    latest = dict(mGameState=game, mSessionState=0, mCarName="", mTrackLocation="")
    assert observe(observer, 0, status(0, **latest))["activity"] == "unknown"
    assert observe(observer, 1, status(1, **latest))["activity"] == "not_driving"


@pytest.mark.parametrize("speed,pit,activity", [(0, 0, "not_driving"), (1, 3, "not_driving"),
                                               (2, 3, "pit_moving"), (20, 0, "driving")])
def test_equal_controls_with_advancing_source_are_usable(speed, pit, activity):
    observer = ActivityEvidence()
    observe(observer, 0, status(0, mSpeed=speed, mPitMode=pit))
    assert observe(observer, 1, status(1, mSpeed=speed, mPitMode=pit))["activity"] == activity


@pytest.mark.parametrize("field,value", [
    ("mVersion", True), ("mVersion", 15), ("mSequenceNumber", True),
    ("mSequenceNumber", 5), ("mSequenceNumber", 2**32), ("mSequenceNumber", -2),
    ("mGameState", True), ("mGameState", 0), ("mGameState", 5),
    ("mSessionState", True), ("mSessionState", 7), ("mPitMode", True),
    ("mPitMode", 6), ("mSpeed", True), ("mSpeed", float("nan")),
    ("mSpeed", float("inf")), ("mSpeed", -1), ("mCarName", None),
    ("mCarName", ""), ("mCarName", "bad\0label"),
    ("mTrackLocation", ""), ("mTrackVariation", 1),
])
def test_invalid_source_clears_baseline_and_never_classifies_driving(field, value):
    observer = admitted()
    assert observe(observer, 2, status(2, **{field: value}))["activity"] == "unknown"
    assert observe(observer, 3)["activity"] == "unknown"
    assert observe(observer, 4)["activity"] == "driving"


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"samples": True}, {"offset_s": True},
    {"updated_at": "not a timestamp"}, {"coverage": "stale"},
    {"latest_is_current": False}, {"status": "failed"}, {"latest": None},
])
def test_bad_status_revokes_and_requires_new_pair(change):
    observer = admitted()
    assert observe(observer, 2, status(2) | change)["activity"] == "unknown"
    assert observe(observer, 3)["activity"] == "unknown"
    assert observe(observer, 4)["activity"] == "driving"


@pytest.mark.parametrize("field", [
    "mCarName", "mTrackLocation", "mTrackVariation", "mSessionState",
])
def test_context_change_requires_pair(field):
    observer = admitted()
    changed = {field: 2 if field == "mSessionState" else "different"}
    assert observe(observer, 2, status(2, **changed))["reason"] == "context_changed"
    assert observe(observer, 2.2, status(2, **changed))["evidence"] == "unknown"
    assert observe(observer, 3, status(3, **changed))["activity"] == "driving"


def test_verified_identity_required_and_source_change_cannot_hold_previous_admission():
    observer = admitted()
    assert observe(observer, 2, source_id="b" * 64)["reason"] == "source_changed"
    assert observe(observer, 3, source_id="b" * 64)["activity"] == "driving"
    assert observe(observer, 4, source_id="archive label")["activity"] == "unknown"
    assert observe(observer, 5)["activity"] == "unknown"
    assert observe(observer, 6)["activity"] == "driving"


@pytest.mark.parametrize("change", [{"samples": 61}, {"offset_s": 1},
                                     {"updated_at": status(1)["updated_at"]}])
def test_new_status_requires_all_progress_axes_to_advance(change):
    observer = admitted()
    assert observe(observer, 2, status(2) | change)["activity"] == "unknown"
    recovered = observe(observer, 3)
    if "samples" in change:
        assert recovered["evidence"] == "advancing"
    else:
        assert recovered["activity"] == "unknown"  # inconsistent baseline cannot bridge
    assert observe(observer, 4)["evidence"] == "advancing"


def test_heartbeat_updates_cannot_renew_same_raw_sequence():
    observer = admitted()
    for second in range(2, 6):
        result = observe(observer, second, status(second, mSequenceNumber=122))
        assert result["activity"] == "unknown" and result["reason"] == "sequence_not_advancing"
    assert observe(observer, 6)["activity"] == "driving"


@pytest.mark.parametrize("change", [{"mSpeed": 0}, {"mGameState": 3}, {"mPitMode": 2}])
def test_same_progress_with_changed_payload_is_corrupt_not_held(change):
    observer = admitted()
    assert observe(observer, 1.2, status(1, **change))["reason"] == "same_progress_payload_changed"
    assert observe(observer, 2)["activity"] == "unknown"
    assert observe(observer, 3)["activity"] == "driving"


def test_regression_or_possible_wrap_is_baseline_not_new_activity():
    observer = ActivityEvidence()
    observe(observer, 0, status(0, mSequenceNumber=4294967292))
    assert observe(observer, 1, status(1, mSequenceNumber=4294967294))["activity"] == "driving"
    assert observe(observer, 2, status(2, mSequenceNumber=0))["reason"] == "sequence_not_advancing"
    assert observe(observer, 3, status(3, mSequenceNumber=2))["activity"] == "driving"


@pytest.mark.parametrize("now,elapsed", [(BASE + .5, 2), (BASE + 2, .5),
                                         (BASE + 3, 3), (BASE + 2.6, 2), (1e15, 2)])
def test_host_clock_faults_require_new_pair(now, elapsed):
    observer = admitted()
    assert observe(observer, 2, now=now, elapsed=elapsed)["activity"] == "unknown"


def test_cumulative_host_drift_and_original_monotonic_expiry():
    observer = admitted()
    for elapsed in (1.4, 1.8, 2.2, 2.6):
        result = observe(observer, elapsed, status(1), now=BASE + elapsed - .2)
        assert result["evidence"] == "held"
    assert observe(observer, 3, status(1), now=BASE + 2.8)["reason"] == "source_evidence_expired"
    observer = admitted()
    for second, drift in [(1.2, .2), (1.4, .4)]:
        assert observe(observer, second, status(1), now=BASE + second + drift)["evidence"] == "held"
    result = observe(observer, 1.6, status(1), now=BASE + 2.2)
    assert result["reason"] == "observation_clock_changed"


def test_cumulative_source_utc_offset_drift_cannot_hide_in_small_steps():
    observer = ActivityEvidence()
    for second in (0, 1, 2):
        result = observe(observer, second, status(second) | {"offset_s": second * .8})
    assert result["activity"] == "driving"
    assert observe(observer, 3, status(3) | {"offset_s": 2.4})["reason"] == "source_clock_changed"


def test_source_delta_disagreement_requires_consistent_new_pair():
    observer = admitted()
    assert observe(observer, 2, status(2) | {"offset_s": 2.6})["reason"] == "source_clock_changed"
    assert observe(observer, 3)["activity"] == "unknown"
    assert observe(observer, 4)["activity"] == "driving"


def test_snapshot_is_detached_serializable_and_contains_no_context_payload():
    observer = admitted()
    original = status(2)
    result = observe(observer, 2, original)
    assert "synthetic" not in json.dumps(result)
    original["latest"]["mSpeed"] = 0
    result["activity"] = "modified"
    result["policy"]["max_age_s"] = 999
    held = observe(observer, 2.2, deepcopy(status(2)))
    assert held["activity"] == "driving" and held["policy"]["max_age_s"] == 2

def test_activity_is_unknown_until_post_reconnect_evidence_advances():
    observer = ActivityEvidence()
    observe(observer, 0, status(0) | {"continuity_generation": 0})
    assert observe(observer, 1, status(1) | {"continuity_generation": 0})["activity"] == "driving"
    first = status(1.25) | {"continuity_generation": 1}
    gap = observe(observer, 1.25, first)
    assert gap["reason"] == "source_continuity_changed" and gap["activity"] == "unknown"
    assert observe(observer, 1.5, first)["evidence"] == "unknown"
    after = observe(observer, 1.75, status(1.75) | {"continuity_generation": 1})
    assert after["activity"] == "driving" and after["evidence"] == "advancing"
