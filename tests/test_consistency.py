import hashlib
import json

import pytest

from simcoach.cli.dashboard_feedback import main
from simcoach.live.consistency import compare_plan, prepare_plan
from tests.test_dashboard_feedback import prepared


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_config(first=3):
    return dict(baseline_first=3, followup_first=first, count=3, spread_s=.5,
                conditions="Synthetic fixed practice settings, matched phase")


def test_cli_grades_only_prespecified_laps_and_separates_pace_from_spread(tmp_path, capsys):
    old_visit, old_session, _ = prepared(tmp_path / "old")
    assert main(["plan-consistency", "--visit", str(old_visit), "--session", str(old_session),
                 "--baseline-first", "3", "--followup-first", "3", "--count", "3",
                 "--target-spread", ".5", "--conditions",
                 "Synthetic matched settings and phase"]) == 0
    plan = json.loads(capsys.readouterr().out)
    new_visit, new_session, _ = prepared(tmp_path / "new", durations=(1, 1.2, 1.1, 1, 1.2))
    assert main(["compare-consistency", "--visit", str(new_visit), "--session", str(new_session),
                 "--baseline-visit", str(old_visit), "--plan", plan["path"],
                 "--plan-sha256", plan["plan_sha256"], "--conditions-match", "matched",
                 "--evidence", "Synthetic settings and selected attempt phase matched"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    with open(receipt["path"], encoding="utf-8") as stream:
        result = json.load(stream)
    assert result["status"] == "compared"
    assert result["change"]["target_met"] is True
    # Captured time spans 19 intervals across the 20 generated ticks per lap.
    assert result["change"]["median_s"] == pytest.approx(-.19)
    assert result["change"]["spread_s"] == pytest.approx(-1.615)
    assert [row["lap"] for row in result["followup"]["window"]] == [3, 4, 5]
    assert result["baseline"]["statistics"]["spread_s"] == pytest.approx(1.805)
    assert next(row for row in result["followup"]["laps"] if row["lap"] == 2)["valid"] is False


@pytest.mark.parametrize("first,timing,conditions,reason", [
    (2, "captured", "matched", "planned_attempts_incomplete_invalid_or_mixed"),
    (5, "captured", "matched", "planned_attempts_incomplete_invalid_or_mixed"),
    (3, "simulator", "matched", "timing_basis_differs"),
    (3, "captured", "unknown", "conditions_unknown"),
], ids=["invalid", "missing", "other-clock", "unknown-conditions"])
def test_withheld_comparison_keeps_all_fixed_attempts(tmp_path, first, timing, conditions, reason):
    old_visit, old_session, _ = prepared(tmp_path / "old")
    plan = prepare_plan(old_visit, old_session, **plan_config(first=first))
    new_visit, new_session, _ = prepared(tmp_path / "new", timing=timing)
    path = compare_plan(old_visit, plan, new_visit, new_session, plan_sha256=digest(plan),
                        conditions_match=conditions, evidence="Synthetic comparison evidence")
    report = json.loads(path.read_text())
    assert report["status"] == "withheld" and report["reason"] == reason
    assert report["change"] is None
    window = report["followup"]["window"]
    assert [item["lap"] for item in window] == list(range(first, first + 3))
    if first == 5:
        assert window[-1] == dict(lap=7, record=None)
    if first == 2:
        assert window[0]["record"]["valid"] is False


def test_conducted_laps_cannot_be_graded_as_prospectively_planned(tmp_path):
    old_visit, old_session, _ = prepared(tmp_path / "old")
    new_visit, new_session, _ = prepared(tmp_path / "new")
    plan = prepare_plan(old_visit, old_session, **plan_config())
    path = compare_plan(old_visit, plan, new_visit, new_session, plan_sha256=digest(plan),
                        conditions_match="matched", evidence="Synthetic settings matched")
    report = json.loads(path.read_text())
    assert report["reason"] == "followup_not_after_plan" and report["change"] is None


def test_changed_plan_preserves_failed_attempt_without_reading_followup(tmp_path):
    visit, session, _ = prepared(tmp_path)
    plan = prepare_plan(visit, session, **plan_config())
    expected = digest(plan)
    plan.write_text(plan.read_text() + "\n")  # Synthetic file change only.
    with pytest.raises(ValueError, match="consistency_plan_hash_mismatch"):
        compare_plan(visit, plan, visit, session, plan_sha256=expected,
                     conditions_match="matched", evidence="Synthetic settings matched")
    assert list((visit / "output/consistency").glob("*/failure.json"))
    assert not list((visit / "output/consistency").glob("*/comparison.json"))
