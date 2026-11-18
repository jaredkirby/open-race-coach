import ctypes
import hashlib
import os
from ctypes import wintypes

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live import visit_recovery as recovery

CREATED = "134118253480000001"


@pytest.mark.parametrize("persistent", [False, True])
def test_artifact_sharing_denial_retries_fresh_reads(tmp_path, monkeypatch, persistent):
    from pathlib import Path

    path = tmp_path / "status.json"
    write_status(path, dict(schema_version=1, status="running"))
    original = Path.read_bytes
    calls = []

    def read(current):
        calls.append(current)
        if persistent or len(calls) == 1:
            raise PermissionError(13, "synthetic file replacement contention")
        return original(current)

    monkeypatch.setattr(Path, "read_bytes", read)
    evidence, value = recovery._artifact(tmp_path, "status.json")
    assert evidence["reads"][0]["recovered"] is (not persistent)
    if persistent:
        assert 1 <= len(calls) <= 3 and value is None
        assert evidence["state"] == "unreadable" and evidence["sha256"] is None
    else:
        assert len(calls) == 2 and value["status"] == "running"
        assert evidence["state"] == "present"
        assert evidence["sha256"] == hashlib.sha256(original(path)).hexdigest()


def test_artifact_retry_rechecks_confinement(tmp_path, monkeypatch):
    from pathlib import Path

    calls = []

    def read(current):
        calls.append(current)
        raise PermissionError(13, "synthetic contention before redirect")

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(recovery, "_confined", lambda *args: not calls)
    evidence, value = recovery._artifact(tmp_path, "status.json")
    assert len(calls) == 1 and value is None
    assert evidence["state"] == "outside_or_redirected"


class Kernel:
    def __init__(self, *, opened=True, times=True, wait=258, close=True):
        self.opened, self.times, self.wait, self.close = opened, times, wait, close
        self.calls = []

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, inherit, pid))
        return 42 if self.opened else 0

    def GetProcessTimes(self, handle, creation, *unused):
        self.calls.append(("times", handle))
        value = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
        value.dwLowDateTime = int(CREATED) & 0xFFFFFFFF
        value.dwHighDateTime = int(CREATED) >> 32
        return self.times

    def WaitForSingleObject(self, handle, delay):
        self.calls.append(("wait", handle, delay))
        return self.wait

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return self.close


@pytest.mark.parametrize("wait,state", [(258, "running"), (0, "exited"), (0xFFFFFFFF, "unknown")])
def test_native_probe_uses_limited_read_rights_zero_wait_and_closes(wait, state):
    kernel = Kernel(wait=wait)
    result = recovery._probe_windows(123, kernel, lambda: 6)
    assert result["state"] == state
    assert result["created_100ns"] == CREATED
    assert kernel.calls == [("open", 0x101000, False, 123), ("times", 42),
                            ("wait", 42, 0), ("close", 42)]


@pytest.mark.parametrize("error,state", [(5, "unknown"), (87, "absent"), (123, "unknown")])
def test_open_failure_distinguishes_access_denied_from_absence(error, state):
    kernel = Kernel(opened=False)
    result = recovery._probe_windows(123, kernel, lambda: error)
    assert result["state"] == state
    assert result["created_100ns"] is None
    assert len(kernel.calls) == 1


def test_times_failure_still_closes_without_claiming_liveness():
    kernel = Kernel(times=False)
    result = recovery._probe_windows(123, kernel, lambda: 5)
    assert result["state"] == "unknown"
    assert result["created_100ns"] is None
    assert kernel.calls[-1] == ("close", 42)
    assert not any(row[0] == "wait" for row in kernel.calls)


def test_close_failure_downgrades_result():
    kernel = Kernel(close=False)
    assert recovery._probe_windows(123, kernel, lambda: 6)["state"] == "unknown"


@pytest.mark.parametrize("pid", [0, -1, True, "123", 2**32])
def test_invalid_pid_never_opens_native_handle(pid, monkeypatch):
    monkeypatch.setattr(recovery, "_kernel32", lambda: pytest.fail("native call"))
    assert recovery.probe_process(pid)["reason"] == "invalid_pid"


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only process integration")
def test_read_only_own_process_probe_has_stable_creation_identity():
    first = recovery.probe_process(os.getpid())
    second = recovery.probe_process(os.getpid())
    assert first == second
    assert first["state"] == "running"
    assert first["pid"] == os.getpid()
    assert recovery._created(first["created_100ns"])


def process(pid, state="running", created=CREATED):
    return dict(pid=pid, state=state, created_100ns=created, reason=None)


def make_visit(tmp_path):
    (tmp_path/"worker").mkdir()
    write_status(tmp_path/"host-process.json", dict(schema_version=1, process=process(10)))
    write_status(tmp_path/"worker-spawn.json", dict(schema_version=1, pid=20, process=process(20)))
    write_status(tmp_path/"worker/worker-process.json", dict(schema_version=1, at=999,
                                                           process=process(20)))
    write_status(tmp_path/"worker-intent.json", dict(schema_version=1, at=999))
    write_status(tmp_path/"heartbeat.json", dict(schema_version=1, at=1000))
    write_status(tmp_path/"status.json", dict(schema_version=1, status="running"))
    return tmp_path


def inspect(run, probe=process):
    return recovery.inspect_visit(run, probe=probe, clock=lambda: 1001)


def test_matching_running_identities_and_fresh_heartbeat_without_effects(tmp_path):
    run = make_visit(tmp_path)
    before = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in run.rglob("*") if p.is_file()}
    result = inspect(run)
    assert result["assessment"] == "host_running"
    assert result["worker"]["identity_status"] == "matched"
    assert result["worker"]["observed_at"] == 1001
    assert result["heartbeat"] == dict(at=1000, age_s=1, fresh=True)
    assert result["actions_taken"] == []
    assert result["lock_ownership_verified"] is result["driver_departure_inferred"] is False
    after = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in run.rglob("*") if p.is_file()}
    assert after == before


def test_reused_host_pid_does_not_hide_surviving_worker(tmp_path):
    result = inspect(make_visit(tmp_path), lambda pid: process(
        pid, created=str(int(CREATED)+1) if pid == 10 else CREATED))
    assert result["host"]["state"] == "absent"
    assert result["host"]["identity_status"] == "mismatch"
    assert result["host"]["reason"] == "pid_reused"
    assert result["assessment"] == "worker_running_without_verified_host"
    assert result["host"]["observed_process"]["state"] == "running"


def test_absent_processes_do_not_establish_restart_safety_or_departure(tmp_path):
    result = inspect(make_visit(tmp_path), lambda pid: process(pid, "absent", None))
    assert result["assessment"] == "no_recorded_process_running"
    assert "safe_to_restart" not in result
    assert result["driver_departure_inferred"] is False
    assert result["launch_ambiguous"] is False


def test_legacy_pid_only_remains_unknown_even_when_pid_is_running(tmp_path):
    run = make_visit(tmp_path)
    write_status(run/"host-process.json", dict(schema_version=1, pid=10))
    write_status(run/"worker-spawn.json", dict(schema_version=1, pid=20))
    (run/"worker/worker-process.json").unlink()
    result = inspect(run, lambda pid: pytest.fail("must not upgrade PID-only identity"))
    assert result["assessment"] == "unknown"
    assert result["launch_ambiguous"] is True


def test_missing_spawn_after_intent_is_ambiguous(tmp_path):
    run = make_visit(tmp_path)
    (run/"worker-spawn.json").unlink()
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert result["assessment"] == "unknown"
    assert result["worker_launcher"]["state"] == "unknown"
    assert result["launch_ambiguous"] is True


def test_access_denied_or_probe_exception_stays_unknown(tmp_path):
    run = make_visit(tmp_path)
    result = inspect(run, lambda pid: process(pid, "unknown", None))
    assert result["assessment"] == "unknown"

    def failed(pid):
        raise OSError("synthetic")

    assert inspect(run, failed)["host"]["reason"] == "probe_error_OSError"


@pytest.mark.parametrize("value", ["{", "[]", '{"schema_version":2}'])
def test_bad_status_artifacts_are_visible_without_overriding_process_evidence(tmp_path, value):
    run = make_visit(tmp_path)
    (run/"status.json").write_text(value)
    result = inspect(run)
    assert result["artifacts"]["status"]["state"] in ("invalid", "unsupported")
    assert result["assessment"] == "host_running"
    assert result["reported_visit_status"] is None


@pytest.mark.parametrize("at,fresh", [(998, False), (1002, False), (1000, True), (None, False)])
def test_heartbeat_reports_stale_future_and_unknown_without_liveness_inference(tmp_path, at, fresh):
    run = make_visit(tmp_path)
    write_status(run/"heartbeat.json", dict(schema_version=1, at=at))
    result = inspect(run)
    assert result["heartbeat"]["fresh"] is fresh
    assert result["host"]["state"] == "running"


@pytest.mark.parametrize("intent,result,expected", [
    (False, False, "not_recorded"), (True, False, "intent_without_result"),
    (False, True, "result_without_intent"), (True, True, "result_recorded"),
])
def test_closure_is_evidence_only_and_never_reexecuted(tmp_path, intent, result, expected):
    run = make_visit(tmp_path)
    if intent:
        write_status(run/"worker/closure-intent.json", dict(schema_version=1, sessions=[]))
    if result:
        write_status(run/"worker/closure.json", dict(schema_version=1, recordings=[]))
    report = inspect(run)
    assert report["closure_evidence"] == expected
    assert report["actions_taken"] == []


def test_missing_visit_does_not_get_created(tmp_path):
    run = tmp_path/"missing"
    assert inspect(run)["assessment"] == "unknown"
    assert not run.exists()


def test_conflicting_outer_pid_refuses_probe(tmp_path):
    run = make_visit(tmp_path)
    write_status(run/"worker-spawn.json", dict(schema_version=1, pid=21, process=process(20)))
    result = inspect(run)
    assert result["worker_launcher"]["reason"] == "recorded_pid_conflict"
    assert result["worker"]["identity_status"] == "matched"


def test_unknown_api_result_cannot_establish_absence_even_with_different_creation(tmp_path):
    result = inspect(make_visit(tmp_path), lambda pid: process(
        pid, "unknown", str(int(CREATED)+1)))
    assert result["host"]["state"] == "unknown"
    assert result["assessment"] == "unknown"


@pytest.mark.parametrize("created", [123, "0", "00123", "x", str(2**64), None])
def test_noncanonical_recorded_creation_does_not_get_coerced(tmp_path, created):
    run = make_visit(tmp_path)
    write_status(run/"host-process.json", dict(schema_version=1,
                                              process=process(10, created=created)))
    assert inspect(run)["host"]["state"] == "unknown"


@pytest.mark.parametrize("times", [
    [1000, float("nan")], [1000, 999, 1001, 1002, 1003],
    [1000, 1003, 1001, 1002, 1002],
])
def test_invalid_or_out_of_interval_probe_timestamp_is_rejected(tmp_path, times):
    run = make_visit(tmp_path)
    values = iter(times)
    with pytest.raises(ValueError, match="invalid_inspection_clock"):
        recovery.inspect_visit(run, probe=process, clock=lambda: next(values))


def add_attempt(run, number, launcher_pid, worker_pid):
    worker = run / f"worker-{number:04d}"
    receipt = run / "capture-attempts" / f"{number:04d}"
    worker.mkdir()
    receipt.mkdir(parents=True)
    write_status(receipt/"intent.json", dict(schema_version=1, at=999))
    write_status(receipt/"spawn.json", dict(schema_version=1, pid=launcher_pid,
                                           process=process(launcher_pid)))
    write_status(worker/"worker-process.json", dict(schema_version=1, at=999,
                                                    process=process(worker_pid)))
    write_status(worker/"status.json", dict(schema_version=1, pid=worker_pid,
                                            status="recording"))
    return worker, receipt


def test_current_attempt_does_not_hide_earlier_surviving_actual_worker(tmp_path):
    run = make_visit(tmp_path)
    add_attempt(run, 2, 30, 31)
    add_attempt(run, 3, 40, 41)
    write_status(run/"status.json", dict(schema_version=1, status="running",
                                        current_worker="worker-0003"))
    result = inspect(run, lambda pid: process(pid, "running" if pid == 31 else "exited"))
    assert result["assessment"] == "worker_running_without_verified_host"
    assert result["worker"]["state"] == "exited"
    assert result["worker"]["recorded_process"]["pid"] == 41
    assert result["worker_launcher"]["recorded_process"]["pid"] == 40
    assert [a["worker_directory"] for a in result["attempts"]] == list(recovery.WORKERS)
    assert result["attempts"][1]["worker"]["state"] == "running"
    assert result["artifacts"]["worker_status"]["path"] == "worker-0003/status.json"


def test_dead_launcher_never_establishes_actual_worker_exit(tmp_path):
    run = make_visit(tmp_path)
    write_status(run/"worker/worker-process.json", dict(schema_version=1, at=999,
                                                       process=process(21)))
    result = inspect(run, lambda pid: process(pid, "running" if pid == 21 else "exited"))
    assert result["assessment"] == "worker_running_without_verified_host"
    assert result["worker"]["state"] == "running"
    assert result["worker_launcher"]["state"] == "exited"
    (run/"worker/worker-process.json").unlink()
    missing = inspect(run, lambda pid: process(pid, "exited"))
    assert missing["worker"]["state"] == "unknown"
    assert missing["assessment"] == "unknown"
    assert missing["launch_ambiguous"] is True


@pytest.mark.parametrize("pointer", ["../outside", "worker/../worker-0002", "worker-0004",
                                    "C:\\outside", None, 2])
def test_invalid_current_pointer_does_not_hide_attempts_or_read_paths(tmp_path, pointer):
    run = make_visit(tmp_path)
    add_attempt(run, 2, 30, 31)
    write_status(run/"status.json", dict(schema_version=1, status="running",
                                        current_worker=pointer))
    result = inspect(run, lambda pid: process(pid, "running" if pid == 31 else "exited"))
    assert result["current_worker_selection"] == "invalid"
    assert result["current_worker"] is None
    assert result["worker"]["state"] == "unknown"
    assert result["assessment"] == "worker_running_without_verified_host"
    assert len(result["attempts"]) == 2
    assert result["artifacts"]["worker_status"]["state"] == "unselected"


def test_unregistered_attempt_and_missing_actual_identity_prevent_absence_claim(tmp_path):
    run = make_visit(tmp_path)
    (run/"worker-0002").mkdir()
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert len(result["attempts"]) == 2
    assert result["attempts"][1]["artifacts"]["spawn"]["state"] == "missing"
    assert result["assessment"] == "unknown"


def test_unexpected_attempt_entry_is_not_silently_ignored(tmp_path):
    run = make_visit(tmp_path)
    (run/"worker-0004").mkdir()
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert result["assessment"] == "unknown"
    assert result["discovery_issues"] == [dict(path="worker-0004",
                                               reason="unrecognized_attempt_entry")]


def test_attempt_receipt_only_is_visible_and_exit_code_is_not_identity(tmp_path):
    run = make_visit(tmp_path)
    receipt = run/"capture-attempts/0002"
    receipt.mkdir(parents=True)
    write_status(receipt/"intent.json", dict(schema_version=1, at=999))
    write_status(receipt/"exit.json", dict(schema_version=1, at=1000, exit_code=0))
    result = inspect(run, lambda pid: process(pid, "exited"))
    second = result["attempts"][1]
    assert second["worker"]["state"] == "unknown"
    assert second["artifacts"]["exit"]["state"] == "present"
    assert second["launch_ambiguous"] is True
    assert result["assessment"] == "unknown"


def test_initial_attempt_preserves_conflicting_legacy_and_new_provenance(tmp_path):
    run = make_visit(tmp_path)
    receipt = run/"capture-attempts/0001"
    receipt.mkdir(parents=True)
    write_status(receipt/"spawn.json", dict(schema_version=1, pid=22, process=process(22)))
    result = inspect(run, lambda pid: process(pid, "exited"))
    first = result["attempts"][0]
    assert first["issues"] == ["launcher_receipts_conflict"]
    assert first["artifacts"]["legacy_spawn"]["state"] == "present"
    assert first["launcher_identity_source"] == "capture-attempts/0001/spawn.json"
    assert result["assessment"] == "unknown"


def test_redirected_artifact_is_not_opened(tmp_path, monkeypatch):
    run = make_visit(tmp_path)
    original = recovery._confined
    blocked = run/"worker/worker-process.json"
    monkeypatch.setattr(recovery, "_confined", lambda root, path:
                        False if path == blocked else original(root, path))
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert result["attempts"][0]["artifacts"]["worker_process"]["state"] == "outside_or_redirected"
    assert result["worker"]["state"] == "unknown"
    assert result["assessment"] == "unknown"


def test_artifact_traversal_never_reads_outside_selected_run(tmp_path):
    run = tmp_path/"visit"
    run.mkdir()
    outside = tmp_path/"outside.json"
    write_status(outside, dict(schema_version=1, secret="must not read"))
    info, value = recovery._artifact(run, "../outside.json")
    assert info["state"] == "outside_or_redirected"
    assert info["sha256"] is None
    assert value is None


def test_reparse_component_is_rejected_before_read(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    run = make_visit(tmp_path)
    original = Path.lstat

    def details(path, *args, **kwargs):
        if path == run/"worker":
            return SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", details)
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert result["worker"]["state"] == "unknown"
    assert result["attempts"][0]["artifacts"]["worker_process"]["sha256"] is None
    assert result["assessment"] == "unknown"


def test_conflicting_old_launcher_can_still_be_alive(tmp_path):
    run = make_visit(tmp_path)
    receipt = run/"capture-attempts/0001"
    receipt.mkdir(parents=True)
    write_status(receipt/"spawn.json", dict(schema_version=1, pid=22, process=process(22)))
    write_status(run/"worker/worker-process.json", dict(schema_version=1, process=process(23)))
    result = inspect(run, lambda pid: process(pid, "running" if pid == 20 else "exited"))
    assert result["assessment"] == "worker_running_without_verified_host"
    assert result["attempts"][0]["conflicting_legacy_launcher"]["state"] == "running"


def test_all_three_attempts_with_verified_exits_are_reported_without_restart_permission(tmp_path):
    run = make_visit(tmp_path)
    add_attempt(run, 2, 30, 31)
    add_attempt(run, 3, 40, 41)
    write_status(run/"status.json", dict(schema_version=1, status="complete",
                                        current_worker="worker-0003"))
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert result["assessment"] == "no_recorded_process_running"
    assert result["actions_taken"] == []
    assert "safe_to_restart" not in result


def test_unobserved_current_pointer_keeps_missing_attempt_unknown(tmp_path):
    run = make_visit(tmp_path)
    write_status(run/"status.json", dict(schema_version=1, status="running",
                                        current_worker="worker-0003"))
    result = inspect(run, lambda pid: process(pid, "exited"))
    assert result["current_worker_selection"] == "unobserved"
    assert result["assessment"] == "unknown"
    assert result["reported_worker_status"] is None
