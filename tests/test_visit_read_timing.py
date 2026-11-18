"""Post-I/O evidence time and honest maintenance interruption, without live I/O."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcoach.cli import visit


def test_worker_status_transient_denial_preserves_fresh_readiness(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    value = dict(schema_version=1, status="recording", intake_active=True,
                 active_ticks=30, updated_at=100.02, tick_age_s=.01)
    path.write_text(json.dumps(value))
    original = Path.read_text
    attempts = []

    def read(current, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError(13, "synthetic sharing denial")
        return original(current, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    diagnostics = []
    observed = visit.read_json(path, diagnostic=diagnostics.append)
    assert observed == value and diagnostics[0]["recovered"]
    assert visit.recorder_ready(observed, now=100.03, process_alive=True)


def test_archive_observation_uses_post_read_time_and_preserves_diagnostics(monkeypatch):
    elapsed = [10.0]
    events = []

    class Source:
        def __init__(self, archive):
            pass

        def __call__(self):
            elapsed[0] += .02
            self.diagnostic({"event": "file_read", "recovered": True})
            return "identity", {"updated_at": elapsed[0]}

    monkeypatch.setattr(visit, "ArchiveStatusSource", Source)
    monkeypatch.setattr(visit, "time", SimpleNamespace(
        time=lambda: elapsed[0], perf_counter=lambda: elapsed[0] + 100))
    identity, sample, now, monotonic = visit.archive_observation(None, events.append)
    assert identity == "identity" and sample["updated_at"] == now == 10.02
    assert monotonic == pytest.approx(110.02)
    assert events == [{"event": "file_read", "recovered": True}]


def test_archive_read_failure_retains_error_without_admitting_cached_data(monkeypatch):
    def source(_):
        def read():
            raise PermissionError(13, "synthetic denial")
        return read

    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    events = []
    identity, status, _, _ = visit.archive_observation(None, events.append)
    assert identity is None and status is None
    assert events[0]["error_type"] == "PermissionError" and events[0]["errno"] == 13


def test_maintenance_marker_never_declares_driver_absent(tmp_path, monkeypatch):
    real_write = visit.write_status

    def write(path, value):
        real_write(path, value)
        if path.name == "status.json" and value.get("status") == "running":
            real_write(path.parent / "stop", {"declaration": "maintenance_restart"})

    monkeypatch.setattr(visit, "write_status", write)
    assert visit.run_visit(tmp_path / "visits", intent="synthetic maintenance", present=True,
                           max_seconds=2) == 1
    run = next(p for p in (tmp_path / "visits").iterdir() if p.is_dir())
    state = json.loads((run / "status.json").read_text())
    assert state["status"] == "incomplete"
    assert state["clock"]["status"] == "interrupted"
    assert state["clock"]["presence"] == "unknown"
    assert state["declared_presence"] is None
    assert state["presence"] == "unknown" and state["arrival_confirmed"] is True
    assert state["problem"] == "clock_close_ValueError"


def test_departure_during_source_read_ends_at_declaration(tmp_path, monkeypatch):
    import time

    root = tmp_path / "visits"
    calls = []

    def source(_):
        def read():
            calls.append(1)
            if len(calls) == 2:
                run = next(root.glob("*/status.json")).parent
                visit.request(run, "stop")
                time.sleep(.02)  # The post-read observation is after the departure boundary.
            return None, None
        return read

    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    assert visit.run_visit(root, intent="synthetic departure race", present=True,
                           archive=tmp_path / "unused", max_seconds=2) == 1  # No recording.
    run = next(root.glob("*/status.json")).parent
    state = json.loads((run / "status.json").read_text())
    boundary = json.loads((run / "stop").read_text())
    assert state["clock"]["presence"] == "declared_absent"
    assert state["declared_presence"] is False
    assert state["presence"] == "declared_absent" and state["arrival_confirmed"] is True
    assert state["clock"]["status"] == "complete"
    assert state["problem"] is None
    assert state["clock"]["updated_at"] == visit.datetime.fromtimestamp(
        boundary["at"], visit.UTC).isoformat()


def test_departure_publication_lock_prevents_clock_advance_before_stop_is_visible(tmp_path):
    calls = []
    clock = SimpleNamespace(update=lambda *args, **kwargs: calls.append(kwargs))
    lock = visit.SourceLock(tmp_path / "request.lock")
    try:
        assert not visit.update_presence(tmp_path, clock, "unknown", now=100, elapsed=100,
                                         source_evidence=None)
        assert not calls
        # The other writer has chosen a timestamp but has not published its stop yet.
        visit.write_status(tmp_path / "stop", dict(at=99, elapsed=99, declaration="driver_left"))
    finally:
        lock.close()
    assert not visit.update_presence(tmp_path, clock, "unknown", now=100, elapsed=100,
                                     source_evidence=None)
    assert not calls


@pytest.mark.parametrize("departure", [False, True])
def test_terminal_presence_matches_clock_through_timeout_or_failed_publication(
        tmp_path, monkeypatch, departure):
    from simcoach.live import visit_clock

    real_write, clock_write = visit.write_status, visit_clock.write_status
    observed = []

    def write(path, value):
        real_write(path, value)
        if path.name == "status.json" and "arrival_confirmed" in value:
            observed.append(dict(value))
            if departure and value["status"] == "running":
                visit.request(path.parent, "stop")

    def fail_summary(path, value):
        if departure and path.name == "summary.json":
            raise OSError("synthetic terminal clock publication failure")
        clock_write(path, value)

    monkeypatch.setattr(visit, "write_status", write)
    monkeypatch.setattr(visit_clock, "write_status", fail_summary)
    assert visit.run_visit(tmp_path / "visits", intent="synthetic presence closure",
                           present=True, max_seconds=.3) == 1
    assert observed[-1]["status"] == "incomplete"
    assert any(row["status"] == "closing" for row in observed)
    for row in observed:
        assert row["arrival_confirmed"] is True
        assert row["presence"] == row["clock"]["presence"]
        assert row["declared_presence"] is {
            "declared_present": True, "declared_absent": False, "unknown": None
        }[row["presence"]]
    last = observed[-1]
    if departure:
        assert last["declared_presence"] is False  # Intent committed, not successful publication.
        assert last["clock"]["status"] == "publication_failed"
        assert last["clock"]["publication_complete"] is False
        assert last["problem"] == "clock_close_OSError"
    else:
        assert last["declared_presence"] is None
        assert last["clock"]["status"] == "interrupted"
        assert last["problem"] == "visit_time_limit"
