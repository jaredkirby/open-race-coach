import hashlib
import json

import pytest

from simcoach.cli.archive_coverage import main
from simcoach.live import visit_coverage
from tests.test_archive_coverage import BASE, sample, segment, stamp
from tests.test_archive_coverage import archive as make_archive


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path, *, mode="live"):
    archive = make_archive(tmp_path)
    visit = tmp_path / "visit"
    clock = dict(schema_version=1, visit_id="synthetic", status="complete",
                 terminal_intent="complete", publication_complete=True, publication_error=None,
                 presence="declared_absent", started_at=stamp(1), updated_at=stamp(1.3),
                 elapsed_s=.3)
    save(visit / "status.json", dict(
        schema_version=1, mode=mode, status="complete", archive=str(archive)))
    save(visit / "stop", dict(at=BASE + 1.3, declaration="driver_left", elapsed=21.3))
    for name in ("status", "summary"):
        save(visit / f"clock/{name}.json", clock)
    identity = hashlib.sha256((archive / "run.json").read_bytes()).hexdigest()
    return visit, archive, identity


def test_declared_arrival_includes_prehost_and_preserves_terminal_sources(tmp_path, capsys):
    visit, archive, identity = fixture(tmp_path)
    arrival = tmp_path / "arrival.json"
    save(arrival, dict(schema_version=1, kind="reviewed_arrival_declaration", visit_id="synthetic",
                       at=stamp(.9), basis="Generated early declaration"))
    segment(archive, 1, [sample(1.02, 2), sample(1.07, 4), sample(1.12, 6)])
    before = (visit / "clock/summary.json").read_bytes()
    out = visit / "coverage/audit"
    assert main(["--archive", str(archive), "--visit", str(visit), "--archive-sha256", identity,
                 "--arrival", str(arrival), "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["metric_known"] is True
    report = json.loads((out / "report.json").read_text())
    assert report["envelope"]["duration_s"] == pytest.approx(.4)
    assert report["envelope"]["prehost_declared_s"] == pytest.approx(.1)
    assert report["unverified_or_unknown_s"] == pytest.approx(.3)
    assert report["lower_bound_percent"] == pytest.approx(25, abs=.001)
    assert report["live_validation"] == "NOT RUN"
    assert (visit / "clock/summary.json").read_bytes() == before


def test_disjoint_tiles_clip_same_segment_without_counting_it_twice(tmp_path, monkeypatch):
    visit, archive, identity = fixture(tmp_path)
    segment(archive, 1, [sample(1.08, 2), sample(1.16, 4), sample(1.24, 6)])
    monkeypatch.setattr(visit_coverage, "TILE_SECONDS", .15)
    report = visit_coverage.audit_visit_coverage(visit, archive, source_id=identity,
                                                 output_dir=visit / "coverage/tiles")
    assert report["metric_known"] and report["envelope"]["scope"] == "host_start_to_departure"
    assert report["verified_span_lower_bound_s"] == pytest.approx(.16)
    assert len(report["tiles"]) >= 2


def test_duplicate_archive_clock_keeps_denominator_without_a_metric(tmp_path):
    visit, archive, identity = fixture(tmp_path)
    segment(archive, 1, [sample(1.1, 2), sample(1.1, 4, wall=1.105)])
    report = visit_coverage.audit_visit_coverage(visit, archive, source_id=identity,
                                                 output_dir=visit / "coverage/failure")
    assert report["result"] == "FAIL" and report["tiles"][0]["integrity_result"] == "FAIL"
    assert report["envelope"]["duration_s"] == pytest.approx(.3)
    assert report["lower_bound_percent"] is None


def test_absent_manifest_stays_unknown_instead_of_claiming_zero_coverage(tmp_path):
    visit, archive, identity = fixture(tmp_path)
    (archive / "segments.jsonl").unlink()
    report = visit_coverage.audit_visit_coverage(visit, archive, source_id=identity,
                                                 output_dir=visit / "coverage/idle")
    assert report["result"] == "NOT RUN" and report["lower_bound_percent"] is None
    assert report["tiles"][0]["reason"] == "no_closed_candidates"


@pytest.mark.parametrize("append", [False, True], ids=["changed-contributor", "manifest-append"])
def test_final_recheck_distinguishes_changed_segment_from_append(tmp_path, monkeypatch, append):
    visit, archive, identity = fixture(tmp_path)
    filename = segment(archive, 1, [sample(1.02, 2), sample(1.07, 4), sample(1.12, 6)])
    publish = visit_coverage.write_archive_coverage_report

    def publish_then_change(report, out):
        receipt = publish(report, out)
        if append:
            segment(archive, 2, [sample(2.2, 8), sample(2.25, 10)])
        else:
            file = archive / filename
            file.write_bytes(file.read_bytes() + b"changed generated input")
        return receipt

    monkeypatch.setattr(visit_coverage, "write_archive_coverage_report", publish_then_change)
    report = visit_coverage.audit_visit_coverage(visit, archive, source_id=identity,
                                                 output_dir=visit / "coverage/recheck")
    if append:
        assert report["metric_known"] and report["verified_span_lower_bound_s"] == pytest.approx(.1)
    else:
        assert report["tiles"][0]["integrity_result"] == "PASS"
        assert report["metric_unknown_reason"] == "contributing_segment_changed"
        assert report["lower_bound_percent"] is None


@pytest.mark.parametrize("change", ["replay", "different_archive", "mismatched_departure"])
def test_wrong_envelope_is_refused_and_retained(tmp_path, change):
    visit, archive, identity = fixture(tmp_path, mode="replay" if change == "replay" else "live")
    if change == "different_archive":
        parent = tmp_path / "other"
        parent.mkdir()
        archive = make_archive(parent)
    if change == "mismatched_departure":
        save(visit / "stop", dict(at=BASE + 1.4, declaration="driver_left", elapsed=21.4))
    out = visit / "coverage/rejected"
    with pytest.raises(ValueError):
        visit_coverage.audit_visit_coverage(visit, archive, source_id=identity, output_dir=out)
    assert (out / "failure.json").is_file() and not (out / "report.json").exists()
