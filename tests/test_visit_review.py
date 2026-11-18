import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live import visit_review
from simcoach.live.visit_clock import VisitClock


def make_visit(tmp_path, *, mode="live", ended=True):
    run = tmp_path/"visit"
    run.mkdir()
    (run/"worker").mkdir()
    write_status(run/"status.json", dict(schema_version=1, mode=mode, status="complete"))
    output = run/"worker/debrief.md"
    output.write_text("Synthetic output: insufficient usable laps.\n")
    if ended:
        clock = VisitClock(run/"clock", presence_confirmed=True,
                           at=datetime.fromtimestamp(1000, UTC), elapsed=10)
        clock.end(at=datetime.fromtimestamp(1002, UTC), elapsed=12)
        write_status(run/"stop", dict(declaration="driver_left", at=1002, elapsed=12))
    return run, output


def record(run, output, **kwargs):
    return visit_review.record_review(run, output=output, verdict="insufficient",
                                     note="Synthetic chief review.", clock=lambda: 1005, **kwargs)


@pytest.mark.parametrize("verdict", visit_review.VERDICTS)
def test_each_verdict_binds_exact_output_and_role_without_delivery_claim(tmp_path, verdict):
    run, output = make_visit(tmp_path)
    before = output.read_bytes()
    result = visit_review.record_review(run, output=output, verdict=verdict,
                                       note="Synthetic assessment.", clock=lambda: 1005)
    data = result["review"]
    assert json.loads(Path(result["path"]).read_text()) == data
    assert data["output"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert data["output"]["path"] == str(output.resolve())
    assert data["verdict"] == verdict and data["reviewer"] == "chief"
    assert data["review_latency_utc_s"] == 3
    assert data["driver_feedback_confirmed"] is data["delivery_verified"] is False
    assert data["audio_heard"] is None
    assert output.read_bytes() == before


def test_corrections_are_additive_and_never_replace_old_review(tmp_path):
    run, output = make_visit(tmp_path)
    first = record(run, output)
    before = Path(first["path"]).read_bytes()
    second = visit_review.record_review(run, output=output, verdict="misleading",
                                       note="Correction: synthetic missing evidence.",
                                       clock=lambda: 1006)
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == before
    assert len(list((run/"reviews").glob("*/review.json"))) == 2


def test_driver_requires_confirmation_and_invalid_attempt_is_retained(tmp_path):
    run, output = make_visit(tmp_path)
    with pytest.raises(ValueError, match="explicit_driver_feedback"):
        record(run, output, reviewer="driver")
    failure = list((run/"reviews").glob("*/failure.json"))
    assert len(failure) == 1
    assert json.loads(failure[0].read_text())["review_accepted"] is False
    assert list((run/"reviews").glob("*/attempt.json"))
    accepted = record(run, output, reviewer="driver", driver_feedback_confirmed=True)["review"]
    assert accepted["driver_feedback_confirmed"] is True
    assert accepted["reviewer_identity_authenticated"] is False
    assert accepted["audio_heard"] is None


def test_confirmation_does_not_convert_chief_review_into_driver_opinion(tmp_path):
    run, output = make_visit(tmp_path)
    review = record(run, output, driver_feedback_confirmed=True)["review"]
    assert review["reviewer"] == "chief"
    assert review["driver_feedback_confirmed"] is False


@pytest.mark.parametrize("change", ["missing", "mismatch", "interrupted", "publication_failed",
                                    "future", "stop_disagrees", "replay", "wrong_visit"])
def test_latency_requires_published_matching_declared_live_departure(tmp_path, change):
    run, output = make_visit(tmp_path, mode="replay" if change == "replay" else "live")
    if change == "missing":
        (run/"clock/summary.json").unlink()
    elif change == "mismatch":
        write_status(run/"clock/status.json", dict(schema_version=1, status="running"))
    elif change in ("interrupted", "publication_failed", "future"):
        summary = json.loads((run/"clock/summary.json").read_text())
        if change == "future":
            summary["updated_at"] = datetime.fromtimestamp(1006, UTC).isoformat()
        else:
            summary["status"] = change
        write_status(run/"clock/summary.json", summary)
        write_status(run/"clock/status.json", summary)
    elif change == "stop_disagrees":
        write_status(run/"stop", dict(declaration="driver_left", at=1001, elapsed=11))
    elif change == "wrong_visit":
        status = run/"status.json"
        write_status(status, dict(json.loads(status.read_text()), clock=dict(visit_id="a" * 32)))
    result = record(run, output)["review"]
    assert result["review_latency_utc_s"] is None
    assert result["declared_departure_at"] is None


def test_departure_change_withholds_latency_without_discarding_review(tmp_path, monkeypatch):
    run, output = make_visit(tmp_path)
    original = visit_review._read_bound
    reads = 0

    def changing(path, root, **kwargs):
        nonlocal reads
        if path == run / "stop":
            reads += 1
            if reads == 2:
                write_status(path, dict(declaration="driver_left", at=1004, elapsed=14))
        return original(path, root, **kwargs)

    monkeypatch.setattr(visit_review, "_read_bound", changing)
    review = record(run, output)["review"]
    assert reads == 2
    assert len(review["departure_sources"]) == 3
    assert review["review_latency_utc_s"] is None


@pytest.mark.parametrize("folder", ["outcomes", "coverage", "output", "worker", "clock",
                                    "worker-0002", "worker-0003"])
def test_allowed_owned_output_roots(tmp_path, folder):
    run, _ = make_visit(tmp_path)
    (run/folder).mkdir(exist_ok=True)
    target = run/folder/"synthetic.txt"
    target.write_text("Synthetic output")
    assert record(run, target)["review"]["output"]["path"] == str(target.resolve())


@pytest.mark.parametrize("folder", ["", "controls", "reviews", "worker-other", "capture-attempts"])
def test_control_and_review_files_are_not_review_outputs(tmp_path, folder):
    run, _ = make_visit(tmp_path)
    (run/folder).mkdir(exist_ok=True)
    target = run/folder/"synthetic.txt"
    target.write_text("Not an output")
    with pytest.raises(ValueError, match="not_a_visit_output"):
        record(run, target)
    assert len(list((run/"reviews").glob("*/failure.json"))) == 1


def test_outside_resolved_output_is_never_read(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)
    outside = tmp_path/"private.txt"
    outside.write_text("Unrelated")
    original = visit_review._path

    def redirected(path):
        return outside if Path(path) == target else original(path)

    monkeypatch.setattr(visit_review, "_path", redirected)
    with pytest.raises(ValueError, match="outside_owned_visit"):
        record(run, target)
    assert outside.read_text() == "Unrelated"


def test_source_change_before_publication_refuses_review(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)
    original = visit_review._read_bound
    changed = False

    def changing(path, root, **kwargs):
        nonlocal changed
        result = original(path, root, **kwargs)
        if Path(path) == target and not changed:
            changed = True
            target.write_text("Changed synthetic output")
        return result

    monkeypatch.setattr(visit_review, "_read_bound", changing)
    with pytest.raises(ValueError, match="source_changed_during_review"):
        record(run, target)
    assert not list((run/"reviews").glob("*/review.json"))
    assert list((run/"reviews").glob("*/failure.json"))


@pytest.mark.parametrize("change", ["heartbeat", "clock", "mode", "pid", "host", "legacy"])
def test_live_review_binds_visit_identity_across_status_updates(tmp_path, monkeypatch, change):
    run, target = make_visit(tmp_path, ended=False)
    status_file = run / "status.json"
    host_file = run / "host-process.json"
    clock = dict(visit_id="b" * 32, presence="declared_present", elapsed_s=2)
    host = dict(schema_version=1, process=dict(pid=42, created_100ns="1234"))
    if change != "legacy":
        write_status(host_file, host)
    status = dict(schema_version=1, mode="live", status="running", pid=42, clock=clock,
                  updated_at=1002)
    write_status(status_file, status)
    initial = status_file.read_bytes()
    original = visit_review._read_bound
    changed = False

    def changing(path, root, **kwargs):
        nonlocal changed
        bound = original(path, root, **kwargs)
        if path == target and not changed:
            changed = True
            next_status = dict(status, updated_at=1003, clock=dict(clock, elapsed_s=3))
            if change == "clock":
                next_status["clock"]["visit_id"] = "c" * 32
            elif change == "mode":
                next_status["mode"] = "replay"
            elif change == "pid":
                next_status["pid"] = 43
            elif change == "host":
                write_status(host_file, dict(host, process=dict(pid=42, created_100ns="4321")))
            write_status(status_file, next_status)
        return bound

    monkeypatch.setattr(visit_review, "_read_bound", changing)
    if change == "heartbeat":
        review = record(run, target)["review"]
        assert review["visit_source"]["sha256"] == hashlib.sha256(initial).hexdigest()
        assert review["visit_source_before_publication"]["sha256"] == hashlib.sha256(
            status_file.read_bytes()).hexdigest()
        assert review["visit_identity"]["visit_id"] == clock["visit_id"]
        assert review["visit_identity"]["host_source"]["sha256"] == hashlib.sha256(
            host_file.read_bytes()).hexdigest()
        assert review["review_latency_utc_s"] is None
    else:
        with pytest.raises(ValueError, match="source_changed_during_review"):
            record(run, target)
        assert list((run / "reviews").glob("*/failure.json"))


def test_partial_publication_failure_is_retained(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)
    operation = "rename" if visit_review.os.name == "nt" else "link"
    original = getattr(visit_review.os, operation)

    def fail_review(source, destination):
        if Path(destination).name == "review.json":
            raise OSError("synthetic publication failure")
        original(source, destination)

    monkeypatch.setattr(visit_review.os, operation, fail_review)
    with pytest.raises(OSError, match="publication"):
        record(run, target)
    assert list((run/"reviews").glob("*/review.partial"))
    assert list((run/"reviews").glob("*/failure.json"))
    assert not list((run/"reviews").glob("*/review.json"))


@pytest.mark.parametrize("field,value", [("note", " "), ("note", "x"*4001),
                                         ("reviewer", "invented"), ("verdict", "good"),
                                         ("driver_feedback_confirmed", 1)])
def test_invalid_requests_are_retained_without_accepted_review(tmp_path, field, value):
    run, target = make_visit(tmp_path)
    fields = dict(output=target, verdict="failed", note="Synthetic note.", clock=lambda: 1005)
    fields[field] = value
    with pytest.raises(ValueError):
        visit_review.record_review(run, **fields)
    assert list((run/"reviews").glob("*/failure.json"))
    assert not list((run/"reviews").glob("*/review.json"))


@pytest.mark.parametrize("at", [-1, True, float("nan"), float("inf")])
def test_invalid_local_review_clock_preserves_attempt(tmp_path, at):
    run, target = make_visit(tmp_path)
    with pytest.raises(ValueError, match="invalid_review_clock"):
        visit_review.record_review(run, output=target, verdict="failed", note="Synthetic.",
                                   clock=lambda: at)
    assert list((run/"reviews").glob("*/attempt.json"))
    assert list((run/"reviews").glob("*/failure.json"))


def test_atomic_publisher_cannot_overwrite_an_existing_result(tmp_path):
    path = tmp_path/"review.json"
    visit_review._publish(path, {"value": "first"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        visit_review._publish(path, {"value": "second"})
    assert path.read_bytes() == before
    assert path.with_suffix(".partial").exists()


@pytest.mark.skipif(visit_review.os.name == "nt", reason="POSIX link cleanup only")
def test_post_publication_cleanup_failure_does_not_contradict_acceptance(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)
    original = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path.name == "review.partial":
            raise PermissionError("synthetic cleanup contention")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    result = record(run, target)
    assert Path(result["path"]).exists()
    assert list((run/"reviews").glob("*/review.partial"))
    assert not list((run/"reviews").glob("*/failure.json"))


@pytest.mark.skipif(visit_review.os.name != "nt", reason="Windows no-overwrite rename")
def test_windows_publication_never_requires_hard_links(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Hard links must not be required on Windows")

    monkeypatch.setattr(visit_review.os, "link", forbidden)
    result = record(run, target)
    assert Path(result["path"]).exists()
    assert not list((run/"reviews").glob("*/review.partial"))


def test_change_during_open_handle_read_is_rejected(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)
    original = visit_review.os.fstat
    identity = target.stat().st_ino
    reads = 0

    def changed(handle):
        nonlocal reads
        result = original(handle)
        if result.st_ino == identity:
            reads += 1
            if reads == 2:
                target.write_text("Changed during read; synthetic")
        return result

    monkeypatch.setattr(visit_review.os, "fstat", changed)
    with pytest.raises(ValueError, match="source_changed_during_read"):
        record(run, target)
    assert list((run/"reviews").glob("*/failure.json"))


def test_redirected_review_directory_is_refused_before_external_writes(tmp_path, monkeypatch):
    run, target = make_visit(tmp_path)
    outside = tmp_path/"outside"
    outside.mkdir()
    original = visit_review._path

    def redirected(path):
        return outside if Path(path) == run/"reviews" else original(path)

    monkeypatch.setattr(visit_review, "_path", redirected)
    with pytest.raises(ValueError, match="outside_owned_visit"):
        record(run, target)
    assert not list(outside.iterdir())
