import json
import time

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live import visit_worker
from simcoach.live.worker_status import WorkerStatusPublisher
from tests.test_visit import fixture_file


@pytest.mark.parametrize("persistent", [False, True])
def test_status_access_failure_keeps_capture_and_closure(tmp_path, monkeypatch, persistent):
    fixture = fixture_file(tmp_path)
    run = tmp_path / "worker"
    run.mkdir()
    write_status(tmp_path / "heartbeat.json", dict(schema_version=1, at=time.time()))
    failures = []

    def writer(path, value, **kwargs):
        if path == run / "status.json" and (persistent or not failures):
            # Preserve evidence from a flushed temporary whose replace failed.
            kwargs["temporary"].write_text(json.dumps(value))
            failures.append(kwargs["temporary"])
            error = PermissionError(13, "injected sharing failure")
            error.winerror = 5
            raise error
        write_status(path, value, **kwargs)

    monkeypatch.setattr(visit_worker, "write_status", writer)
    code = visit_worker.work(run, tmp_path, fixture=fixture)
    assert code == (1 if persistent else 0)
    closure = json.loads((run / "closure.json").read_text())
    assert closure["recordings"][0]["validation"] == "PASS"
    assert closure["recordings"][0]["analysis"] == "PASS"
    assert all(path.exists() for path in failures)
    journal = [json.loads(line) for line in
               (run / "status-publication-errors.jsonl").read_text().splitlines()]
    assert len(journal) == len(failures)
    if persistent:
        assert not (run / "status.json").exists()
    else:
        state = json.loads((run / "status.json").read_text())
        assert state["status"] == "complete"
        assert state["publication_diagnostic"]["failures"] == 1


def test_failed_observation_never_refreshes_old_status(tmp_path):
    write_status(tmp_path / "status.json", {"updated_at": 1})

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    publisher = WorkerStatusPublisher(tmp_path, fail)
    assert not publisher.publish({"updated_at": 2, "status": "recording"})
    assert json.loads((tmp_path / "status.json").read_text())["updated_at"] == 1
    publisher.writer = write_status
    assert publisher.publish({"updated_at": 3, "status": "recording"})
    result = json.loads((tmp_path / "status.json").read_text())
    assert result["updated_at"] == 3
    assert result["publication_diagnostic"]["last_failure"]["observation_at"] == 2


def test_programming_error_is_not_silenced(tmp_path):
    def fail(*args, **kwargs):
        raise ValueError("invalid observation")

    with pytest.raises(ValueError):
        WorkerStatusPublisher(tmp_path, fail).publish({"updated_at": 1, "status": "recording"})
