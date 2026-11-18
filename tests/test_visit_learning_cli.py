"""Explicit review and fresh outcome snapshots preserve old outputs and role boundaries."""

import json
from pathlib import Path

import pytest

from simcoach.cli import visit as cli
from simcoach.live.visit_outcome import build_visit_outcome, write_visit_outcome
from tests.test_visit_outcome import put, visit


def test_chief_review_and_correction_survive_in_new_outcome(tmp_path, capsys):
    run = visit(tmp_path)
    first = write_visit_outcome(run)
    first_bytes = Path(first["report_path"]).read_bytes()
    for verdict, note in (("useful", "Synthetic chief assessment, not driver feedback."),
                          ("misleading", "Correction: this is only a test clock, not seat time.")):
        assert cli.main(["review", str(run), "--output", first["report_path"],
                         "--verdict", verdict, "--note", note]) == 0
    assert cli.main(["outcome", str(run), "--out", str(tmp_path / "new-outcome")]) == 0
    fresh = json.loads((tmp_path / "new-outcome/report.json").read_text())
    reviews = [row["files"]["review.json"]["reported"] for row in fresh["output_reviews"]]
    assert {row["verdict"] for row in reviews} == {"useful", "misleading"}
    assert all(row["reviewer"] == "chief" and row["driver_feedback_confirmed"] is False
               and row["review_latency_utc_s"] is None for row in reviews)
    assert fresh["unmeasured"]["useful_debrief_latency_s"] is None
    assert Path(first["report_path"]).read_bytes() == first_bytes
    assert not (run / "outcome-result.json").exists()  # Refresh does not replace host's receipt.
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_driver_review_requires_explicit_feedback_and_preserves_failed_attempt(tmp_path):
    run = visit(tmp_path)
    original = write_visit_outcome(run)
    with pytest.raises(SystemExit) as error:
        cli.main(["review", str(run), "--output", original["report_path"], "--reviewer", "driver",
                  "--verdict", "useful", "--note", "Synthetic missing confirmation"])
    assert error.value.code == 2
    result = build_visit_outcome(run)
    assert len(result["output_reviews"]) == 1
    files = result["output_reviews"][0]["files"]
    assert files["review.json"]["reported"] is None
    assert files["failure.json"]["reported"]["review_accepted"] is False


def test_scoped_or_unsupported_coverage_attachments_do_not_become_whole_visit_metric(tmp_path):
    run = visit(tmp_path)
    for name, version in (("scoped", 1), ("unsupported", 99)):
        put(run / "coverage" / name / "report.json",
            dict(schema_version=version, verified_sample_span_s=1,
                 requested_window={"start_at": "partial test window"}, metric_known=True))
    result = build_visit_outcome(run)
    rows = result["archive_coverage_audits"]
    assert len(rows) == 2
    assert {r["files"]["report.json"]["state"] for r in rows} == {
        "reported_version_1", "unknown_or_unsupported"}
    assert result["unmeasured"]["capture_coverage"] is None
    assert "not automatically whole-visit" in result["supplemental_evidence_scope"]
