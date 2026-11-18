"""Host backend selection/failure isolation using synthetic workers and operations."""
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from simcoach.cli import visit
from simcoach.cli.telemetry_watch import write_status
from simcoach.live import recording_storage
from tests.test_fuel_radio import CONTEXT, status
from tests.test_operations_client import plan


@pytest.mark.parametrize("launch_failed", [False, True])
def test_service_backend_is_exclusive_and_failure_does_not_prevent_capture(
        tmp_path, monkeypatch, launch_failed):
    monkeypatch.setattr(recording_storage, "free_bytes", lambda _: recording_storage.MIN_FREE_BYTES)
    clients, workers = [], []
    class Client:
        def __init__(self, run, **kwargs):
            self.run = run
            self.forwarded = []
            self.process = None
            self.last = {}
            clients.append(self)
            if launch_failed:
                raise OSError("synthetic service launch failure")

        def poll(self):
            self.last = dict(health="healthy", service_id="synthetic", binding_valid=True,
                             state=dict(radio={"closed": False, "delivery_health": "disabled"},
                                        supervision={"supervision_health": "healthy",
                                                     "fuel_state": "unarmed"}))
            return self.last

        def forward_fuel(self, filename, request):
            self.forwarded.append(filename)

        def sync_receipts(self):
            for name in self.forwarded:
                write_status(self.run / "receipts" / name,
                             dict(accepted=True, authority="operations_service"))

        def terminal(self):
            return (self.run / "stop").exists()

        def close_streams(self):
            pass

    def forbidden(*args, **kwargs):
        raise AssertionError("Embedded operational owner must not be constructed")

    class Child:
        pid = 1234
        def __init__(self, directory):
            self.directory = directory

        def poll(self):
            if (self.directory.parent / "stop").exists():
                write_status(self.directory / "status.json", dict(
                    schema_version=1, status="complete", stop_reason="visit_stop_requested"))
                return 0
            return None

    def spawn(command, **kwargs):
        directory = Path(command[command.index("--run") + 1])
        directory.mkdir()
        workers.append(directory)
        write_status(directory / "status.json", dict(
            schema_version=1, status="recording", active_ticks=10, intake_active=True,
            recording_storage=recording_storage.RecordingStorage().poll(directory),
            updated_at=time.time(), tick_age_s=.01))
        write_status(directory / "closure.json", dict(schema_version=1, recordings=[dict(
            path=str(directory / "synthetic"), validation="PASS", analysis="PASS",
            summary={"synthetic": True}, analysis_path="synthetic-no-analysis")]))
        return Child(directory)

    counter = [0]
    source_started = time.perf_counter()
    def source(_):
        def read():
            counter[0] += 1
            value = status(counter[0], 8)
            value["updated_at"] = datetime.now(UTC).isoformat()
            # Poll count is not elapsed source time. Keep the fake source clock
            # coherent with its actual publication timestamp at the host cadence.
            value["offset_s"] = time.perf_counter() - source_started
            return "synthetic", value
        return read

    original_write = visit.write_status
    observations = []
    request_sent = [False]
    def publish(path, value):
        original_write(path, value)
        if (path.name == "status.json" and path.parent.parent == tmp_path / "visits"
                and value.get("status") == "running"):
            observations.append(value.copy())
            if value.get("capture_ready"):
                if launch_failed or clients[0].forwarded:
                    visit.request(path.parent, "stop")
                elif not request_sent[0]:
                    visit.request(path.parent, "fuel", cockpit_litres=8, verified_at=time.time(),
                                  source_id="synthetic", solo_player_confirmed=True, note="test")
                    request_sent[0] = True

    monkeypatch.setattr(visit, "OperationsClient", Client)
    monkeypatch.setattr(visit, "Supervisor", forbidden)
    monkeypatch.setattr(visit, "VisitAudio", forbidden)
    monkeypatch.setattr(visit.subprocess, "Popen", spawn)
    monkeypatch.setattr(visit, "ArchiveStatusSource", source)
    monkeypatch.setattr(visit, "write_status", publish)
    code = visit.run_visit(tmp_path / "visits", intent="synthetic service selection", present=True,
                           archive=tmp_path / "unused", plan=plan(), max_seconds=3,
                           separate_operations=True)
    assert code == (1 if launch_failed else 0)
    assert len(clients) == len(workers) == 1
    final = json.loads((workers[0].parent / "status.json").read_text())
    assert final["clock"]["presence"] == "declared_absent"
    assert any(v["capture_ready"] for v in observations)
    if launch_failed:
        assert final["problem"] == "operations_launch_unconfirmed"
        assert any(v["supervision"]["fuel_state"] == "unknown" for v in observations)
    else:
        assert clients[0].forwarded
        receipt = workers[0].parent / "receipts" / clients[0].forwarded[0]
        assert json.loads(receipt.read_text())["authority"] == "operations_service"


def test_cli_exposes_service_backend_without_changing_embedded_default(monkeypatch, tmp_path):
    captured = {}
    def run(root, **kwargs):
        captured.update(kwargs)
        return 0
    from dataclasses import asdict
    config = tmp_path / "plan.json"
    write_status(config, dict(schema_version=1, **asdict(plan())))
    monkeypatch.setattr(visit, "run_visit", run)
    assert visit.main(["start", "--present", "--intent", "synthetic", "--archive", str(tmp_path),
                       "--plan", str(config), "--operations-service"]) == 0
    assert captured["separate_operations"] is True and captured["plan"].context == CONTEXT

@pytest.mark.parametrize("generation", [None, 0, True])
def test_host_withholds_current_fuel_from_a_mismatched_generation(generation):
    observed = dict(source_id="archive")
    if generation is not None:
        observed["continuity_generation"] = generation
    service = dict(health="healthy", state=dict(observed_source=observed,
        supervision=dict(supervision_health="healthy", fuel_state="return_due",
                         approval=dict(accepted=True), notice={"kind": "fuel_return"},
                         plan={"synthetic": True})))
    archive_status = {"continuity_generation": 1}
    result = visit.operations_supervision(service, archive_status, source_id="archive")
    assert result["fuel_state"] == "unknown"
    assert result["reason"] == "operations_source_continuity_unverified"
    assert result["notice"] is None and result["approval"] is None
    assert result["plan"] == {"synthetic": True}
    assert service["state"]["supervision"]["fuel_state"] == "return_due"
    observed["continuity_generation"] = 1
    current = visit.operations_supervision(service, archive_status, source_id="archive")
    assert current["fuel_state"] == "return_due"
