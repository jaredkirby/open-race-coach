"""Read-only process identity and Driver Visit recovery evidence inspection."""

from __future__ import annotations

import ctypes
import hashlib
import math
import os
import stat
import time
from ctypes import wintypes
from pathlib import Path

from simcoach.live.json_read import read_with_retry
from simcoach.live.supervisor import strict_json

QUERY_AND_SYNCHRONIZE = 0x1000 | 0x100000
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 258


def _pid(value):
    return type(value) is int and 0 < value <= 0xFFFFFFFF


def _created(value):
    return (isinstance(value, str) and value.isascii() and value.isdecimal()
            and len(value) <= 20 and value == str(int(value))
            and 0 < int(value) <= 0xFFFFFFFFFFFFFFFF)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _probe_windows(pid, kernel, last_error):
    """Injectable native boundary; never requests modification rights."""
    result = dict(state="unknown", pid=pid, created_100ns=None, reason=None)
    handle = kernel.OpenProcess(QUERY_AND_SYNCHRONIZE, False, pid)
    if not handle:
        error = last_error()
        result.update(state="absent" if error == 87 else "unknown",
                      reason="process_absent" if error == 87 else f"open_process_error_{error}")
        return result
    try:
        creation, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            result["reason"] = f"process_times_error_{last_error()}"
        else:
            value = creation.dwLowDateTime | creation.dwHighDateTime << 32
            if value == 0:
                result["reason"] = "invalid_creation_time"
            else:
                result["created_100ns"] = str(value)
                wait = kernel.WaitForSingleObject(handle, 0)
                if wait == WAIT_TIMEOUT:
                    result["state"] = "running"
                elif wait == WAIT_OBJECT_0:
                    result["state"] = "exited"
                else:
                    result["reason"] = f"process_wait_error_{last_error()}"
    finally:
        if not kernel.CloseHandle(handle):
            result.update(state="unknown", reason=f"close_handle_error_{last_error()}")
    return result


def probe_process(pid):
    """Snapshot a Windows PID using creation FILETIME, never PID alone as identity.

    Creation time is decimal text: a FILETIME exceeds JavaScript's exact integer range.
    Access denied and API failures remain unknown; this does not test lock ownership.
    """
    result = dict(state="unknown", pid=pid if _pid(pid) else None,
                  created_100ns=None, reason="invalid_pid")
    if not _pid(pid):
        return result
    if os.name != "nt":
        result["reason"] = "unsupported_platform"
        return result
    try:
        return _probe_windows(pid, _kernel32(), ctypes.get_last_error)
    except Exception as exc:
        result["reason"] = f"probe_error_{type(exc).__name__}"
        return result


def _artifact(run, relative):
    path = run / relative
    info = dict(path=relative, state="missing", sha256=None)
    def read():
        if not _confined(run, path):
            return None
        return path.read_bytes()

    def diagnostic(value):
        info.setdefault("reads", []).append(value)

    try:
        raw = read_with_retry(read, role="visit_inspection_artifact", diagnostic=diagnostic)
    except FileNotFoundError:
        return info, None
    except OSError:
        info["state"] = "unreadable"
        return info, None
    if raw is None:
        info["state"] = "outside_or_redirected"
        return info, None
    info["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        value = strict_json(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        info["state"] = "invalid"
        return info, None
    if not isinstance(value, dict):
        info["state"] = "invalid"
        return info, None
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        info["state"] = "unsupported"
        return info, None
    info["state"] = "present"
    return info, value


def _confined(run, path):
    """Reject traversal and reparse components before opening diagnostic files."""
    try:
        relative = path.relative_to(run)
        path.resolve().relative_to(run.resolve())
        current = run
        for part in relative.parts:
            if part in (".", ".."):
                return False
            current = current / part
            try:
                details = current.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(details.st_mode)
                    or getattr(details, "st_file_attributes", 0) & 0x400):
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _identity(record, probe, clock):
    recorded = record.get("process") if isinstance(record, dict) else None
    result = dict(state="unknown", identity_status="unavailable",
                  recorded_process=recorded if isinstance(recorded, dict) else None,
                  observed_process=None, observed_at=None, reason="recorded_identity_unavailable")
    if (not isinstance(recorded, dict) or not _pid(recorded.get("pid"))
            or not _created(recorded.get("created_100ns"))):
        return result
    # If an outer PID exists, both pieces of the spawn receipt must agree.
    if "pid" in record and record["pid"] != recorded["pid"]:
        result["reason"] = "recorded_pid_conflict"
        return result
    try:
        observed = probe(recorded["pid"])
    except Exception as exc:
        result["reason"] = f"probe_error_{type(exc).__name__}"
        return result
    observed_at = clock()
    if not _number(observed_at):
        raise ValueError("invalid_inspection_clock")
    result["observed_at"] = observed_at
    result["observed_process"] = observed if isinstance(observed, dict) else None
    if (not isinstance(observed, dict) or not _pid(observed.get("pid"))
            or observed["pid"] != recorded["pid"]
            or observed.get("state") not in ("running", "exited", "absent", "unknown")
            or observed.get("reason") is not None and not isinstance(observed["reason"], str)):
        result["reason"] = "invalid_probe_result"
        return result
    if observed["state"] == "unknown":
        result["reason"] = observed.get("reason") or "probe_unknown"
    elif observed["state"] == "absent":
        result.update(state="absent", reason="recorded_process_absent")
    elif not _created(observed.get("created_100ns")):
        result["reason"] = "observed_identity_unavailable"
    elif recorded["created_100ns"] != observed["created_100ns"]:
        result.update(state="absent", identity_status="mismatch", reason="pid_reused")
    else:
        result.update(state=observed["state"], identity_status="matched",
                      reason=observed.get("reason"))
    return result


def _closure(artifacts):
    closure_intent = artifacts["closure_intent"]["state"]
    closure_result = artifacts["closure"]["state"]
    if closure_result == "present" and closure_intent == "present":
        return "result_recorded"
    elif closure_result == "present" and closure_intent == "missing":
        return "result_without_intent"
    elif closure_result == "missing" and closure_intent == "present":
        return "intent_without_result"
    elif closure_result == closure_intent == "missing":
        return "not_recorded"
    return "unknown"


WORKERS = ("worker", "worker-0002", "worker-0003")


def _discovery_issues(run):
    issues = []
    for relative, allowed, prefix in ((".", WORKERS, "worker-"),
                                      ("capture-attempts", ("0001", "0002", "0003"), "")):
        directory = run / relative
        if not _confined(run, directory):
            issues.append(dict(path=relative, reason="outside_or_redirected"))
            continue
        try:
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 256:
                        issues.append(dict(path=relative, reason="discovery_limit"))
                        break
                    if entry.name.startswith(prefix) and entry.name not in allowed:
                        # Root legacy receipt files are not attempt directories.
                        if (relative == "." and not entry.is_dir(follow_symlinks=False)
                                and not entry.is_symlink()):
                            continue
                        issues.append(dict(path=str(Path(relative) / entry.name),
                                           reason="unrecognized_attempt_entry"))
        except FileNotFoundError:
            pass
        except OSError:
            issues.append(dict(path=relative, reason="discovery_unreadable"))
    return issues


def _inspect_attempt(run, number, probe, clock):
    worker = WORKERS[number - 1]
    receipt = f"capture-attempts/{number:04d}"
    artifacts, values = {}, {}
    paths = dict(intent=f"{receipt}/intent.json", spawn=f"{receipt}/spawn.json",
                 exit=f"{receipt}/exit.json", worker_process=f"{worker}/worker-process.json",
                 worker_status=f"{worker}/status.json",
                 closure_intent=f"{worker}/closure-intent.json",
                 closure=f"{worker}/closure.json")
    if number == 1:
        paths.update(legacy_intent="worker-intent.json", legacy_spawn="worker-spawn.json")
    for key, relative in paths.items():
        artifacts[key], values[key] = _artifact(run, relative)
    observed = any(info["state"] != "missing" for info in artifacts.values())
    observed = observed or os.path.lexists(run / worker) or os.path.lexists(run / receipt)
    if not observed and number != 1:
        return None
    intent_key = ("legacy_intent"
                  if number == 1 and artifacts["intent"]["state"] == "missing" else "intent")
    spawn_key = ("legacy_spawn"
                 if number == 1 and artifacts["spawn"]["state"] == "missing" else "spawn")
    launcher = _identity(values[spawn_key], probe, clock)
    actual = _identity(values["worker_process"], probe, clock)
    issues = []
    legacy_launcher = None
    if number == 1 and values["spawn"] and values["legacy_spawn"]:
        left, right = values["spawn"].get("process"), values["legacy_spawn"].get("process")
        if left != right:
            issues.append("launcher_receipts_conflict")
            legacy_launcher = _identity(values["legacy_spawn"], probe, clock)
    status = values["worker_status"] or {}
    if (_pid(status.get("pid")) and actual["recorded_process"]
            and status["pid"] != actual["recorded_process"].get("pid")):
        issues.append("worker_status_pid_conflict")
    return dict(attempt=number, worker_directory=worker, observed=observed,
                artifacts=artifacts, launcher=launcher, worker=actual,
                conflicting_legacy_launcher=legacy_launcher,
                launcher_identity_source=artifacts[spawn_key]["path"],
                worker_identity_source=artifacts["worker_process"]["path"],
                intent_source=artifacts[intent_key]["path"],
                reported_worker_status=status.get("status")
                if isinstance(status.get("status"), str) else None,
                closure_evidence=_closure(artifacts), issues=issues,
                launch_ambiguous=artifacts[intent_key]["state"] != "missing" and
                (actual["reason"] in ("recorded_identity_unavailable", "recorded_pid_conflict")
                 or launcher["reason"] in ("recorded_identity_unavailable", "recorded_pid_conflict")
                 or bool(issues)))


def inspect_visit(run, *, probe=probe_process, clock=time.time):
    """Inspect every bounded attempt; a selected pointer never hides other owners."""
    run = Path(run).absolute()
    started = clock()
    if not _number(started):
        raise ValueError("invalid_inspection_clock")
    artifacts, values = {}, {}
    for key, relative in {
        "host_process": "host-process.json", "worker_spawn": "worker-spawn.json",
        "worker_intent": "worker-intent.json", "status": "status.json",
        "heartbeat": "heartbeat.json",
    }.items():
        artifacts[key], values[key] = _artifact(run, relative)
    host = _identity(values["host_process"], probe, clock)
    attempts = [row for number in range(1, 4)
                if (row := _inspect_attempt(run, number, probe, clock)) is not None]
    issues = _discovery_issues(run)
    state = values["status"] or {}
    pointer = state.get("current_worker", "worker")
    selection = "legacy_default" if "current_worker" not in state else "selected"
    selected = next((row for row in attempts if row["worker_directory"] == pointer), None)
    if not isinstance(pointer, str) or pointer not in WORKERS:
        selection = "invalid"
        selected = None
    elif selected is None or not selected["observed"]:
        selection = "unobserved"
    if selection in ("invalid", "unobserved"):
        issues.append(dict(path="status.json", reason=f"current_worker_{selection}"))
    worker = selected["worker"] if selected else _identity(None, probe, clock)
    launcher = selected["launcher"] if selected else _identity(None, probe, clock)
    for key in ("worker_status", "closure_intent", "closure"):
        if selected:
            artifacts[key] = selected["artifacts"][key]
        else:
            artifacts[key] = dict(path=None, state="unselected", sha256=None)
    finished = clock()
    identities = [host] + [row[key] for row in attempts for key in ("launcher", "worker")]
    identities += [row["conflicting_legacy_launcher"] for row in attempts
                   if row["conflicting_legacy_launcher"] is not None]
    if (not _number(finished) or finished < started
            or any(row["observed_at"] is not None and not started <= row["observed_at"] <= finished
                   for row in identities)):
        raise ValueError("invalid_inspection_clock")
    if host["state"] == "running":
        assessment = "host_running"
    elif any(row["state"] == "running" for row in identities[1:]):
        assessment = "worker_running_without_verified_host"
    elif (not issues and not any(row["issues"] for row in attempts)
          and all(row["state"] in ("absent", "exited") for row in identities)):
        assessment = "no_recorded_process_running"
    else:
        assessment = "unknown"
    heartbeat = values["heartbeat"]
    heartbeat_at = heartbeat.get("at") if heartbeat else None
    if not _number(heartbeat_at):
        heartbeat_at = None
    heartbeat_age = finished - heartbeat_at if _number(heartbeat_at) else None
    return dict(
        schema_version=1, inspected_at=started, completed_at=finished, assessment=assessment,
        host=host, worker=worker, worker_launcher=launcher, artifacts=artifacts,
        current_worker=pointer if isinstance(pointer, str) and pointer in WORKERS else None,
        current_worker_selection=selection, attempts=attempts, discovery_issues=issues,
        reported_visit_status=state.get("status") if isinstance(state.get("status"), str) else None,
        reported_worker_status=selected["reported_worker_status"] if selected else None,
        heartbeat=dict(at=heartbeat_at, age_s=heartbeat_age,
                       fresh=heartbeat_age is not None and 0 <= heartbeat_age < 3),
        closure_evidence=selected["closure_evidence"] if selected else "unknown",
        launch_ambiguous=bool(issues) or any(row["launch_ambiguous"] for row in attempts),
        lock_ownership_verified=False, actions_taken=[], driver_departure_inferred=False,
    )
