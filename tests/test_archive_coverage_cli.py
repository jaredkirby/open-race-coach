"""The offline CLI preserves incomplete audit results instead of reporting success."""

import json

import pytest

from simcoach.cli import archive_coverage


@pytest.mark.parametrize("integrity,known,complete,code", [
    ("PASS", True, True, 0), ("FAIL", False, True, 1),
    ("PASS", True, False, 1), ("NOT RUN", False, False, 1),
])
def test_cli_writes_every_result_and_distinguishes_incomplete_scope(
        tmp_path, monkeypatch, capsys, integrity, known, complete, code):
    calls = []
    report = dict(integrity_result=integrity, metric_known=known, scope_complete=complete)

    def analyze(archive, **kwargs):
        calls.append((archive, kwargs))
        return report

    def write(value, destination):
        destination.mkdir()
        path = destination / "report.json"
        path.write_text(json.dumps(value))
        return dict(report_path=path, report=value)

    monkeypatch.setattr(archive_coverage, "analyze_archive_coverage", analyze)
    monkeypatch.setattr(archive_coverage, "write_archive_coverage_report", write)
    out = tmp_path / "out"
    result = archive_coverage.main([
        "--archive", str(tmp_path / "archive"), "--start-at", "2026-01-01T00:00:00Z",
        "--end-at", "2026-01-01T00:01:00Z", "--segment", "samples-000001.jsonl.gz",
        "--segment", "samples-000002.jsonl.gz", "--out", str(out)])
    assert result == code and (out / "report.json").is_file()
    assert calls[0][1]["segment_names"] == ["samples-000001.jsonl.gz", "samples-000002.jsonl.gz"]
    assert json.loads(capsys.readouterr().out)["integrity_result"] == integrity


def test_explicit_segment_selection_is_required(tmp_path):
    with pytest.raises(SystemExit) as error:
        archive_coverage.main(["--archive", str(tmp_path), "--start-at", "2026-01-01T00:00:00Z",
                               "--end-at", "2026-01-01T00:01:00Z", "--out", str(tmp_path / "out")])
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []
