"""Offline status outages must not stop independent raw writes."""

import gzip
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli import telemetry_watch
from simcoach.live.archive_status import ArchiveStatusPublisher
from tests.test_telemetry_watch import Clock, Source, sample


def records(run):
    result = []
    manifests = [json.loads(row) for row in (run / "segments.jsonl").read_text().splitlines()]
    for item in manifests:
        path = run / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        with gzip.open(path, "rt") as stream:
            result.extend(json.loads(row) for row in stream)
    return result, manifests


def test_blocked_status_writer_cannot_hold_raw_rotation_or_confirm_terminal(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def blocked(path, value, **kwargs):
        entered.set()
        assert release.wait(5), "test failed to release blocked status I/O"
        telemetry_watch.write_status(path, value, **kwargs)

    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=blocked)
    values = [sample(i * 2) for i in range(1, 21)]
    clock = Clock(tmp_path, len(values))

    class ObservedSource(Source):
        def read(self):
            assert entered.wait(2)
            return super().read()

    try:
        code = telemetry_watch.watch(
            tmp_path, ObservedSource(values), SharedMemory, hz=2, segment_seconds=1,
            monotonic=clock.read, sleep=clock.sleep, status_publisher=publisher,
            publication_timeout_s=.02,
        )
        assert code == 1  # Raw closure succeeded; terminal status remains unconfirmed.
        raw, manifest = records(tmp_path)
        assert len(raw) == len(values) and len(manifest) == 10
        assert not (tmp_path / "status.json").exists()
        state = publisher.snapshot()
        assert state["attempts"] == 1 and state["superseded_snapshots"] > 0
        assert not state["finished"] and state["confirmed_sequence"] == 0
        final = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
        assert final["capture_status"] == "complete" and not final["terminal_confirmed"]
    finally:
        release.set()
        assert publisher.close(timeout_s=2)
    # Releasing an embedded test's blocked thread later must not rewrite the
    # already-recorded timeout/exit result. A real CLI process exits instead.
    assert json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1]) == final
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "complete"


def test_persistent_initial_publication_errors_preserve_complete_raw_and_failed_temps(tmp_path):
    entered = threading.Event()

    def fail(path, value, *, temporary):
        temporary.write_text(json.dumps(value))
        entered.set()
        raise PermissionError("synthetic sustained status denial")

    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=fail,
                                       retry_min_s=.01, retry_max_s=.02)
    clock = Clock(tmp_path, 4)

    class WaitingSource(Source):
        def read(self):
            assert entered.wait(2)
            return super().read()

    code = telemetry_watch.watch(
        tmp_path, WaitingSource([sample(2), sample(4), sample(6), sample(8)]), SharedMemory,
        hz=2, segment_seconds=1, monotonic=clock.read, sleep=clock.sleep,
        status_publisher=publisher,
    )
    assert code == 1 and not (tmp_path / "status.json").exists()
    assert len(records(tmp_path)[0]) == 4
    failed = list(tmp_path.glob("status-publication-*.tmp"))
    assert len(failed) >= 2 and len(failed) == publisher.snapshot()["failures"]
    assert all(json.loads(path.read_text())["status"] in ("running", "complete") for path in failed)
    events = [json.loads(row) for row in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(row["event"] == "status_publication_failure" for row in events)
    assert events[-1]["capture_status"] == "complete"
    assert events[-1]["diagnostic"]["failures"] == len(failed)


def test_new_observations_do_not_reset_backoff_and_recovery_keeps_original_timestamp(tmp_path):
    failed, recovered = threading.Event(), threading.Event()
    attempts = []

    def writer(path, value, *, temporary):
        attempts.append(value)
        if len(attempts) == 1:
            temporary.write_text(json.dumps(value))
            failed.set()
            raise OSError("synthetic outage")
        telemetry_watch.write_status(path, value, temporary=temporary)
        recovered.set()

    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=writer,
                                       retry_min_s=.3, retry_max_s=.3)
    original = dict(updated_at="2026-01-01T00:00:00+00:00", samples=1)
    publisher.submit(original)
    assert failed.wait(2)
    for value in range(2, 15):
        publisher.submit(dict(updated_at="2026-01-01T00:00:01+00:00", samples=value))
    assert not recovered.wait(.05)
    assert recovered.wait(2)
    saved = json.loads((tmp_path / "status.json").read_text())
    assert saved["updated_at"] == "2026-01-01T00:00:01+00:00"
    assert saved["samples"] == 14 and len(attempts) == 2
    assert saved["status_publication"]["prior_state"]["failures"] == 1
    assert json.loads(next(tmp_path.glob("status-publication-*.tmp")).read_text())["samples"] == 1
    assert publisher.close(timeout_s=2)


def test_old_inflight_success_is_not_terminal_acknowledgment(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def writer(path, value, **kwargs):
        if value["status"] == "running":
            entered.set()
            assert release.wait(2)
            telemetry_watch.write_status(path, value, **kwargs)
        else:
            raise OSError("terminal status failed")

    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=writer)
    publisher.submit(dict(status="running"))
    assert entered.wait(2)
    terminal = publisher.submit(dict(status="complete"))
    release.set()
    assert not publisher.close(timeout_s=2)
    assert publisher.snapshot()["confirmed_sequence"] < terminal
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "running"


def test_unexpected_publisher_exception_is_observable_and_cannot_confirm_shutdown(tmp_path):
    def bug(*args, **kwargs):
        raise ValueError("synthetic programmer error")

    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=bug)
    publisher.submit({"status": "complete"})
    assert not publisher.close(timeout_s=2)
    state = publisher.snapshot()
    assert state["finished"] and state["fatal"] and state["failures"] == 1
    assert state["last_failure"]["error_type"] == "ValueError"


def test_observation_timestamp_precedes_slow_archive_flush(tmp_path, monkeypatch):
    times = [0]
    original_flush = telemetry_watch.Segments.flush
    published = []
    initial = threading.Event()

    def flush(self):
        times[0] += 10  # Only the test's wall-time stamp advances during disk work.
        original_flush(self)

    def stamp():
        return f"observed-{times[0]}"

    def writer(path, value, **kwargs):
        published.append((value, times[0]))
        telemetry_watch.write_status(path, value, **kwargs)
        initial.set()

    class WaitingSource(Source):
        def read(self):
            assert initial.wait(2)
            return super().read()

    monkeypatch.setattr(telemetry_watch.Segments, "flush", flush)
    monkeypatch.setattr(telemetry_watch, "utc_now", stamp)
    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=writer)
    clock = Clock(tmp_path, 4)
    assert telemetry_watch.watch(
        tmp_path, WaitingSource([sample(2), sample(4), sample(6), sample(8)]), SharedMemory,
        hz=2, segment_seconds=1, monotonic=clock.read, sleep=clock.sleep,
        status_publisher=publisher,
    ) == 0
    assert all(int(value["updated_at"].split("-")[1]) <= at for value, at in published)
    assert published[0][0]["updated_at"] == "observed-0" and published[0][1] >= 10


def test_stop_accepts_recognized_run_without_initial_status(tmp_path, monkeypatch):
    (tmp_path / "run.json").write_text(json.dumps(dict(
        schema_version=1, shared_memory_name="$pcars2$", expected_version=14)))
    monkeypatch.setattr(telemetry_watch.sys, "argv", ["watch", "--stop", str(tmp_path)])
    assert telemetry_watch.main() == 0
    assert (tmp_path / "stop").exists() and not (tmp_path / "status.json").exists()


def test_optional_temporary_never_overwrites_a_failed_attempt(tmp_path):
    path = tmp_path / "status.json"
    temporary = tmp_path / "failed.tmp"
    temporary.write_text("keep failed evidence")
    with pytest.raises(FileExistsError):
        telemetry_watch.write_status(path, {}, temporary=temporary)
    assert temporary.read_text() == "keep failed evidence" and not path.exists()


def test_source_close_failure_still_closes_publisher_and_preserves_partial(tmp_path):
    class CloseFailure(Source):
        def close(self):
            raise OSError("synthetic source close failure")

    publisher = ArchiveStatusPublisher(tmp_path / "status.json")
    clock = Clock(tmp_path, 1)
    assert telemetry_watch.watch(
        tmp_path, CloseFailure([sample(2)]), SharedMemory, hz=2,
        monotonic=clock.read, sleep=clock.sleep, status_publisher=publisher,
    ) == 1
    assert publisher.snapshot()["finished"]
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["status"] == "failed"
    assert state["cleanup_errors"] == [{"phase": "source_close", "error_type": "OSError"}]
    assert list(tmp_path.glob("samples-*.partial"))
    assert not (tmp_path / "segments.jsonl").exists()


def test_failed_raw_finalization_cannot_publish_complete_terminal_state(tmp_path, monkeypatch):
    original = telemetry_watch.Segments.finish
    writers = []

    def fail(self, *, complete):
        writers.append(self)
        raise OSError("synthetic raw finalization failure")

    monkeypatch.setattr(telemetry_watch.Segments, "finish", fail)
    clock = Clock(tmp_path, 1)
    try:
        assert telemetry_watch.watch(
            tmp_path, Source([sample(2)]), SharedMemory, hz=2,
            monotonic=clock.read, sleep=clock.sleep,
        ) == 1
        state = json.loads((tmp_path / "status.json").read_text())
        assert state["status"] == "failed" and "raw finalization" in state["error"]
        terminal = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
        assert terminal["capture_status"] == "failed"
        assert terminal["terminal_confirmed"] is True  # Exact failed snapshot, not success.
        assert terminal["terminal_sequence"] == state["status_publication"]["observation_sequence"]
        assert not (tmp_path / "segments.jsonl").exists()
    finally:
        for writer in writers:
            original(writer, complete=False)  # Close the synthetic open descriptor only.


def test_failed_terminal_submission_cannot_acknowledge_last_running_snapshot(tmp_path):
    initial = threading.Event()

    def writer(path, value, **kwargs):
        telemetry_watch.write_status(path, value, **kwargs)
        initial.set()

    publisher = ArchiveStatusPublisher(tmp_path / "status.json", writer=writer)
    submit = publisher.submit

    def reject_terminal(value):
        if value["status"] == "complete":
            raise ValueError("synthetic terminal submit failure")
        return submit(value)

    class WaitingSource(Source):
        def read(self):
            assert initial.wait(2)
            return super().read()

    publisher.submit = reject_terminal
    clock = Clock(tmp_path, 1)
    assert telemetry_watch.watch(
        tmp_path, WaitingSource([sample(2)]), SharedMemory, hz=2,
        monotonic=clock.read, sleep=clock.sleep, status_publisher=publisher,
    ) == 1
    terminal = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert terminal["terminal_sequence"] is None and not terminal["terminal_confirmed"]
    assert terminal["capture_status"] == "failed"
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "running"


def test_failed_stopped_event_cannot_make_terminal_telemetry_current(tmp_path, monkeypatch):
    original = Path.open

    def open_file(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        if path == tmp_path / "events.jsonl" and args and args[0] == "x":
            def write(text):
                row = json.loads(text)
                if row.get("event") == "coverage" and row.get("current") == "stopped":
                    raise OSError("synthetic stopped-event write failure")
                return handle.write(text)
            return SimpleNamespace(write=write, flush=handle.flush, fileno=handle.fileno,
                                   close=handle.close)
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    clock = Clock(tmp_path, 1)
    assert telemetry_watch.watch(
        tmp_path, Source([sample(2)]), SharedMemory, hz=2,
        monotonic=clock.read, sleep=clock.sleep,
    ) == 1
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["status"] == "failed"
    assert state["coverage"] == "available"  # Last successfully journaled transition.
    assert state["latest_is_current"] is False
    assert state["cleanup_errors"] == [{"phase": "event_finish", "error_type": "OSError"}]
    assert len(records(tmp_path)[0]) == 1
