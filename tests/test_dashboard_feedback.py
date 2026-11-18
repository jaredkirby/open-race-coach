import json
import os
from dataclasses import replace

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.ingest.session import create_recorded_session, finalize_recorded_session
from simcoach.live.dashboard_feedback import prepare_feedback, reviewed_feedback, select_feedback
from simcoach.live.visit_review import record_review
from tests.test_session import build_ticks, session_info


def prepared(tmp_path, *, timing="captured", durations=(1, 1.2, 1.1, 3, 1.3)):
    visit = tmp_path / "visit"
    (visit / "worker/sessions").mkdir(parents=True)
    write_status(visit / "status.json", dict(schema_version=1, mode="replay", status="running"))
    info = session_info()
    sid, started, paths = create_recorded_session(visit / "worker/sessions", info)
    lap_ticks = build_ticks(rows_per_lap=20)[:20]
    ticks, start = [], 0
    for lap, duration in enumerate(durations, start=1):
        ticks.extend(replace(tick, t=start+i*duration/20, lap=lap)
                     for i, tick in enumerate(lap_ticks))
        start += duration
    ticks.append(replace(lap_ticks[0], t=start, lap=6))
    finalize_recorded_session(paths, sid, info, started, ticks, sim_invalidated_laps={2})
    if timing != "captured":
        assert timing in {"simulator", "mixed"}
        path = paths.root / "laps.jsonl"
        laps = [json.loads(line) for line in path.read_text().splitlines()]
        for row in laps:
            if timing == "simulator" or row["lap"] == 1:
                row["lap_time_source"] = "sim"
        # Explicitly synthetic simulator-time input, never a real recording edit.
        path.write_text("".join(json.dumps(row) + "\n" for row in laps))
    card = prepare_feedback(visit, paths.root, target_laps=3, target_spread_s=.5)
    return visit, paths.root, card


def accept(visit, card, verdict="useful"):
    return record_review(visit, output=card, verdict=verdict, reviewer="chief",
                         note="Synthetic review. PRIVATE NOTE must not be displayed.")["path"]


@pytest.mark.skipif(os.name != "nt", reason="Windows extended feedback paths")
def test_long_recording_path_can_be_prepared_reviewed_and_selected(tmp_path):
    from simcoach.live.visit_review import _io

    root = tmp_path / ("w" * (210 - len(str(tmp_path)) - 1))
    visit, session, card = prepared(_io(root))
    assert len(str(session / "session.yaml")) > 260
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    review = accept(visit, card)
    selection = select_feedback(visit, review)
    result = reviewed_feedback(visit, selection)
    assert result["state"] == "reviewed"
    assert result["card"]["statistics"]["valid_laps"] == 4
    assert str(visit) not in json.dumps(result)
    assert {p.name: p.read_bytes() for p in session.iterdir()} == before


def test_validated_card_keeps_slow_invalid_and_fragment_laps_and_requires_review(tmp_path):
    visit, session, card = prepared(tmp_path)
    value = json.loads(card.read_text())["display"]
    assert len(value["laps"]) == 6 and value["statistics"]["valid_laps"] == 4
    assert value["statistics"]["spread_s"] > 1.8  # Slow valid lap is retained.
    assert value["exclusions"] == {"sim_invalidated": 1, "bad_lap_progress": 1}
    assert reviewed_feedback(visit, visit / "output/dashboard-selection.json")["card"] is None
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    review = accept(visit, card)
    selection = select_feedback(visit, review)
    result = reviewed_feedback(visit, selection)
    assert result["state"] == "reviewed" and result["card"] == value
    assert not result["delivery_verified"] and not result["driver_endorsed"]
    encoded = json.dumps(result)
    assert "PRIVATE NOTE" not in encoded and str(visit) not in encoded
    assert "sources" not in result["card"]
    assert {p.name: p.read_bytes() for p in session.iterdir()} == before


@pytest.mark.parametrize("verdict", ["insufficient", "misleading", "failed"])
def test_later_explicit_review_selection_suppresses_objective_or_entire_card(tmp_path, verdict):
    visit, _, card = prepared(tmp_path)
    first = accept(visit, card)
    selection = select_feedback(visit, first)
    assert reviewed_feedback(visit, selection)["card"]["objective"]
    later = accept(visit, card, verdict)
    select_feedback(visit, later)
    result = reviewed_feedback(visit, selection)
    if verdict == "insufficient":
        assert result["card"]["objective"] is None
    else:
        assert result["state"] == "withheld" and result["card"] is None
    assert len(list((visit / "reviews").glob("*/review.json"))) == 2


def test_tampered_reviewed_card_is_withheld_without_rewriting_it(tmp_path):
    visit, _, card = prepared(tmp_path)
    selection = select_feedback(visit, accept(visit, card))
    card.write_text(card.read_text() + " ")
    assert reviewed_feedback(visit, selection)["state"] == "unavailable"
    assert card.read_text().endswith(" ")


def test_unreviewed_foreign_or_driver_review_cannot_be_selected(tmp_path):
    visit, _, card = prepared(tmp_path)
    driver = record_review(visit, output=card, verdict="useful", note="Synthetic driver opinion",
                           reviewer="driver", driver_feedback_confirmed=True)["path"]
    with pytest.raises(ValueError, match="selection_unverified"):
        select_feedback(visit, driver)
    assert not (visit / "output/dashboard-selection.json").exists()
    assert list((visit / "output/dashboard-selections").glob("*.json"))


def test_invalid_objective_preserves_failed_card_attempt(tmp_path):
    visit, session, _ = prepared(tmp_path)
    with pytest.raises(ValueError):
        prepare_feedback(visit, session, target_spread_s=float("nan"))
    assert list((visit / "output/dashboard").glob("*/failure.json"))


def test_selected_review_bytes_are_pinned_and_malformed_selection_is_unavailable(tmp_path):
    from pathlib import Path
    visit, _, card = prepared(tmp_path)
    review = accept(visit, card)
    selection = select_feedback(visit, review)
    Path(review).write_text(Path(review).read_text() + " ")
    assert reviewed_feedback(visit, selection)["state"] == "unavailable"
    selection.write_text("[]")
    assert reviewed_feedback(visit, selection)["state"] == "unavailable"


@pytest.mark.parametrize("mode", ["simulator", "mixed"])
def test_feedback_preserves_timing_sources_without_mixed_statistics(tmp_path, mode):
    visit, session, card = prepared(tmp_path, timing=mode)
    path = session / "laps.jsonl"
    laps = [json.loads(line) for line in path.read_text().splitlines()]
    value = json.loads(card.read_text())
    display = value["display"]
    assert display["timing_basis"] == ("simulator_time" if mode == "simulator" else "mixed")
    assert value["schema_version"] == 2
    assert [row["lap_time_source"] for row in display["laps"]] == [
        row["lap_time_source"] for row in laps]
    assert display["timing_basis"] == ("simulator_time" if mode == "simulator" else "mixed")
    assert display["statistics"]["valid_laps"] == 4
    if mode == "mixed":
        assert display["statistics"]["best_s"] is None
        assert display["statistics"]["median_s"] is None
        assert display["statistics"]["spread_s"] is None
        assert display["objective"] is None
        assert set(display["timing_groups"]) == {"sim", "derived_from_ticks"}
        assert display["timing_groups"]["derived_from_ticks"]["spread_s"] > 1.8
    else:
        assert display["statistics"]["spread_s"] > 1.8
        assert display["objective"]["timing_basis"] == "simulator_time"
    selected = select_feedback(visit, accept(visit, card))
    assert reviewed_feedback(visit, selected)["card"] == display


def test_legacy_card_is_preserved_but_not_given_inferred_timing_sources(tmp_path):
    visit, _, card = prepared(tmp_path)
    value = json.loads(card.read_text())
    value["schema_version"] = 1
    for row in value["display"]["laps"]:
        del row["lap_time_source"]
    card.write_text(json.dumps(value))
    original = card.read_bytes()
    review = accept(visit, card)
    with pytest.raises(ValueError, match="selection_unverified"):
        select_feedback(visit, review)
    assert card.read_bytes() == original
    assert not (visit / "output/dashboard-selection.json").exists()
