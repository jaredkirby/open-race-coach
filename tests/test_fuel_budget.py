"""Fuel estimates use all eligible laps and retain uncertainty; synthetic raw pages."""
import base64
import json
from dataclasses import asdict

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli import fuel_budget as cli
from simcoach.live.fuel_budget import LapFuel, analyze_fuel_budget, draft_plan, rate_estimate
from simcoach.live.supervisor import Context, FuelPlan
from tests.test_archive_coverage import archive, segment, stamp

CONTEXT = Context("test car", "test track", "short", 5)


def pages():
    rows = []
    # First fragment + four fully bounded laps; invalidated second full lap is
    # also the largest burn and must remain in the fuel calculation.
    fuel = 8.0
    for i in range(51):
        page = SharedMemory()
        page.mVersion = 14
        page.mSequenceNumber = i*2+2
        page.mNumParticipants = 3
        page.mViewedParticipantIndex = 0
        page.mCarName = page.mCarNames[0].value = b"test car"
        page.mTrackLocation, page.mTrackVariation = b"test track", b"short"
        page.mGameState, page.mSessionState, page.mRaceState = 2, 5, 2
        page.mRaceStates[0] = 2
        page.mTrackLength, page.mFuelCapacity = 100, 33
        page.mFuelLevel = fuel / 33
        part = page.mParticipantInfo[0]
        part.mIsActive = True
        part.mLapsCompleted = i//10
        part.mCurrentLapDistance = (i%10)*10
        page.mLapInvalidated = 20 <= i < 30
        value = dict(schema_version=1, at=stamp(i/10), offset_s=i/10,
            sequence=i*2+2, struct_size_bytes=20700,
            summary=dict(mVersion=14, mSequenceNumber=i*2+2),
            raw_base64=base64.b64encode(bytes(page)).decode())
        rows.append(value)
        fuel -= .06 if 20 <= i < 30 else .04
    return rows


def audit(run, names, **changes):
    return analyze_fuel_budget(run, segment_names=names, start_at=stamp(0), end_at=stamp(6),
                              context=CONTEXT, participant_index=0, **changes)


def test_invalid_slow_lap_remains_in_maximum_and_explicit_load(tmp_path):
    run = archive(tmp_path)
    name = segment(run, 1, pages())
    report = audit(run, [name])
    assert report["integrity_result"] == "PASS" and report["scope_complete"]
    assert report["estimate"]["eligible_laps"] == 4
    assert report["estimate"]["max_observed_litres"] == pytest.approx(.6, abs=1e-5)
    assert max(r["consumed_litres"] for r in report["laps"] if r["simulator_invalidated"]) > .59
    assert report["estimate"]["litres_per_lap"] >= .75
    assert report["laps"][0]["reasons"] == ["partial_start"]
    plan = draft_plan(report, timed_laps=5, out_laps=0)
    assert plan.required_load == plan.litres_per_lap*8
    assert plan.return_threshold_litres == plan.litres_per_lap*3
    assert not report["local_player_identity_verified"] and not report["automatic_approval"]


@pytest.mark.parametrize("fault", ["hash", "count", "unsupported_layout"])
def test_integrity_failure_withholds_candidate_even_after_good_prefix(tmp_path, fault):
    run = archive(tmp_path)
    rows = pages()
    if fault == "unsupported_layout":
        rows[-1]["struct_size_bytes"] = 1
    name = segment(run, 1, rows, **({"samples": 2} if fault == "count" else {}))
    if fault == "hash":
        with (run/name).open("ab") as output:
            output.write(b"changed")
    report = audit(run, [name])
    assert report["integrity_result"] == "FAIL"
    assert report["estimate"]["litres_per_lap"] is None
    with pytest.raises(ValueError):
        draft_plan(report, timed_laps=5)


@pytest.mark.parametrize("fault", ["refuel", "counter", "view", "capacity", "gap", "freeze"])
def test_continuity_fault_is_retained_and_excluded(tmp_path, fault):
    from simcoach.live.fuel_budget import decode
    values = [decode(row, SharedMemory) for row in pages()]
    if fault == "refuel":
        values[24]["litres"] += 1
    elif fault == "counter":
        values[24]["completed"] = 0
    elif fault == "view":
        values[24]["index"] = 1
    elif fault == "capacity":
        values[24]["capacity"] += 1
    elif fault == "gap":
        del values[22:27]
    else:
        for row in values[22:27]:
            row["sequence"] = values[21]["sequence"]
    evidence = LapFuel(CONTEXT, 0, .25)
    for value in values:
        evidence.add(value)
    evidence.finish()
    assert any(not r["eligible"] and r["completed_start"] == 2 for r in evidence.rows)
    assert len([r for r in evidence.rows if r["eligible"]]) < 4
    assert evidence.rows  # Failed portions stay inspectable.


def test_coarse_equal_offsets_are_reported_without_repair(tmp_path):
    run = archive(tmp_path)
    rows = pages()
    rows[22]["offset_s"] = rows[21]["offset_s"]
    # A coarse 15.625 ms tick can repeat for nearby observations; this is volume
    # per observed lap, not proof of strictly timed archive coverage.
    rows[22]["at"] = stamp(2.11)
    rows[23]["at"], rows[23]["offset_s"] = stamp(2.2), 2.2
    name = segment(run, 1, rows)
    report = audit(run, [name])
    assert report["diagnostics"]["coarse_timestamp_pair"] == 1
    assert report["estimate"]["eligible_laps"] == 4
    assert rows[22]["offset_s"] == 2.1


def test_constant_fuel_is_insufficient_and_omitted_segments_break_laps(tmp_path):
    run = archive(tmp_path)
    rows = pages()
    for row in rows:
        page = SharedMemory.from_buffer_copy(base64.b64decode(row["raw_base64"]))
        page.mFuelLevel = .2
        row["raw_base64"] = base64.b64encode(bytes(page)).decode()
    names = [segment(run, 1, rows[:25]), segment(run, 3, rows[25:])]
    report = audit(run, names)
    assert report["diagnostics"]["omitted_segment"] == 1
    assert report["estimate"]["status"] == "insufficient_data"
    assert report["estimate"]["litres_per_lap"] is None


def test_margin_rounds_up_and_never_trims_largest_lap():
    rows = [dict(eligible=True, consumed_litres=v) for v in (.3, .4, .6)]
    estimate = rate_estimate(rows)
    assert estimate["litres_per_lap"] == .75
    assert rate_estimate(rows[:2])["status"] == "insufficient_data"
    rows.append(dict(eligible=False, consumed_litres=.7, reasons=["partial_start"]))
    assert rate_estimate(rows)["status"] == "unresolved_partial_consumption"
    assert rate_estimate(rows)["litres_per_lap"] is None


@pytest.mark.parametrize("gap", [False, True])
def test_observed_stationary_grid_to_green_includes_standing_start_lap(tmp_path, gap):
    from simcoach.live.fuel_budget import decode
    values = [decode(row, SharedMemory) for row in pages()]
    pre = dict(values[0], race=1, game=4, at=values[0]["at"]-(1 if gap else .1),
               offset=0, sequence=0, litres=8.4)
    # Match the green fuel observation; the larger first lap must affect the rate.
    values[0]["litres"] = 8.4
    evidence = LapFuel(CONTEXT, 0, .25)
    evidence.add(pre)
    for row in values:
        evidence.add(row)
    evidence.finish()
    assert evidence.rows[0]["eligible"] is not gap
    estimate = rate_estimate(evidence.rows)
    if gap:
        assert estimate["status"] == "unresolved_partial_consumption"
    else:
        assert estimate["eligible_laps"] == 5
        assert estimate["max_observed_litres"] > .79


def test_cli_writes_review_wrapper_not_directly_usable_plan(tmp_path, capsys):
    run = archive(tmp_path)
    name = segment(run, 1, pages())
    context = tmp_path / "context.json"
    context.write_text(json.dumps(asdict(CONTEXT)))
    out = tmp_path / "review"
    assert cli.main(["--archive", str(run), "--segment", name, "--start-at", stamp(0),
        "--end-at", stamp(6), "--context", str(context), "--participant-index", "0",
        "--timed-laps", "5", "--out-laps", "0", "--out", str(out)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["estimate"] == "review_required"
    candidate = json.loads((out / "candidate.json").read_text())
    assert candidate["status"] == "chief_review_required" and not candidate["fuel_approval"]
    with pytest.raises((TypeError, KeyError)):
        FuelPlan.from_dict(candidate)
    assert FuelPlan.from_dict(candidate["plan"]).timed_laps == 5

def test_manifest_break_cannot_join_fuel_spans_with_coherent_counters(tmp_path):
    run = archive(tmp_path)
    rows = pages()
    names = [
        segment(run, 1, rows[:25], continuity_break_after=True),
        segment(run, 2, rows[25:]),
    ]
    report = audit(run, names)
    assert report["integrity_result"] == "PASS"
    assert report["diagnostics"]["archive_continuity_break"] == 1
    assert report["estimate"]["eligible_laps"] == 3
    assert any("archive_continuity_break" in row["reasons"] for row in report["laps"])
    assert all(row["end_at"] <= stamp(2.4) or row["start_at"] >= stamp(2.5)
               for row in report["laps"])


def test_known_continuity_break_does_not_hide_a_larger_partial_burn():
    rows = [dict(eligible=True, consumed_litres=value) for value in (.3, .4, .6)]
    rows.append(dict(eligible=False, consumed_litres=.7, reasons=["archive_continuity_break"]))
    result = rate_estimate(rows)
    assert result["status"] == "unresolved_partial_consumption"
    assert result["max_partial_litres"] == .7 and result["litres_per_lap"] is None

def uncounted_crossing_pages(fault=None):
    rows = pages()
    for i, row in enumerate(rows):
        page = SharedMemory.from_buffer_copy(base64.b64decode(row["raw_base64"]))
        player = page.mParticipantInfo[0]
        player.mCurrentLap = max(1, i // 10)
        player.mLapsCompleted = max(0, i // 10 - 1)
        page.mLapInvalidated = page.mLapsInvalidated[0] = i < 10
        if i in (9, 10):
            page.mNumSectors = 3
            player.mCurrentSector = 2 if i == 9 else 0
            player.mCurrentLapDistance = 99.5 if i == 9 else 0
            player.mWorldPosition[0] = 0 if i == 9 else 1
            page.mSpeed = 10
            page.mCurrentTime = -1 if i == 9 else .02
        if i == 10:
            if fault == "teleport":
                player.mWorldPosition[0] = 100
            elif fault == "distance":
                player.mCurrentLapDistance = 10
            elif fault == "sector":
                player.mCurrentSector = 1
            elif fault == "timer":
                page.mCurrentTime = 20
            elif fault == "flags":
                page.mLapsInvalidated[0] = True
            elif fault == "track_length":
                page.mTrackLength = 130
        row["raw_base64"] = base64.b64encode(bytes(page)).decode()
    return rows


def test_verified_invalidated_crossing_can_anchor_following_fuel_lap(tmp_path):
    run = archive(tmp_path)
    name = segment(run, 1, uncounted_crossing_pages())
    report = audit(run, [name])
    assert report["estimate"]["eligible_laps"] == 4
    assert report["diagnostics"]["observed_invalidated_crossing"] == 1
    expired, following = report["laps"][:2]
    assert expired["simulator_invalidated"] and not expired["eligible"]
    assert expired["completed_start"] == expired["completed_end"] == 0
    assert expired["end_boundary"] == following["start_boundary"] == "observed_invalidated_crossing"
    assert following["eligible"] and following["consumed_litres"] == pytest.approx(.4, abs=1e-5)


@pytest.mark.parametrize("fault", ["teleport", "distance", "sector", "timer", "flags",
                                  "track_length", "marked_source_break"])
def test_inconsistent_uncounted_crossing_keeps_fuel_span_unqualified(tmp_path, fault):
    run = archive(tmp_path)
    rows = uncounted_crossing_pages(fault)
    names = ([segment(run, 1, rows[:10], continuity_break_after=True), segment(run, 2, rows[10:])]
             if fault == "marked_source_break" else [segment(run, 1, rows)])
    report = audit(run, names)
    assert report["integrity_result"] == "PASS"
    assert "observed_invalidated_crossing" not in report["diagnostics"]
    assert report["estimate"]["eligible_laps"] == 3
