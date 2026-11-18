import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path

import pytest

from simcoach.cli.dashboard import DashboardServer
from simcoach.cli.telemetry_watch import snapshot_summary, write_status
from simcoach.live.dashboard import DashboardFeed, project, read_source
from tests.test_activity_evidence import BASE, SOURCE, status

NONCE = "0123456789abcdef"


def sample(feed, second, value=None, source=SOURCE):
    feed.sample(status(second) if value is None else value,
                source_id=source, now=BASE + second, elapsed=second)
    return feed.response(NONCE, elapsed=second)


def ready():
    feed = DashboardFeed()
    assert sample(feed, 0)["state"] == "waiting"
    assert sample(feed, 1)["state"] == "fresh"
    return feed


def test_hold_never_refreshes_expiry_and_publisher_freeze_clears_values():
    feed = ready()
    for second in (1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75):
        value = sample(feed, second, status(1))
        assert value["state"] == "fresh"
        assert value["valid_for_ms"] == int((3 - second) * 1000)
    assert sample(feed, 3, status(1))["telemetry"] is None
    assert sample(feed, 3.25, status(3))["state"] == "waiting"
    assert sample(feed, 4)["state"] == "fresh"
    assert feed.response(NONCE, elapsed=5.01)["reason"] == "publisher_observation_gap"
    assert feed.response(NONCE, elapsed=5.01)["telemetry"] is None


def test_projection_is_allowlisted_and_invalid_optional_values_remain_null():
    source = status(1, mFuelLevel=.5, mFuelCapacity=8, mThrottle=float("nan"),
                    mBrake=True, mGear=3, mLastLapTime=-1, mBestLapTime=49.7,
                    mName="PRIVATE DRIVER", raw_base64="PRIVATE RAW", machine="PRIVATE PATH")
    value = project(source["latest"])
    assert value["fuel_litres"] == 4 and value["gear"] == 3
    assert value["throttle_fraction"] is None and value["brake_fraction"] is None
    assert value["simulator_last_lap_s"] is None and value["simulator_best_lap_s"] == 49.7
    assert "PRIVATE" not in json.dumps(value)
    assert value["view"]["identity"] == "viewed_car_unconfirmed"


@pytest.mark.parametrize("change,reason", [
    ({"coverage": "stale_mapping"}, "coverage_stale_mapping"),
    ({"status": "failed"}, "capture_failed"),
    ({"status": "complete"}, "capture_stopped"),
    ({"latest_is_current": False}, "latest_not_current"),
    ({"offset_s": -1}, "invalid_offset"),
    ({"schema_version": 3}, "unsupported_schema_version"),
])
def test_faults_clear_current_readings_and_need_new_progress(change, reason):
    feed = ready()
    value = sample(feed, 2, status(2) | change)
    assert value["state"] == "stale" and value["telemetry"] is None
    assert value["reason"] == reason
    assert sample(feed, 3)["state"] == "waiting"
    assert sample(feed, 4)["state"] == "fresh"


def test_same_sample_controls_changed_or_view_switched_cannot_look_fresh():
    feed = ready()
    value = sample(feed, 1.25, status(1, mThrottle=1))
    assert value["reason"] == "same_sample_changed" and value["telemetry"] is None
    assert sample(feed, 2)["state"] == "waiting"
    assert sample(feed, 3)["state"] == "fresh"
    view = dict(schema_version=1, index=1, count=2)
    assert sample(feed, 4, status(4, participant_observation=view))["reason"] == "view_changed"
    assert sample(feed, 5, status(5, participant_observation=view))["state"] == "fresh"


def test_source_context_clock_changes_and_response_mutation():
    feed = ready()
    assert sample(feed, 2, source="b" * 64)["state"] == "waiting"
    assert sample(feed, 3, source="b" * 64)["state"] == "fresh"
    assert sample(feed, 4, status(4, mCarName="another"), "b" * 64)["state"] == "waiting"
    value = sample(feed, 5, status(5, mCarName="another"), "b" * 64)
    value["telemetry"]["context"]["car"] = "mutated"
    assert feed.response(NONCE, elapsed=5)["telemetry"]["context"]["car"] == "another"
    feed.sample(status(6), source_id="b" * 64, now=BASE + 6, elapsed=5.25)
    assert feed.response(NONCE, elapsed=5.25)["telemetry"] is None


def test_operations_panel_cannot_carry_through_unseen_source_transition():
    feed = ready()
    feed.panels(None, dict(state="return_due", valid_for_ms=2000), elapsed=1)
    before = feed.response(NONCE, elapsed=1)
    assert before["fuel_supervision"] == "return_due"
    assert sample(feed, 1.25, status(1.25, mCarName="another"))["state"] == "waiting"
    fresh = sample(feed, 1.5, status(1.5, mCarName="another"))
    assert fresh["state"] == "fresh" and fresh["fuel_supervision"] == "unknown"
    assert fresh["source_epoch"] == before["source_epoch"] + 1
    assert fresh["source_epoch_at"] == fresh["source_at"]


def archive_at(path):
    path.mkdir()
    identity = dict(schema_version=1, expected_version=14, struct_size_bytes=20700,
                    private_name="PRIVATE DRIVER", path="PRIVATE PATH")
    (path / "run.json").write_text(json.dumps(identity))
    (path / "status.json").write_text(json.dumps(status(0)))
    return path


def test_bounded_source_read_does_not_change_files_and_refuses_bad_identity(tmp_path):
    archive = archive_at(tmp_path / "archive")
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    identity, value = read_source(archive)
    assert len(identity) == 64 and value == status(0)
    assert {p.name: p.read_bytes() for p in archive.iterdir()} == before
    (archive / "status.json").write_text(" " * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="source_too_large"):
        read_source(archive)
    (archive / "run.json").write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="duplicate_json_key"):
        read_source(archive)


def request(port, *, token="test-token", path="/v1/telemetry", method="GET", extra=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, headers={"Authorization": "Bearer " + token,
                           "X-ORC-Request-ID": NONCE} | (extra or {}))
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_real_http_is_authenticated_read_only_no_store_and_has_no_raw_endpoint():
    feed = ready()
    with DashboardServer(("127.0.0.1", 0), feed, "test-token", {}, clock=lambda: 1.1) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            code, headers, raw = request(server.server_port)
            assert code == 200 and json.loads(raw)["state"] == "fresh"
            assert headers["Cache-Control"] == "no-store"
            assert "Access-Control-Allow-Origin" not in headers
            assert request(server.server_port, token="wrong")[0] == 401
            assert request(server.server_port, extra={"X-ORC-Request-ID": "bad"})[0] == 400
            assert request(server.server_port, extra={"Origin": "https://other.example"})[0] == 403
            assert request(server.server_port, extra={"Host": "other.example"})[0] == 403
            for path in ("/run.json", "/../run.json", "/v1/telemetry?token=test-token", "/stop"):
                assert request(server.server_port, path=path)[0] == 404
            assert request(server.server_port, method="POST")[0] == 501
        finally:
            server.shutdown()
            worker.join(timeout=3)
    with pytest.raises(ValueError, match="loopback_required"):
        DashboardServer(("0.0.0.0", 0), feed, "test-token", {})


def test_new_summary_timings_are_simulator_reported_and_finite():
    from simcoach.adapters.ams2 import SharedMemory
    page = SharedMemory()
    page.mBestLapTime, page.mLastLapTime, page.mCurrentTime = 49.7, float("nan"), 12
    value = snapshot_summary(page)
    assert value["mBestLapTime"] == pytest.approx(49.7)
    assert value["mLastLapTime"] is None and value["mCurrentTime"] == 12


def test_real_bridge_process_reads_synthetic_archive_and_closes_without_touching_source(tmp_path):
    archive, out = archive_at(tmp_path / "archive"), tmp_path / "bridge"
    before_identity = (archive / "run.json").read_bytes()
    root = Path(__file__).resolve().parents[1]
    with (tmp_path / "stdout.log").open("w") as stdout, \
            (tmp_path / "stderr.log").open("w") as stderr:
        process = subprocess.Popen([sys.executable, "-B", "-m", "simcoach.cli.dashboard",
            "--archive", str(archive), "--out", str(out), "--duration", "15"], cwd=root,
            stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.perf_counter() + 10
            while not (out / "run.json").exists():
                assert process.poll() is None
                assert time.perf_counter() < deadline
                time.sleep(.05)
            run = json.loads((out / "run.json").read_text())
            token = (out / "connection-code.txt").read_text()
            observed = []
            start = time.time()
            while time.perf_counter() < deadline:
                now = time.time()
                value = status(now - start)
                value["updated_at"] = datetime.fromtimestamp(now, UTC).isoformat()
                value["offset_s"] = now - start
                write_status(archive / "status.json", value)
                time.sleep(.3)
                code, headers, body = request(run["port"], token=token)
                observed.append(json.loads(body)["state"])
                if observed[-1] == "fresh":
                    break
            assert "fresh" in observed
            write_status(archive / "status.json", status(0) | {"coverage": "stale_mapping"})
            time.sleep(.5)
            assert json.loads(request(run["port"], token=token)[2])["telemetry"] is None
            (out / "stop").touch()
            assert process.wait(timeout=8) == 0
            assert json.loads((out / "status.json").read_text())["state"] == "stopped"
            assert (archive / "run.json").read_bytes() == before_identity
            assert {p.name for p in archive.iterdir()} == {"run.json", "status.json"}
            assert token not in (tmp_path / "stdout.log").read_text()
        finally:
            if process.poll() is None:
                (out / "stop").touch()
                process.wait(timeout=8)

def test_dashboard_waits_for_a_pair_after_an_archive_continuity_change():
    feed = DashboardFeed()
    assert sample(feed, 0, status(0) | {"continuity_generation": 0})["state"] == "waiting"
    assert sample(feed, 1, status(1) | {"continuity_generation": 0})["state"] == "fresh"
    feed.panels(None, dict(state="armed", valid_for_ms=2000), elapsed=1)
    reset = status(1.25) | {"continuity_generation": 1}
    pending = sample(feed, 1.25, reset)
    assert pending["state"] == "waiting" and pending["reason"] == "source_continuity_changed"
    assert pending["telemetry"] is None and pending["fuel_supervision"] == "unknown"
    assert sample(feed, 1.5, reset)["state"] == "waiting"
    fresh = sample(feed, 1.75, status(1.75) | {"continuity_generation": 1})
    assert fresh["state"] == "fresh" and fresh["continuity_generation"] == 1
    assert fresh["source_epoch"] == 1 and fresh["fuel_supervision"] == "unknown"
