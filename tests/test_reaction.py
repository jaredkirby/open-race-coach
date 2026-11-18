import copy
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from simcoach.live.reaction import LapInvalidationDetector, assess_optional_output
from simcoach.live.supervisor import Context

CONTEXT = Context("test_car", "test_track", "test_layout", 1)
SOURCE = "a"*64
BASE = 1700000000


def status(index, flag, *, lap=4):
    return dict(schema_version=1, status="running", updated_at=datetime.fromtimestamp(
        BASE+index*.2, UTC).isoformat(), offset_s=100+index*.2, samples=100+index,
        target_hz=60, coverage="available", latest_is_current=True, error=None,
        latest=dict(mVersion=14, mSequenceNumber=1000+index*2,
                    mCarName=CONTEXT.car, mTrackLocation=CONTEXT.track,
                    mTrackVariation=CONTEXT.variation, mSessionState=1,
                    mGameState=2, mRaceState=2, mPitMode=0,
                    mLapInvalidated=flag, mCurrentLap=lap))


def observe(detector, index, flag, **kwargs):
    return detector.observe(status(index, flag), source_id=SOURCE,
                            now=BASE+index*.2, elapsed=index*.2, **kwargs)


def detector(tmp_path, **kwargs):
    return LapInvalidationDetector(CONTEXT, tmp_path/"reactions.sqlite3", **kwargs)


def decisions(path):
    with sqlite3.connect(path) as connection:
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM decisions ORDER BY sequence")]


def test_false_to_true_proposes_precise_flag_only_and_preserves_evidence(tmp_path):
    subject = detector(tmp_path)
    assert observe(subject, 0, False)["reason"] == "false_baseline"
    decision = observe(subject, 1, True)
    event = decision["proposal"]
    assert event["text"] == "Lap marked invalid."
    assert event["causal_claim"] is None and event["delivery"] == "NOT RUN"
    assert event["before"]["invalidated"] is False and event["edge"]["invalidated"] is True
    assert event["edge"]["raw_lap"] == 4
    assert event["edge"]["view"] is None
    assert event["expires_at"] == BASE+.2+2
    assert event["timestamp_basis"] == "archive_sample"
    assert observe(subject, 2, True)["proposal"] is None
    assert subject.observe(status(2, True), source_id=SOURCE,
                           now=BASE+.5, elapsed=.5)["reason"] == "duplicate_observation"
    subject.close()
    saved = decisions(tmp_path/"reactions.sqlite3")
    assert saved[1]["proposal"] == event
    assert len(saved) == 4


def test_initial_true_and_sparse_missed_transition_never_invent_edge(tmp_path):
    subject = detector(tmp_path, timestamp_basis="collector_heartbeat")
    assert observe(subject, 0, True)["reason"] == "initial_true_without_baseline"
    assert observe(subject, 1, True)["proposal"] is None
    # An unobserved true pulse between these false observations cannot be recovered.
    assert observe(subject, 2, False)["proposal"] is None
    assert observe(subject, 3, False)["proposal"] is None
    subject.close()


@pytest.mark.parametrize("mutation, expected", [
    ("flag", "invalid_lap_flag"), ("lap", "raw_lap_changed"),
    ("source", "source_changed"), ("context", "unexpected_context"),
    ("pit", "inactive_driving_context"), ("menu", "inactive_driving_context"),
    ("race", "inactive_driving_context"), ("version", "unsupported_layout"),
    ("sequence", "source_progress_invalid"), ("odd", "unstable_sequence"),
    ("count", "source_progress_invalid"), ("unavailable", "coverage_mapping_unavailable"),
])
def test_breaks_refuse_edge_and_require_new_false_baseline(tmp_path, mutation, expected):
    subject = detector(tmp_path)
    observe(subject, 0, False)
    changed, source = status(1, True), SOURCE
    if mutation == "flag":
        changed["latest"]["mLapInvalidated"] = 1
    elif mutation == "lap":
        changed["latest"]["mCurrentLap"] = 1
    elif mutation == "source":
        source = "b"*64
    elif mutation == "context":
        changed["latest"]["mCarName"] = "other"
    elif mutation in ("pit", "menu", "race", "version", "sequence", "odd"):
        key, value = {"pit": ("mPitMode", 1), "menu": ("mGameState", 3),
                      "race": ("mRaceState", 4), "version": ("mVersion", 13),
                      "sequence": ("mSequenceNumber", 1000),
                      "odd": ("mSequenceNumber", 1001)}[mutation]
        changed["latest"][key] = value
    elif mutation == "count":
        changed["samples"] = 100
    else:
        changed["coverage"] = "mapping_unavailable"
    result = subject.observe(changed, source_id=source, now=BASE+.2, elapsed=.2)
    assert result["proposal"] is None and result["reason"] == expected
    assert observe(subject, 2, True)["proposal"] is None
    assert observe(subject, 3, False)["proposal"] is None
    assert observe(subject, 4, True)["proposal"] is not None
    subject.close()


@pytest.mark.parametrize("kind", ["stale", "future", "gap", "wall_reset", "elapsed_reset"])
def test_clock_and_gap_rejection(tmp_path, kind):
    subject = detector(tmp_path)
    observe(subject, 0, False)
    sample, now, elapsed = status(1, True), BASE+.2, .2
    if kind == "stale":
        now, elapsed = BASE+3, 3
    elif kind == "future":
        now, elapsed = BASE+.1, .1
    elif kind == "gap":
        sample, now, elapsed = status(11, True), BASE+2.2, 2.2
    elif kind == "wall_reset":
        sample = status(0, True)
        now = BASE-.1
    else:
        observe(subject, 1, False)
        sample, now, elapsed = status(2, True), BASE+.4, .1
    assert subject.observe(sample, source_id=SOURCE, now=now, elapsed=elapsed)["proposal"] is None
    subject.close()


def viewed(index, flag, *, slot=0):
    value = status(index, flag)
    value["latest"]["participant_observation"] = dict(
        schema_version=1, index=slot, count=2, active=True, car=CONTEXT.car,
        race_state=2, pit_mode=0)
    return value


@pytest.mark.parametrize("change, expected", [
    ("slot", "viewed_slot_changed"),
    ("inactive", "participant_evidence_invalid"),
    ("missing", "participant_evidence_unavailable"),
    ("source_clock", "source_clock_changed"),
])
def test_shadow_reaction_requires_same_view_and_coherent_source_clock(tmp_path, change, expected):
    subject = detector(tmp_path, mode="shadow")
    try:
        baseline = subject.observe(viewed(0, False), source_id=SOURCE, now=BASE, elapsed=0)
        assert baseline["reason"] == "false_baseline"
        edge = viewed(1, True, slot=1 if change == "slot" else 0)
        if change == "inactive":
            edge["latest"]["participant_observation"]["active"] = False
        elif change == "missing":
            edge["latest"].pop("participant_observation")
        elif change == "source_clock":
            edge["offset_s"] += 1
        refused = subject.observe(edge, source_id=SOURCE, now=BASE+.2, elapsed=.2)
        assert refused["reason"] == expected and refused["proposal"] is None
        assert subject.observe(viewed(2, True), source_id=SOURCE, now=BASE+.4,
                               elapsed=.4)["proposal"] is None
        subject.observe(viewed(3, False), source_id=SOURCE, now=BASE+.6, elapsed=.6)
        proposal = subject.observe(viewed(4, True), source_id=SOURCE, now=BASE+.8,
                                   elapsed=.8)["proposal"]
        assert proposal["edge"]["view"]["index"] == 0
    finally:
        subject.close()


def test_expired_edge_does_not_extend_deadline(tmp_path):
    subject = detector(tmp_path, ttl_s=.1)
    observe(subject, 0, False)
    result = subject.observe(status(1, True), source_id=SOURCE, now=BASE+.35, elapsed=.35)
    assert result["proposal"] is None and result["reason"] == "edge_expired"
    subject.close()
    restarted = detector(tmp_path, ttl_s=.1)
    observe(restarted, 0, False)
    assert observe(restarted, 1, True)["reason"] == "already_consumed"
    restarted.close()


def test_out_of_range_finite_clock_resets_baseline_on_windows_and_elsewhere(tmp_path):
    subject = detector(tmp_path)
    observe(subject, 0, False)
    result = subject.observe(status(1, True), source_id=SOURCE, now=1e15, elapsed=.1)
    assert result["reason"] == "invalid_observation_time" and result["proposal"] is None
    assert observe(subject, 2, True)["proposal"] is None
    assert observe(subject, 3, False)["proposal"] is None
    assert observe(subject, 4, True)["proposal"] is not None
    subject.close()


def test_reopen_starts_without_baseline_and_consumed_edge_cannot_replay(tmp_path):
    subject = detector(tmp_path)
    observe(subject, 0, False)
    event_id = observe(subject, 1, True)["proposal"]["id"]
    subject.close()
    restarted = detector(tmp_path)
    assert observe(restarted, 1, True)["proposal"] is None
    restarted.close()
    replayed = detector(tmp_path)
    baseline, edge = viewed(0, False), viewed(1, True)
    # A different replay wrapper or an enriched slot projection cannot reissue an edge.
    baseline["samples"], edge["samples"] = 1, 2
    replayed.observe(baseline, source_id=SOURCE, now=BASE, elapsed=0)
    result = replayed.observe(edge, source_id=SOURCE, now=BASE+.2, elapsed=.2)
    assert result["proposal"] is None and result["reason"] == "already_consumed"
    replayed.close()
    with sqlite3.connect(tmp_path/"reactions.sqlite3") as connection:
        assert connection.execute("SELECT event_id FROM consumed").fetchall() == [(event_id,)]


def test_journal_ownership_and_configuration_are_explicit(tmp_path):
    subject = detector(tmp_path)
    with pytest.raises(RuntimeError):
        detector(tmp_path)
    subject.close()
    with pytest.raises(ValueError, match="incompatible_reaction_journal"):
        detector(tmp_path, mode="shadow")


def test_storage_failure_cannot_expose_proposal_or_keep_detector_running(tmp_path):
    subject = detector(tmp_path)
    observe(subject, 0, False)
    subject.connection.close()
    with pytest.raises(sqlite3.ProgrammingError):
        observe(subject, 1, True)
    assert subject.closed
    with pytest.raises(RuntimeError, match="closed"):
        observe(subject, 2, True)


def test_optional_output_guards_are_shadow_assessment_only(tmp_path):
    subject = detector(tmp_path)
    observe(subject, 0, False)
    proposal = observe(subject, 1, True)["proposal"]
    saved = copy.deepcopy(proposal)
    common = dict(now=BASE+.3, muted=False, urgent=False, workload_clear=True)
    assert assess_optional_output(proposal, **common) == "eligible_for_shadow_review"
    for override, expected in (({"muted": True}, "muted"),
                               ({"urgent": True}, "urgent_output_has_priority"),
                               ({"workload_clear": False}, "workload_unknown"),
                               ({"now": BASE+3}, "expired_or_future")):
        assert assess_optional_output(proposal, **dict(common, **override)) == expected
    assert proposal == saved
    subject.close()

def test_continuity_change_refuses_an_edge_with_advancing_counters(tmp_path):
    subject = detector(tmp_path)
    try:
        def read(index, flag, generation):
            value = status(index, flag) | {"continuity_generation": generation}
            return subject.observe(value, source_id=SOURCE, now=BASE + index * .2,
                                   elapsed=index * .2)

        assert read(0, False, 0)["reason"] == "false_baseline"
        dropped = read(1, True, 1)
        assert dropped["reason"] == "source_continuity_changed" and dropped["proposal"] is None
        assert read(2, True, 1)["proposal"] is None
        assert read(3, False, 1)["proposal"] is None
        assert read(4, True, 1)["proposal"]["edge"]["continuity_generation"] == 1
    finally:
        subject.close()
