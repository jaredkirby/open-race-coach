"""Archive-source continuity tests with simulated host time and no simulator process."""

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from simcoach.cli import visit
from simcoach.live.visit_clock import VisitClock
from tests.test_race_watch import BASE, status


def test_source_and_context_changes_do_not_bridge_driving_intervals(tmp_path, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(visit, "time", SimpleNamespace(
        time=lambda: BASE + elapsed[0], perf_counter=lambda: elapsed[0],
        sleep=lambda _: elapsed.__setitem__(0, elapsed[0] + .25)))

    def source(_directory):
        def read():
            step = round(elapsed[0] * 4)
            sample = status(elapsed[0], mSpeed=30, mSequenceNumber=2 * step + 2,
                            mCarName="changed car" if step >= 5 else "test car")
            sample["samples"] = step + 1
            return ("a" if step < 3 else "b") * 64, sample
        return read

    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    monkeypatch.setattr(visit.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("This test never admits or launches a recorder")))
    real_write = visit.write_status
    published = []

    def write(path, value):
        real_write(path, value)
        if path.name == "status.json" and value.get("status") == "running":
            published.append(value.copy())
            if elapsed[0] >= 2:
                visit.request(path.parent, "stop")

    monkeypatch.setattr(visit, "write_status", write)
    root = tmp_path / "synthetic-visits"
    assert visit.run_visit(root, intent="Synthetic source continuity test only", present=True,
                           archive=tmp_path / "fake-source", max_seconds=3) == 1
    run = next(path for path in root.iterdir() if path.is_dir())
    with sqlite3.connect(run / "clock/events.sqlite3") as db:
        intervals = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM events ORDER BY sequence")
            if json.loads(row[0])["event"] == "interval"]
    by_end = {row["end_offset_s"]: row for row in intervals if row["seconds"] > 0}
    assert by_end[.75]["activity"] == "unknown"
    assert by_end[1.0]["activity"] == "unknown"
    assert by_end[1.25]["activity"] == "unknown"
    assert by_end[1.5]["activity"] == "unknown"
    assert by_end[2.0]["activity"] == "driving"
    assert by_end[.75]["source_evidence"]["reason"] == "source_changed"
    assert by_end[1.25]["source_evidence"]["reason"] == "context_changed"
    assert by_end[2.0]["source_evidence"]["evidence"] == "advancing"
    assert all(row["worker_pid"] is None for row in published)
    assert all("activity_source" in row for row in published)


def test_held_evidence_is_saved_without_turning_it_into_new_source_progress(tmp_path):
    from simcoach.live.activity_evidence import ActivityEvidence

    observer = ActivityEvidence()
    clock = VisitClock(tmp_path / "clock", presence_confirmed=True,
                       at=datetime.fromtimestamp(BASE, UTC), elapsed=0)
    sample = None
    for second in (0, .25, .5, .75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3):
        if second in (0, 1):
            sample = status(second, mSpeed=0)
        evidence = observer.observe(sample, source_id="a" * 64, now=BASE + second,
                                    elapsed=second)
        clock.update(evidence["activity"], at=datetime.fromtimestamp(BASE + second, UTC),
                     elapsed=second, source_evidence=evidence)
        if second == 1.25:
            clock.annotate("intentional_rest", activity=evidence["activity"],
                           at=datetime.fromtimestamp(BASE + second, UTC), elapsed=second,
                           source_evidence=evidence)
        # A caller mutating its detached object cannot rewrite the persisted journal.
        evidence["activity"] = "driving"
    summary = clock.end(at=datetime.fromtimestamp(BASE + 3.25, UTC), elapsed=3.25)
    with sqlite3.connect(clock.run / "events.sqlite3") as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events")]
    held = [row["source_evidence"] for row in events
            if (row.get("source_evidence") or {}).get("evidence") == "held"]
    assert held and all(row["activity"] == "not_driving" for row in held)
    assert all(row["source_at"] == BASE + 1 for row in held)
    assert all(row["expires_at"] == BASE + 3 for row in held)
    assert summary["activity_s"]["driving"] == 0
    assert summary["not_driving_reason_s"]["intentional_rest"] > 0
    assert sum(summary["activity_s"].values()) == pytest.approx(3.25)
    assert sum(summary["not_driving_reason_s"].values()) == pytest.approx(
        summary["activity_s"]["not_driving"])
    last = [row for row in events if row.get("end_offset_s") == 3][0]
    assert last["activity"] == "unknown"


def test_mismatched_source_diagnostic_cannot_credit_driving(tmp_path):
    from simcoach.live.activity_evidence import ActivityEvidence

    evidence = ActivityEvidence().observe(None, source_id=None, now=BASE, elapsed=0)
    clock = VisitClock(tmp_path / "clock", presence_confirmed=True,
                       at=datetime.fromtimestamp(BASE, UTC), elapsed=0)
    with pytest.raises(ValueError, match="invalid_source_evidence"):
        clock.update("driving", at=datetime.fromtimestamp(BASE + .25, UTC), elapsed=.25,
                     source_evidence=evidence)
    assert clock.snapshot()["elapsed_s"] == 0
    clock.close()
