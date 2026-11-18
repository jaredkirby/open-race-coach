import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from simcoach.live.visit_clock import VisitClock

START = datetime(2026, 1, 1, tzinfo=UTC)


def stamp(seconds):
    return dict(at=START+timedelta(seconds=seconds), elapsed=100+seconds)


def start(tmp_path, name="visit"):
    return VisitClock(tmp_path/name, presence_confirmed=True, **stamp(0))


def events(run):
    with sqlite3.connect(run/"events.sqlite3") as connection:
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM events ORDER BY sequence")]


def test_requires_explicit_presence_and_new_directory(tmp_path):
    for value in (False, None, 1, "true"):
        with pytest.raises(ValueError, match="explicit_presence"):
            VisitClock(tmp_path/"visit", presence_confirmed=value, **stamp(0))
    assert not (tmp_path/"visit").exists()
    visit = start(tmp_path)
    with pytest.raises(FileExistsError):
        start(tmp_path)
    visit.close()


def test_activity_partition_and_prospective_rest_reasons(tmp_path):
    visit = start(tmp_path)
    visit.update("driving", **stamp(.2))
    visit.update("driving", **stamp(.4))
    visit.update("pit_moving", **stamp(.6))
    visit.update("pit_moving", **stamp(.8))
    visit.update("not_driving", **stamp(1))
    visit.update("not_driving", **stamp(1.2))
    before = list(events(visit.run))
    visit.annotate("intentional_rest", activity="not_driving", **stamp(1.4))
    visit.update("not_driving", **stamp(1.6))
    summary = visit.end(**stamp(1.8))
    assert sum(summary["activity_s"].values()) == pytest.approx(1.8)
    assert summary["activity_s"] == pytest.approx(dict(
        driving=.2, pit_moving=.2, transition=.4, not_driving=.6, unknown=.4))
    assert summary["not_driving_reason_s"]["unspecified"] == pytest.approx(.4)
    assert summary["not_driving_reason_s"]["intentional_rest"] == pytest.approx(.2)
    assert sum(summary["not_driving_reason_s"].values()) == pytest.approx(.6)
    assert events(visit.run)[:len(before)] == before
    assert summary["presence"] == "declared_absent"


def test_gap_is_unknown_not_assistant_wait(tmp_path):
    visit = start(tmp_path)
    visit.annotate("assistant_wait", activity="not_driving", **stamp(0))
    visit.update("not_driving", **stamp(.5))
    visit.update("not_driving", **stamp(5))
    summary = visit.end(**stamp(5.5))
    assert summary["activity_s"]["unknown"] == pytest.approx(5)
    assert summary["not_driving_reason_s"]["assistant_wait"] == pytest.approx(.5)
    assert any(event.get("gap") for event in events(visit.run))


def test_wall_clock_jump_counts_monotonic_duration_as_unknown(tmp_path):
    visit = start(tmp_path)
    visit.update("driving", **stamp(.2))
    visit.update("driving", at=START-timedelta(hours=1), elapsed=100.4)
    summary = visit.close()
    assert summary["activity_s"]["unknown"] == pytest.approx(.4)
    assert summary["elapsed_s"] == pytest.approx(.4)
    assert summary["presence"] == "unknown"
    assert any(event.get("clock_changed") for event in events(visit.run))


def test_monotonic_reset_cannot_rewrite_time_and_restart_needs_new_visit(tmp_path):
    visit = start(tmp_path)
    visit.update("driving", **stamp(.5))
    before = events(visit.run)
    with pytest.raises(ValueError, match="elapsed_regressed"):
        visit.update("driving", at=START+timedelta(seconds=1), elapsed=1)
    assert events(visit.run) == before
    summary = visit.close()
    assert summary["status"] == "interrupted"
    assert summary["elapsed_s"] == pytest.approx(.5)
    with pytest.raises(FileExistsError):
        start(tmp_path)
    new_visit = VisitClock(tmp_path/"new", presence_confirmed=True,
                           at=START+timedelta(hours=1), elapsed=1)
    assert new_visit.snapshot()["elapsed_s"] == 0
    assert new_visit.visit_id != visit.visit_id
    new_visit.close()


def test_departure_stops_counting_and_summary_is_not_rewritten(tmp_path):
    visit = start(tmp_path)
    visit.end(**stamp(.5))
    saved = (visit.run/"summary.json").read_bytes()
    with pytest.raises(RuntimeError, match="visit_not_running"):
        visit.update("driving", **stamp(100))
    with pytest.raises(RuntimeError, match="visit_not_running"):
        visit.end(**stamp(100))
    visit.close()
    assert (visit.run/"summary.json").read_bytes() == saved
    assert json.loads(saved)["elapsed_s"] == pytest.approx(.5)


@pytest.mark.parametrize("reason", ["setup", "other_wait", "assistant_wait"])
def test_reason_does_not_turn_driving_into_waiting(tmp_path, reason):
    visit = start(tmp_path)
    visit.annotate(reason, activity="driving", **stamp(0))
    visit.update("driving", **stamp(.5))
    summary = visit.end(**stamp(.5))
    assert summary["activity_s"]["driving"] == pytest.approx(.5)
    assert sum(summary["not_driving_reason_s"].values()) == 0


def test_invalid_inputs_do_not_append_or_relabel(tmp_path):
    visit = start(tmp_path)
    before = events(visit.run)
    with pytest.raises(ValueError, match="invalid_reason"):
        visit.annotate("wasted", **stamp(.5))
    with pytest.raises(ValueError, match="invalid_activity"):
        visit.update("present", **stamp(.5))
    with pytest.raises(ValueError, match="visit_requires_utc"):
        visit.update("driving", at=datetime(2026, 1, 1), elapsed=100.5)
    with pytest.raises(ValueError, match="invalid_elapsed"):
        visit.update("driving", at=START, elapsed=float("nan"))
    assert events(visit.run) == before
    visit.close()


def test_interrupted_summary_never_infers_absence_or_time_after_last_sample(tmp_path):
    visit = start(tmp_path)
    visit.update("not_driving", **stamp(.4))
    result = visit.close()
    assert result["status"] == "interrupted" and result["presence"] == "unknown"
    assert result["elapsed_s"] == pytest.approx(.4)
    assert json.loads((visit.run/"summary.json").read_text()) == result


def test_journal_failure_does_not_advance_in_memory(tmp_path):
    visit = start(tmp_path)
    visit.connection.close()
    before = visit.snapshot()
    with pytest.raises(sqlite3.ProgrammingError):
        visit.update("driving", **stamp(.5))
    assert visit.snapshot() == before
    assert not (visit.run/"summary.json").exists()


def test_failed_terminal_publish_preserves_journal_and_running_checkpoint(tmp_path, monkeypatch):
    from simcoach.cli import telemetry_watch

    visit = start(tmp_path)
    replace = telemetry_watch.os.replace

    def fail_terminal(source, destination):
        if destination.name == "summary.json":
            raise OSError("synthetic publication failure")
        return replace(source, destination)

    monkeypatch.setattr(telemetry_watch.os, "replace", fail_terminal)
    with pytest.raises(OSError, match="publication failure"):
        visit.end(**stamp(.5))
    assert (visit.run/"summary.tmp").is_file()
    assert not (visit.run/"summary.json").exists()
    assert json.loads((visit.run/"status.json").read_text())["status"] == "running"
    assert events(visit.run)[-1]["event"] == "ended"
    before = events(visit.run)
    summary = visit.close()
    assert summary["status"] == "publication_failed"
    assert summary["publication_complete"] is False
    assert summary["terminal_intent"] == "complete"
    assert summary["presence"] == "declared_absent"
    assert summary["elapsed_s"] == pytest.approx(.5)
    assert events(visit.run) == before
    assert not (visit.run/"summary.json").exists()
    with pytest.raises(RuntimeError, match="visit_not_running"):
        visit.update("driving", **stamp(1))


def test_terminal_checkpoint_failure_latches_failure_after_summary(tmp_path, monkeypatch):
    from simcoach.live import visit_clock

    visit = start(tmp_path)
    write = visit_clock.write_status

    def fail_checkpoint(path, value):
        if path.name == "status.json":
            raise OSError("synthetic terminal checkpoint failure")
        return write(path, value)

    monkeypatch.setattr(visit_clock, "write_status", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint failure"):
        visit.end(**stamp(.5))
    published_summary = (visit.run/"summary.json").read_bytes()
    before = events(visit.run)
    result = visit.close()
    assert result["status"] == "publication_failed"
    assert result["publication_complete"] is False
    assert result["publication_error"] == "OSError"
    assert result["elapsed_s"] == pytest.approx(.5)
    assert events(visit.run) == before
    assert (visit.run/"summary.json").read_bytes() == published_summary
    assert json.loads((visit.run/"status.json").read_text())["status"] == "running"


@pytest.mark.parametrize("reason", ["setup", "assistant_wait", "other_wait", "intentional_rest"])
def test_previous_stop_reason_does_not_follow_driver_into_next_stop(tmp_path, reason):
    visit = start(tmp_path)
    visit.annotate(reason, activity="not_driving", **stamp(0))
    visit.update("not_driving", **stamp(.5))
    original = events(visit.run)
    visit.update("driving", **stamp(1))  # Transition alone does not end a declaration.
    assert visit.snapshot()["current_reason"] == reason
    visit.update("driving", **stamp(1.5))
    assert visit.snapshot()["current_reason"] == "unspecified"
    visit.update("not_driving", **stamp(2))
    visit.update("not_driving", **stamp(2.5))
    visit.annotate("other_wait", activity="not_driving", **stamp(2.5))
    visit.update("not_driving", **stamp(3))
    summary = visit.end(**stamp(3))
    assert summary["reason_policy"] == "until_driving"
    assert summary["activity_s"]["not_driving"] == pytest.approx(1.5)
    assert summary["not_driving_reason_s"][reason] == pytest.approx(
        1 if reason == "other_wait" else .5)
    assert summary["not_driving_reason_s"]["unspecified"] == pytest.approx(.5)
    assert sum(summary["not_driving_reason_s"].values()) == pytest.approx(1.5)
    journal = events(visit.run)
    assert journal[:len(original)] == original
    resets = [e for e in journal if e.get("cause") == "driving_resumed"]
    assert len(resets) == 1
    assert resets[0]["offset_s"] == pytest.approx(1.5)
    assert resets[0]["reason"] == "unspecified"
    assert journal[journal.index(resets[0])-1]["activity"] == "driving"


@pytest.mark.parametrize("activity", ["unknown", "transition", "pit_moving", "not_driving"])
def test_non_driving_evidence_cannot_end_declared_rest(tmp_path, activity):
    visit = start(tmp_path)
    visit.annotate("intentional_rest", activity=activity, **stamp(0))
    visit.update(activity, **stamp(.5))
    visit.update(activity, **stamp(1))
    assert visit.snapshot()["current_reason"] == "intentional_rest"
    assert not any(e.get("cause") == "driving_resumed" for e in events(visit.run))
    visit.close()


def test_gap_clock_fault_and_zero_duration_do_not_clear_reason(tmp_path):
    visit = start(tmp_path)
    visit.annotate("setup", activity="driving", **stamp(0))
    visit.update("driving", **stamp(0))
    visit.update("driving", **stamp(4))
    visit.update("driving", at=START+timedelta(seconds=20), elapsed=104.5)
    assert visit.snapshot()["current_reason"] == "setup"
    assert not any(e.get("cause") == "driving_resumed" for e in events(visit.run))
    visit.update("driving", at=START+timedelta(seconds=20.5), elapsed=105)
    assert visit.snapshot()["current_reason"] == "unspecified"
    visit.close()


def test_explicit_reason_at_driving_endpoint_is_prospective(tmp_path):
    visit = start(tmp_path)
    visit.annotate("setup", activity="driving", **stamp(0))
    visit.annotate("other_wait", activity="driving", **stamp(.5))
    assert visit.snapshot()["current_reason"] == "other_wait"
    assert events(visit.run)[-1]["cause"] == "declared"
    visit.update("not_driving", **stamp(1))
    visit.update("not_driving", **stamp(1.5))
    assert visit.snapshot()["not_driving_reason_s"]["other_wait"] == pytest.approx(.5)
    visit.close()


def test_reason_reset_and_interval_share_one_journal_transaction(tmp_path):
    visit = start(tmp_path)
    visit.annotate("assistant_wait", activity="driving", **stamp(0))
    before, snapshot = events(visit.run), visit.snapshot()
    visit.connection.execute("""
        CREATE TRIGGER reject_reset BEFORE INSERT ON events
        WHEN instr(NEW.payload, 'driving_resumed') > 0
        BEGIN SELECT RAISE(ABORT, 'synthetic reason reset failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic reason reset failure"):
        visit.update("driving", **stamp(.5))
    assert events(visit.run) == before
    assert visit.snapshot() == snapshot
    visit.connection.execute("DROP TRIGGER reject_reset")
    visit.update("driving", **stamp(.5))
    assert visit.snapshot()["current_reason"] == "unspecified"
    assert visit.snapshot()["activity_s"]["driving"] == pytest.approx(.5)
    visit.close()
