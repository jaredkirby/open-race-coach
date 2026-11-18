"""Host publication/recovery wiring; synthetic artifacts, never simulator or audio."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcoach.cli import visit
from tests.test_visit import execute, fixture_file


@pytest.mark.parametrize("bad", [False, True])
def test_fixture_visit_publishes_bound_outcome_and_process_receipts(tmp_path, bad):
    fixture = fixture_file(tmp_path, bad=bad)
    root = tmp_path / "v"
    result = execute(["-m", "simcoach.cli.visit", "start", "--present", "--intent", "test only",
                      "--fixture", str(fixture), "--out", str(root), "--max-seconds", "15"])
    assert result.returncode == (1 if bad else 0), result.stdout + result.stderr
    run = next(p for p in root.iterdir() if p.is_dir())
    for name in ("host-process.json", "worker-spawn.json"):
        value = json.loads((run / name).read_text())
        assert value["process"]["pid"] > 0
        assert value["process"]["state"] in ("running", "exited", "absent", "unknown")
    receipt = json.loads((run / "outcome-result.json").read_text())
    assert receipt["status"] == "written"
    assert receipt["software_timing"]["write_and_hash_s"] >= 0
    assert receipt["software_timing"]["after_declared_departure_s"] is None
    report = Path(receipt["report_path"])
    assert report.is_file()
    assert hashlib.sha256(report.read_bytes()).hexdigest() == receipt["report_sha256"]
    outcome = json.loads(report.read_text())
    host = json.loads((run / "status.json").read_text())
    assert host["arrival_confirmed"] is True
    assert host["presence"] == "declared_absent" and host["declared_presence"] is False
    assert outcome["mode"] == "replay" and outcome["clock"]["known"] is True
    assert len(outcome["recordings"]) == 1
    recording = outcome["recordings"][0]
    assert recording["reported_validation"] == ("FAIL" if bad else "PASS")
    if bad:
        assert recording["reported_analysis"] == "NOT RUN" and not recording["analyses"]
        assert recording["lap_review"] is None
        assert (run / "worker/failure.txt").exists()
    else:
        assert recording["analyses"][0]["run_status"] == "complete"
        assert recording["analyses"][0]["analysis_status"] == "insufficient_data"
        assert recording["lap_review"]["statistics"]["valid_laps"] == 1
    assert outcome["unmeasured"]["capture_coverage"] is None
    assert not list(run.rglob("coach_response.json"))


def test_outcome_failure_preserves_terminal_inputs_and_reports_failure(tmp_path, monkeypatch):
    source = tmp_path / "status.json"
    source.write_text('{"status":"complete"}', encoding="utf-8")
    original = source.read_bytes()

    def fail(run):
        (run / "partial-evidence").write_text("preserve")
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(visit, "write_visit_outcome", fail)
    receipt = visit.publish_outcome(tmp_path)
    assert receipt["status"] == "failed" and receipt["report_path"] is None
    assert receipt["error_type"] == "OSError"
    assert receipt["software_timing"] is None
    assert source.read_bytes() == original
    assert (tmp_path / "partial-evidence").read_text() == "preserve"
    assert (tmp_path / "outcome-failure.txt").exists()


@pytest.mark.parametrize("departure,finished,expected", [(8, 11, 3), (11, 12, None), (8, 9, None)])
def test_software_publication_uses_only_ordered_host_counter(
        tmp_path, monkeypatch, departure, finished, expected):
    report = tmp_path / "generated-report.json"
    report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(visit, "write_visit_outcome", lambda _: {"report_path": report})
    times = iter([10, finished])
    monkeypatch.setattr(visit, "time", SimpleNamespace(
        perf_counter=lambda: next(times), time=lambda: 1000))
    receipt = visit.publish_outcome(tmp_path, departure_elapsed=departure)
    if finished < 10:
        assert receipt["software_timing"] is None
    else:
        assert receipt["software_timing"]["write_and_hash_s"] == finished - 10
        assert receipt["software_timing"]["after_declared_departure_s"] == expected


def test_failed_process_probe_is_diagnostic_only(monkeypatch):
    def fail(pid):
        raise OSError("synthetic permission failure")

    monkeypatch.setattr(visit, "probe_process", fail)
    value = visit.process_snapshot(123)
    assert value == dict(pid=123, state="unknown", created_100ns=None,
                         reason="probe_failed_OSError")


def test_inspect_cli_only_reads_selected_visit(tmp_path, monkeypatch, capsys):
    observed = []

    def inspect(run):
        observed.append(run)
        return dict(assessment="unknown", actions_performed=[])

    monkeypatch.setattr(visit, "inspect_visit", inspect)
    assert visit.main(["inspect", str(tmp_path)]) == 0
    assert observed == [tmp_path]
    assert json.loads(capsys.readouterr().out)["actions_performed"] == []
    assert list(tmp_path.iterdir()) == []
