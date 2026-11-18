"""Combined OS processes with synthetic adapter/source; never open AMS2 or audio.

The harness replaces only simulator input and test-owned child entry points.
Host/worker/service lifecycle, recorder, validation and analysis execute normally.
Production mode labels describe selected code paths, not live-test provenance.
"""
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.cli.visit import request
from simcoach.live import visit_release, visit_review
from simcoach.live.dashboard_feedback import prepare_feedback, select_feedback
from simcoach.live.json_read import read_with_retry
from simcoach.live.stint_checkpoint import request_checkpoint
from simcoach.live.supervisor import source_identity
from simcoach.live.visit_recovery import probe_process
from simcoach.live.visit_worker import local_path
from tests.test_fuel_radio import CONTEXT, status
from tests.test_operations_client import plan
from tests.test_session import session_info

REPO = Path(__file__).resolve().parents[1]
ROLES = {"simcoach.cli.visit": "host", "simcoach.live.visit_worker": "worker",
         "simcoach.live.operations_service": "service"}


def child(role, root, arguments, entry=None):
    """Entrypoint used only by this test's hidden children."""
    if role in {"host", "worker"} and (root / "storage-before-low").exists():
        from simcoach.live import recording_storage

        def generated_capacity(_):
            low = any((root / f"storage-{window}-low").exists()
                      and not (root / f"storage-{window}-restored").exists()
                      for window in ("before", "during"))
            return recording_storage.MIN_FREE_BYTES - 1 if low else 4 * 1024 ** 3

        recording_storage.free_bytes = generated_capacity
    if role == "worker":
        from simcoach.adapters import ams2
        from simcoach.adapters.base import NormalizedTick
        from simcoach.live.visit_worker import main

        class SyntheticAdapter:
            sim_name = "ams2"

            def connect(self):
                self.started = time.perf_counter()

            def disconnect(self):
                pass

            def read_session_info(self):
                latest = self.source()
                return dict(session_info(), car_raw=CONTEXT.car, car="example_car",
                            track_raw=CONTEXT.track, track="example_track",
                            session_type={1: "practice", 5: "race", 6: "hotlap"}[
                                latest["mSessionState"]],
                            track_length_m=100.0, adapter_version="synthetic-process-test")

            def read_tick(self):
                latest = self.source()
                elapsed = time.perf_counter() - self.started
                progress = elapsed % 2 / 2
                angle = progress * 2 * math.pi
                return NormalizedTick(
                    t=elapsed, lap=int(elapsed / 2) + 1, lap_dist_pct=progress,
                    lap_dist_m=progress * 100, speed=50, throttle=.5, brake=0,
                    steering=.1, gear=3, rpm=5000,
                    vehicle_state="running" if latest["mGameState"] == 2 else "menu",
                    pos_x=math.cos(angle) * 16, pos_y=math.sin(angle) * 16, pos_z=0)

            def source(self):
                return read_with_retry(
                    lambda: json.loads((root / "archive/status.json").read_text())["latest"],
                    role="synthetic_adapter")

        ams2.AMS2Adapter = SyntheticAdapter
        return (entry or main)(arguments)
    if role == "service":
        from simcoach.live import operations_service

        artifact = operations_service._artifact

        def lose_archive_during_request(directory, name):
            result = artifact(directory, name)
            if (name.startswith("requests/") and (root / "approval-source-fault").exists()
                    and not (root / "approval-source-fault-entered").exists()):
                (root / "approval-source-fault-entered").touch()
                deadline = time.perf_counter() + 3
                while time.perf_counter() < deadline:
                    packet = read_with_retry(
                        lambda: json.loads((root / "archive/status.json").read_text()),
                        role="synthetic_approval_boundary")
                    if packet["coverage"] == "stale_mapping":
                        break
                    time.sleep(.01)
                else:
                    raise RuntimeError("synthetic approval source fault not published")
            return result

        operations_service._artifact = lose_archive_during_request

        def fail_when_requested():
            while not (root / "fail-service").exists():
                time.sleep(.02)
            os._exit(7)  # Only this test-owned process; no external process is killed.

        threading.Thread(target=fail_when_requested, daemon=True).start()
        return (entry or operations_service.main)(arguments)
    from simcoach.cli import visit
    spawn = subprocess.Popen

    def test_spawn(command, **kwargs):
        if visit_release.active_binding() is not None:
            # Refuse an unexpected child shape rather than accidentally opening AMS2.
            command = guarded_command(command, root)
            return spawn(command, **kwargs)
        module = command[2] if len(command) > 2 and command[1] == "-m" else None
        roles = {"simcoach.live.visit_worker": "worker",
                 "simcoach.live.operations_service": "service"}
        if module in roles:
            command = child_command(roles[module], root, command[3:])
        return spawn(command, **kwargs)

    visit.subprocess.Popen = test_spawn
    if entry is not None:
        return entry(arguments)
    return visit.run_visit(root / "visits", intent="Synthetic process integration only",
                           present=True, archive=root / "archive", plan=plan(),
                           separate_operations=True, max_seconds=60)


def child_command(role, root, arguments=()):
    code = ("import sys; from pathlib import Path; "
            "from tests.test_visit_operations_process import child; "
            "raise SystemExit(child(sys.argv[1], Path(sys.argv[2]), sys.argv[3:]))")
    return [sys.executable, "-c", code, role, str(root), *arguments]


def guarded_entry(root):
    """Inject only test input/failure hooks, then enter the real release CLI."""
    import importlib
    module = sys.argv[sys.argv.index("--module") + 1]
    if module not in ROLES:
        raise ValueError("unexpected_synthetic_child_role")
    target = importlib.import_module(module)
    original = target.main
    target.main = lambda arguments: child(ROLES[module], root, arguments, entry=original)
    from simcoach.cli.visit_release import main
    return main()


def guarded_command(command, root):
    """Keep isolated guard arguments/source selection; substitute a test bootstrap."""
    if (command[1:6] != ["-I", "-B", "-u", "-c", visit_release.BOOTSTRAP]
            or command[7] != "run"
            or command[command.index("--module") + 1] not in ROLES):
        raise ValueError("unexpected_guarded_child_command")
    code = ("import sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv.pop(1)); "
            "sys.path.insert(1, sys.argv.pop(1)); "
            "from tests.test_visit_operations_process import guarded_entry; "
            "raise SystemExit(guarded_entry(Path(sys.argv.pop(1))))")
    return [*command[:5], code, command[6], str(REPO), str(root), *command[7:]]


@pytest.mark.skipif(os.name != "nt", reason="Windows actual process identities")
@pytest.mark.parametrize(("service_crash", "storage_pressure"), [
    (False, False), (True, False), (False, True),
], ids=["normal", "service_exit", "storage_pressure"])
@pytest.mark.parametrize("guarded", [False, True], ids=["workspace", "guarded"])
def test_combined_host_worker_service_closure_and_failure(
        tmp_path, service_crash, guarded, storage_pressure, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    write_status(tmp_path / "test-provenance.json", dict(
        schema_version=1, mode="synthetic_process_integration", audio_enabled=False,
        actual_driver_presence=False, live_validation="NOT RUN",
        input="generated normalized adapter and separate synthetic status publisher",
        service_crash=service_crash, guarded_source=guarded, storage_pressure=storage_pressure,
        test_bootstrap=guarded, test_harness=str(Path(__file__).resolve())))
    write_status(archive / "run.json", dict(schema_version=1, expected_version=14,
                                            struct_size_bytes=20700))
    if storage_pressure:
        (tmp_path / "storage-before-low").touch()
    started = time.time()
    observations = []
    generation = 0
    context = dict(mGameState=1, mSessionState=1, mRaceState=1, mSpeed=0)

    def publish():
        now = time.time()
        stamp = datetime.fromtimestamp(now, UTC)
        if stamp.timestamp() > now:
            stamp -= timedelta(microseconds=1)
        seconds = now - started
        value = status(seconds, 8, **context)
        value.update(updated_at=stamp.isoformat(), samples=int(seconds * 10000) + 1,
                     continuity_generation=generation)
        value["latest"]["mSequenceNumber"] = value["samples"] * 2
        if ((tmp_path / "approval-source-fault-entered").exists()
                and not (tmp_path / "approval-source-restored").exists()):
            value.update(coverage="stale_mapping", latest_is_current=False)
        write_status(archive / "status.json", value)

    def read(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    publish()
    streams = [(tmp_path / f"host.{suffix}.log").open("xb") for suffix in ("stdout", "stderr")]
    prepared = None
    host_command = child_command("host", tmp_path)
    if guarded:
        snapshot = tmp_path / "snapshot"
        prepared = visit_release.prepare_release(REPO, snapshot)
        write_status(tmp_path / "plan.json", dict(schema_version=1, **asdict(plan())))
        host_command = guarded_command(visit_release.launch_command(
            snapshot, prepared["release_id"], "simcoach.cli.visit", tmp_path / "host-launch",
            ["start", "--present", "--intent", "Synthetic guarded process integration",
             "--archive", str(archive), "--out", str(tmp_path / "visits"),
             "--plan", str(tmp_path / "plan.json"), "--operations-service",
             "--max-seconds", "60"]), tmp_path)
    previous_capture = SourceLock(tmp_path / "visits" / "capture.lock")
    try:
        host = subprocess.Popen(host_command, stdout=streams[0], stderr=streams[1],
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except BaseException:
        previous_capture.close()
        for stream in streams:
            stream.close()
        raise
    run = None
    dashboard = None
    dashboard_root = tmp_path / "dashboard"
    dashboard_packets = []
    metrics = {}
    # Ignore machine proxy configuration for this explicit synthetic loopback test.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def poll_dashboard():
        info = read(dashboard_root / "run.json")
        if not info:
            return {}
        token = (dashboard_root / "connection-code.txt").read_text(encoding="ascii")
        nonce = uuid4().hex
        query = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}/v1/telemetry",
            headers={"Authorization": "Bearer " + token, "X-ORC-Request-ID": nonce})
        began = time.perf_counter()
        with opener.open(query, timeout=2) as response:
            packet = json.loads(response.read(128 * 1024 + 1))
        assert packet["request_id"] == nonce
        dashboard_packets.append(dict(at=time.time(), round_trip_s=time.perf_counter()-began,
                                      packet=packet))
        return packet

    def until(predicate, timeout=10):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            publish()
            value = read(run / "status.json") if run else {}
            if dashboard is not None:
                assert dashboard.poll() is None, "synthetic dashboard exited early"
                value["test_dashboard"] = poll_dashboard()
            observations.append(value)
            if predicate(value):
                return value
            assert host.poll() is None, "test host exited before expected observation"
            time.sleep(.05)
        pytest.fail("combined process observation deadline expired")

    try:
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            candidates = list((tmp_path / "visits").glob("*/host-process.json"))
            if candidates:
                run = candidates[0].parent
                break
            publish()
            time.sleep(.05)
        assert run is not None
        if storage_pressure:
            low = until(lambda v: v.get("phase") == "waiting_for_capture_storage"
                        and (v.get("source") or {}).get("ready") is True)
            assert low["recording_storage"]["state"] == "low"
            assert low["recovery"]["attempts"] == []
            assert low["clock"]["presence"] == "declared_present"
            (tmp_path / "storage-before-restored").touch()
        blocked = until(lambda v: v.get("phase") == "waiting_for_capture_lock")
        still_blocked = until(lambda v: v.get("phase") == "waiting_for_capture_lock"
                              and v["clock"]["elapsed_s"] > blocked["clock"]["elapsed_s"])
        assert still_blocked["source"]["ready"] and not still_blocked["capture_ready"]
        assert still_blocked["clock"]["presence"] == "declared_present"
        assert still_blocked["recovery"]["attempts"] == []
        assert not (run / "capture-attempts").exists()
        previous_capture.close()
        # Capture must start while the synthetic car is still in the garage.
        ready = until(lambda v: v.get("capture_ready"))
        assert ready["clock"]["visit_id"] == blocked["clock"]["visit_id"]
        assert [row["attempt"] for row in ready["recovery"]["attempts"]] == [1]
        assert not ready["recovery"]["capture_interrupted"]
        assert read(archive / "status.json")["latest"]["mGameState"] == 1
        assert ready["worker"]["active_ticks"] > 0
        for suffix in ("stdout", "stderr"):
            streams.append((tmp_path / f"dashboard.{suffix}.log").open("xb"))
        dashboard_args = ["--archive", str(archive), "--out", str(dashboard_root),
                          "--visit", str(run), "--operations", str(run / "operations"),
                          "--duration", "60"]
        dashboard_command = (visit_release.launch_command(
            snapshot, prepared["release_id"], "simcoach.cli.dashboard",
            tmp_path / "dashboard-launch", dashboard_args) if guarded else
            [sys.executable, "-B", "-m", "simcoach.cli.dashboard", *dashboard_args])
        dashboard = subprocess.Popen(dashboard_command,
            stdout=streams[-2], stderr=streams[-1], creationflags=subprocess.CREATE_NO_WINDOW)
        until(lambda v: v["test_dashboard"].get("state") == "fresh")
        if guarded and not service_crash and not storage_pressure:
            context.update(mGameState=2, mRaceState=2, mSpeed=0)
            stationary = until(lambda v: v.get("capture_ready") and
                             v.get("operations", {}).get("state", {}).get(
                                 "chief_link", {}).get("state") == "connected" and
                             v.get("supervision", {}).get("phase") == "active")
            (tmp_path / "approval-source-fault").touch()
            pending = request(run, "fuel", cockpit_litres=8, verified_at=time.time(),
                              source_id=source_identity(archive), solo_player_confirmed=True,
                              note="Synthetic approval while still in garage")
            receipt_file = Path(pending["receipt_path"])
            refused = until(lambda v: read(receipt_file).get("accepted") is False
                            and not v.get("capture_ready")
                            and v.get("worker", {}).get("ticks_observed", 0)
                            > stationary["worker"]["ticks_observed"])
            receipt = read(receipt_file)
            assert receipt["reason"] == "capture_not_healthy" and not receipt["applied"]
            assert receipt["authority"] == "operations_service"
            assert refused["worker"]["pid"] == stationary["worker"]["pid"]
            assert refused["clock"]["visit_id"] == stationary["clock"]["visit_id"]
            assert read(archive / "status.json")["latest"]["mSpeed"] == 0
            write_status(tmp_path / "approval-source-fault.json", dict(
                receipt=str(receipt_file), worker_pid=refused["worker"]["pid"],
                ticks_advanced=True, archive_admission=False, live_validation="NOT RUN"))
            (tmp_path / "approval-source-restored").touch()
            until(lambda v: v.get("capture_ready") and
                  v.get("supervision", {}).get("supervision_health") == "healthy" and
                  v["supervision"]["fuel_state"] == "unarmed")
        context.update(mGameState=2, mRaceState=2, mSpeed=50)
        until(lambda v: v.get("capture_ready") and
                      v.get("operations", {}).get("health") == "healthy" and
                      v.get("operations", {}).get("state", {}).get(
                          "chief_link", {}).get("state") == "connected" and
                      v.get("supervision", {}).get("supervision_health") == "healthy")
        identities = [read(run / name)["process"] for name in (
            "host-process.json", "worker/worker-process.json", "operations/run.json")]
        assert len({p["pid"] for p in identities}) == 3
        assert all(probe_process(p["pid"])["created_100ns"] == p["created_100ns"]
                   for p in identities)
        request(run, "fuel", cockpit_litres=8, verified_at=time.time(),
                source_id=source_identity(archive), solo_player_confirmed=True,
                note="Synthetic process integration only")
        until(lambda v: any(read(p).get("accepted") and
                            read(p).get("authority") == "operations_service"
                            for p in (run / "receipts").glob("*.json")))
        armed = until(lambda v: v["test_dashboard"].get("operations", {}).get("state") == "armed")
        assert armed["test_dashboard"]["continuity_generation"] == 0
        if guarded and not service_crash and not storage_pressure:
            # Skip the degraded heartbeat. All source counters and clocks keep
            # advancing with the same context and native recorder process.
            mark = len(dashboard_packets)
            generation = 1
            restarted = until(lambda v: v.get("capture_ready")
                              and v["test_dashboard"].get("continuity_generation") == 1
                              and v["test_dashboard"]["operations"]["state"] == "unarmed"
                              and (v.get("supervision") or {}).get("fuel_state") == "unarmed")
            assert restarted["worker"]["pid"] == identities[1]["pid"]
            assert restarted["clock"]["visit_id"] == armed["clock"]["visit_id"]
            new_frames = [row["packet"] for row in dashboard_packets[mark:]
                          if row["packet"].get("continuity_generation") == 1]
            assert new_frames and all(row["operations"]["state"] not in ("armed", "return_due")
                                      for row in new_frames)
            reload = request(run, "fuel", cockpit_litres=8, verified_at=time.time(),
                             source_id=source_identity(archive), solo_player_confirmed=True,
                             note="Synthetic new cockpit evidence after archive generation change")
            def current_arm(value):
                service = value.get("operations", {}).get("state") or {}
                observed = service.get("observed_source") or {}
                frame = value.get("test_dashboard", {})
                return (read(Path(reload["receipt_path"])).get("accepted") is True
                        and observed.get("continuity_generation") == 1
                        and frame.get("continuity_generation") == 1
                        and frame["operations"]["state"] == "armed")

            rearmed = until(current_arm)
            assert rearmed["worker"]["pid"] == identities[1]["pid"]
            observed = rearmed["operations"]["state"]["observed_source"]
            assert observed["continuity_generation"] == 1
            write_status(tmp_path / "continuity-readback.json", dict(
                before=0, after=1, recorder_unchanged=True, driver_remained_seated=True,
                previous_approval_withheld=True, explicit_reapproval=True,
                local_dashboard_only=True, actual_ams2="NOT RUN"))
        # Ensure multiple actual worker observations, not just an initial status file.
        baseline = ready["worker"]["ticks_observed"]
        until(lambda v: v.get("worker", {}).get("ticks_observed", 0) > baseline + 130)
        # Review the first stint without ending physical-presence state or capture.
        checkpoint_started = time.perf_counter()
        checkpoint = request_checkpoint(run)
        receipt = Path(checkpoint["receipt_path"])
        after = until(lambda v: read(receipt).get("state") == "complete")
        metrics["checkpoint_s"] = time.perf_counter() - checkpoint_started
        assert after["clock"]["presence"] == "declared_present"
        assert not (run / "stop").exists()
        assert after["worker"]["pid"] == identities[1]["pid"]
        assert after["test_dashboard"]["feedback"]["state"] == "unavailable"
        recorded = read(receipt)["recordings"][-1]
        card = prepare_feedback(run, Path(recorded["path"]))
        original_read = visit_review._read_bound
        status_advanced = False

        def advance_during_review(path, root, **kwargs):
            nonlocal status_advanced
            result = original_read(path, root, **kwargs)
            if path == card and not status_advanced:
                start = read(run / "status.json")["updated_at"]
                until(lambda v: v.get("updated_at", 0) > start)
                status_advanced = True
            return result

        with monkeypatch.context() as patch:
            patch.setattr(visit_review, "_read_bound", advance_during_review)
            review = visit_review.record_review(
                run, output=card, verdict="insufficient", reviewer="chief",
                note="Synthetic integration: small sample, no driver evidence.")
        accepted_review = review["review"]
        assert status_advanced
        assert (accepted_review["visit_source"]["sha256"]
                != accepted_review["visit_source_before_publication"]["sha256"])
        assert accepted_review["visit_identity"]["visit_id"] == after["clock"]["visit_id"]
        assert accepted_review["review_latency_utc_s"] is None
        selection_started = time.perf_counter()
        select_feedback(run, review["path"])
        displayed = until(lambda v: v["test_dashboard"].get("feedback", {}).get(
            "state") == "reviewed")
        metrics["selected_feedback_to_http_s"] = time.perf_counter() - selection_started
        feedback = displayed["test_dashboard"]["feedback"]
        assert feedback["card"]["objective"] is None
        assert not feedback["delivery_verified"] and not feedback["driver_endorsed"]
        assert feedback["card"]["laps"] == read(card)["display"]["laps"]
        assert str(run) not in json.dumps(displayed["test_dashboard"])
        assert "small sample" not in json.dumps(displayed["test_dashboard"])
        until(lambda v: v.get("capture_ready") and
              v.get("worker", {}).get("ticks_observed", 0) > after["worker"]["ticks_observed"]+30)
        if storage_pressure:
            (tmp_path / "storage-during-low").touch()
            pressure_started = time.perf_counter()
            limited = until(lambda v: v.get("worker_exit") == 1
                            and v["recovery"]["state"] == "capture_storage_unavailable")
            assert not limited["capture_ready"] and limited["source"]["ready"]
            assert limited["worker"]["stop_reason"] == "recording_disk_reserve"
            assert limited["supervision"]["fuel_state"] == "armed"
            assert limited["clock"]["visit_id"] == ready["clock"]["visit_id"]
            assert len(limited["recovery"]["attempts"]) == 1
            original_closure = (run / "worker/closure.json").read_bytes()
            metrics["storage_pressure_to_replacement_hold_s"] = time.perf_counter()-pressure_started
            (tmp_path / "storage-during-restored").touch()
            recovered = until(lambda v: v.get("current_worker") == "worker-0002"
                              and v.get("capture_ready") and v["worker"]["active_ticks"] > 30)
            assert recovered["clock"]["visit_id"] == ready["clock"]["visit_id"]
            assert recovered["operations"]["service_id"] == limited["operations"]["service_id"]
            assert recovered["supervision"]["fuel_state"] == "armed"
            assert recovered["worker"]["pid"] != identities[1]["pid"]
            assert (run / "worker/closure.json").read_bytes() == original_closure
        elif service_crash:
            baseline = read(run / "status.json")["worker"]["ticks_observed"]
            (tmp_path / "fail-service").touch()
            crash_started = time.perf_counter()
            until(lambda v: v.get("operations", {}).get("launcher_exit") == 7 and
                  v.get("supervision", {}).get("fuel_state") == "unknown" and
                  v["test_dashboard"].get("operations", {}).get("state") == "unknown" and
                  v.get("capture_ready") and
                  v.get("worker", {}).get("ticks_observed", 0) > baseline + 30)
            metrics["service_crash_to_observed_unknown_s"] = time.perf_counter()-crash_started
        else:
            # Same capture owner crosses modes; each new plan needs fresh fuel approval.
            for session_state, expected_count in ((5, 3), (6, 4)):
                context["mSessionState"] = session_state
                context.update(mFuelLevel=4/33, participant_observation=dict(
                    schema_version=1, index=0, count=3 if session_state == 5 else 2,
                    active=True, car=CONTEXT.car, race_state=2, pit_mode=0))
                state = until(lambda v, expected_count=expected_count: v.get("capture_ready") and
                      len(v.get("worker", {}).get("sessions", [])) >= expected_count)
                config = replace(plan(), context=replace(CONTEXT, session_state=session_state),
                                 timed_laps=5, out_laps=0, return_threshold_litres=1.5)
                request(run, "plan", plan=dict(schema_version=1, **asdict(config)),
                        expected_plan_id=state["supervision"]["plan_id"],
                        note="Synthetic mode transition")
                ready = until(lambda v, session_state=session_state: v.get(
                    "supervision", {}).get("plan", {}).get(
                    "context", {}).get("session_state") == session_state and
                    v["supervision"]["supervision_health"] == "healthy")
                assert ready["supervision"]["fuel_state"] == "unarmed"
                assert ready["worker"]["pid"] == identities[1]["pid"]
                assert ready["clock"]["presence"] == "declared_present"
                request(run, "fuel", cockpit_litres=4, verified_at=time.time(),
                        source_id=source_identity(archive), solo_player_confirmed=False,
                        player_view_confirmed=True, participant_index=0,
                        note="Fresh synthetic cockpit check after plan acceptance")
                until(lambda v, session_state=session_state: v.get(
                      "supervision", {}).get("fuel_state") == "armed" and
                      v["test_dashboard"].get("operations", {}).get("state") == "armed" and
                      v["test_dashboard"]["telemetry"]["context"]["session_state"] ==
                      session_state)
            context.update(mGameState=1, mSpeed=0)
            until(lambda v: v.get("capture_ready") and
                  v.get("activity_source", {}).get("activity") == "not_driving")
        request(run, "stop")
        interrupted = service_crash or storage_pressure
        assert host.wait(timeout=20) == (1 if interrupted else 0)
        final = read(run / "status.json")
        assert final["status"] == ("incomplete" if interrupted else "complete")
        assert final["clock"]["presence"] == "declared_absent"
        assert final["worker_exit"] == 0
        assert len(final["recovery"]["attempts"]) == (2 if storage_pressure else 1)
        rows = final["closure"]["recordings"]
        assert rows and all(r["validation"] == r["analysis"] == "PASS" for r in rows)
        if not interrupted:
            import yaml
            types = {yaml.safe_load(local_path(Path(r["path"]) / "session.yaml").read_text())[
                "session_type"] for r in rows}
            assert types == {"practice", "race", "hotlap"}
        assert not final["closure"]["unregistered_artifacts"]
        assert (run / "worker/debrief.md").is_file()
        assert not list(local_path(run).rglob("coach_response.json"))
        assert all(probe_process(p["pid"])["state"] in {"absent", "exited"} for p in identities)
        assert final["operations"]["launcher_exit"] == (7 if service_crash else 0)
        if service_crash:
            assert final["problem"] == "operations_stop_unconfirmed"
        else:
            assert final["operations"]["state"]["status"] == "complete"
            assert final["radio"]["closed"] is True
        if storage_pressure:
            assert final["problem"] == "capture_interrupted"
        outcome = next((run / "outcomes").glob("*/report.json"))
        report = read(outcome)
        assert report["operations"]["supervision_journal"]["sha256"]
        assert report["visit_status"] == final["status"]
        assert report["worker_attempts"][0]["checkpoints"]
        earlier = read(run / "worker/closure.json")["recordings"] if storage_pressure else []
        assert len(report["recordings"]) == len(earlier) + len(rows)
        for recording in report["recordings"]:
            timing = recording["lap_review"]
            assert timing["timing_basis"] == "captured_time"
            assert len(timing["laps"]) == recording["validation_summary"]["lap_count"]
        software = read(run / "outcome-result.json")["software_timing"]
        assert 0 <= software["write_and_hash_s"] <= software["after_declared_departure_s"]
        assert report["unmeasured"]["useful_debrief_latency_s"] is None
        assert all(len(list(local_path(Path(row["path"])).glob("analysis/*"))) == 1 for row in rows)
    finally:
        previous_capture.close()
        if host.poll() is None:
            if run:
                write_status(run / "stop", dict(at=time.time(), elapsed=time.perf_counter(),
                                               declaration="driver_left"))
            host.wait(timeout=45)
        if dashboard is not None:
            if dashboard.poll() is None:
                (dashboard_root / "stop").touch()
            dashboard.wait(timeout=10)
        for stream in streams:
            stream.close()
        write_status(tmp_path / "observations.json", dict(host_exit=host.returncode,
                                                         observations=observations))
        write_status(tmp_path / "dashboard-observations.json", dict(
            mode="synthetic_process_integration", mac_delivery="NOT RUN",
            audible_delivery="NOT RUN", actual_driver_presence=False,
            dashboard_exit=dashboard.returncode if dashboard else None,
            metrics=metrics, observations=dashboard_packets))
    assert dashboard.returncode == 0
    assert read(dashboard_root / "status.json")["state"] == "stopped"
    if guarded:
        bound = {}
        roles = [
            ("host", run / "host-process.json"),
            ("worker", run / "worker/worker-process.json"),
            ("service", run / "operations/run.json"),
            ("dashboard", dashboard_root / "run.json"),
        ]
        if storage_pressure:
            roles.append(("worker-0002", run / "worker-0002/worker-process.json"))
        for role, metadata in roles:
            actual = read(metadata)
            binding = actual["source_release"]
            assert binding["release_id"] == prepared["release_id"]
            receipt = read(Path(binding["launch_receipt"]))
            assert receipt["process"]["pid"] == actual["process"]["pid"]
            assert receipt["process"]["created_100ns"] == actual["process"]["created_100ns"]
            assert receipt["imported_sources"]
            assert all((snapshot / relative).is_file()
                       for relative in receipt["imported_sources"].values())
            if role == "service" and service_crash:
                # os._exit bypasses final publication; don't invent a clean receipt.
                assert receipt["state"] == "role_entering" and receipt["exit_code"] is None
            else:
                assert receipt["state"] == "role_returned"
                expected = (role == "host" and interrupted or role == "worker" and storage_pressure)
                assert receipt["exit_code"] == int(expected)
            bound[role] = dict(binding=binding, process=actual["process"],
                               state=receipt["state"], exit_code=receipt["exit_code"])
        assert len({v["process"]["pid"] for v in bound.values()}) == len(roles)
        verified = visit_release.verify_release(snapshot, prepared["release_id"])
        write_status(tmp_path / "guarded-evidence.json", dict(
            release=verified, roles=bound, test_bootstrap=True,
            actual_simulator=False, mac_delivery="NOT RUN", audible_delivery="NOT RUN"))
