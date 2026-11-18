"""Real hidden Windows processes and synthetic pages; no production mutex/mapping."""
import ctypes
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from simcoach.live.collector_launch import CollectorClaim
from simcoach.live.collector_release import prepare_release, verify_release

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows collector ownership")
REPO = Path(__file__).resolve().parents[1]
CHILD = r'''
import sys
from pathlib import Path
root, attempt, release_id, claim, mode = sys.argv[1:]
sys.path.insert(0, root)
from simcoach.live import collector_launch as launch
from simcoach.cli import collector_release, telemetry_watch
from simcoach.utils.llm_logger import configure_logging
configure_logging(Path(attempt).parent / "adapter-logs")
from simcoach.adapters.ams2 import SharedMemory
original = launch.CollectorClaim
launch.CollectorClaim = lambda: original(claim)
class Synthetic:
    def __init__(self, *args):
        self.count = 0
        if mode == "capture_failure":
            raise OSError("synthetic mapping failure")
    def read(self):
        self.count += 1
        page = SharedMemory()
        page.mVersion = 14
        page.mSequenceNumber = self.count * 2
        if self.count == 5 and mode not in {"crash_test", "gap_crash"}:
            next(Path(attempt).glob("archive/ams2-*/run.json")).with_name("stop").touch()
        if self.count == 6 and mode == "crash_test":
            (Path(attempt) / "synthetic-ready").touch()
        if mode == "crash_test" and (Path(attempt) / "crash-now").exists():
            __import__("os")._exit(37)
        if mode == "gap_crash" and self.count > 5:
            if (Path(attempt) / "crash-now").exists():
                __import__("os")._exit(37)
            raise OSError("synthetic mapping loss")
        return bytes(page)
    def close(self):
        pass
telemetry_watch.ExistingMapping = Synthetic
if mode == "storage_failure":
    def unavailable(*args, **kwargs):
        raise OSError("synthetic launch receipt storage failure")
    telemetry_watch.write_status = unavailable
raise SystemExit(collector_release.main([
    "run", "--release-id", release_id, "--attempt", attempt]))
'''


def child(root, attempt, identity, *, mode="normal", isolated=True, claim=None):
    result = subprocess.run([sys.executable, *( ["-I"] if isolated else []), "-B", "-c",
        CHILD, str(root), str(attempt), identity,
        claim or f"Local\\ORC.CollectorTest.{uuid4().hex}", mode],
        cwd=attempt.parent, capture_output=True, text=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW)
    name = f"child-{uuid4().hex}"
    (attempt.parent / f"{name}.stdout.log").write_text(result.stdout)
    (attempt.parent / f"{name}.stderr.log").write_text(result.stderr)
    return result


@pytest.mark.parametrize("mode,expected", [("normal", 0), ("capture_failure", 1)])
def test_one_selected_snapshot_real_process_returns_once(tmp_path, mode, expected):
    root, attempt = tmp_path / "snapshot", tmp_path / "attempt"
    prepared = prepare_release(REPO, root)
    result = child(root, attempt, prepared["release_id"], mode=mode)
    assert result.returncode == expected, result.stderr
    launch = json.loads((attempt / "launch.json").read_text())
    assert launch["state"] == "collector_returned" and launch["exit_code"] == expected
    assert launch["ownership_released"] and launch["automatic_retry"] is False
    assert len(launch["archives"]) == 1
    archive = Path(launch["archives"][0])
    run = json.loads((archive / "run.json").read_text())
    assert run["runtime"]["release"]["release_id"] == prepared["release_id"]
    assert run["pid"] == launch["process"]["pid"]
    assert run["runtime"]["process"]["created_100ns"] == launch["process"]["created_100ns"]
    status = json.loads((archive / "status.json").read_text())
    assert status["status"] == ("complete" if expected == 0 else "failed")
    assert status["samples"] == (5 if expected == 0 else 0)
    before = (attempt / "launch.json").read_bytes()
    repeated = child(root, attempt, prepared["release_id"])
    assert repeated.returncode != 0 and "FileExistsError" in repeated.stderr
    assert (attempt / "launch.json").read_bytes() == before
    assert verify_release(root)["integrity"] == "PASS"


@pytest.mark.parametrize("fault", ["wrong_id", "cache", "not_isolated", "owned"])
def test_admission_failure_preserved_without_opening_mapping(tmp_path, fault):
    root, attempt = tmp_path / "snapshot", tmp_path / "attempt"
    prepared = prepare_release(REPO, root)
    if fault == "cache":
        cache = root / "simcoach/__pycache__"
        cache.mkdir()
        (cache / "unverified.pyc").write_bytes(b"not reviewed")
    name = f"Local\\ORC.CollectorTest.{uuid4().hex}"
    owner = CollectorClaim(name) if fault == "owned" else None
    if owner:
        owner.__enter__()
    try:
        result = child(root, attempt, "wrong" if fault == "wrong_id" else prepared["release_id"],
                       isolated=fault != "not_isolated", claim=name)
    finally:
        if owner:
            owner.__exit__()
    assert result.returncode != 0
    receipt = json.loads((attempt / "launch.json").read_text())
    assert receipt["state"] == "failed" and receipt["exit_code"] is None
    assert not (attempt / "archive").exists()
    assert receipt["automatic_retry"] is False


def test_claim_blocks_legacy_waitone_and_same_thread_duplicate(tmp_path):
    name = f"Local\\ORC.CollectorTest.{uuid4().hex}"
    with CollectorClaim(name):
        with pytest.raises(RuntimeError, match="owner_exists"), CollectorClaim(name):
            pytest.fail("duplicate ownership")
        # Native WaitForSingleObject implements the legacy .NET WaitOne semantics.
        code = r'''
import ctypes, sys
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
k.CreateMutexW.restype = wintypes.HANDLE
k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k.WaitForSingleObject.restype = wintypes.DWORD
k.CloseHandle.argtypes = [wintypes.HANDLE]
h = k.CreateMutexW(None, False, sys.argv[1])
assert h
result = k.WaitForSingleObject(h, 0)
k.CloseHandle(h)
assert result == 258, result
'''
        checked = subprocess.run([sys.executable, "-I", "-B", "-c", code, name],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert checked.returncode == 0, checked.stderr
    with CollectorClaim(name):
        pass


def test_existing_unowned_mutex_is_not_silently_adopted():
    name = f"Local\\ORC.CollectorTest.{uuid4().hex}"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, name)
    assert handle
    try:
        with pytest.raises(RuntimeError, match="owner_exists"), CollectorClaim(name):
            pytest.fail("must reconcile an existing object")
    finally:
        kernel.CloseHandle(handle)


def test_storage_failure_before_admission_leaves_attempt_and_no_mapping(tmp_path):
    root, attempt = tmp_path / "snapshot", tmp_path / "attempt"
    prepared = prepare_release(REPO, root)
    result = child(root, attempt, prepared["release_id"], mode="storage_failure")
    assert result.returncode != 0 and "synthetic launch receipt storage failure" in result.stderr
    assert attempt.is_dir() and not (attempt / "archive").exists()
    assert not (attempt / "launch.json").exists()
    repeated = child(root, attempt, prepared["release_id"])
    assert repeated.returncode != 0 and "FileExistsError" in repeated.stderr


@pytest.mark.parametrize("mode", ["crash_test", "gap_crash"])
def test_process_death_preserves_unknown_closure_without_retry(tmp_path, mode):
    root, attempt = tmp_path / "snapshot", tmp_path / "attempt"
    prepared = prepare_release(REPO, root)
    name = f"Local\\ORC.CollectorTest.{uuid4().hex}"
    # This handle owns only our synthetic test process, never a live rig process.
    with (tmp_path / "crash.stdout.log").open("w") as stdout, \
            (tmp_path / "crash.stderr.log").open("w") as stderr:
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", CHILD,
            str(root), str(attempt), prepared["release_id"], name, mode],
            cwd=tmp_path, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 15
            while not ((attempt / "synthetic-ready").exists() if mode == "crash_test"
                       else any(attempt.glob("archive/ams2-*/segments.jsonl"))):
                assert process.poll() is None, "synthetic process exited before crash exercise"
                assert time.monotonic() < deadline, "synthetic process never reached capture"
                time.sleep(.05)
            receipt = (attempt / "launch.json").read_bytes()
            value = json.loads(receipt)
            assert value["state"] == "collector_entering" and value["exit_code"] is None
            if mode == "gap_crash":
                manifest, = attempt.glob("archive/ams2-*/segments.jsonl")
                # Manifest creation and write are separate filesystem effects.
                while not (raw := manifest.read_bytes()).endswith(b"\n"):
                    assert process.poll() is None and time.monotonic() < deadline
                    time.sleep(.05)
                entry = json.loads(raw)
                assert entry["samples"] == 5 and entry["continuity_break_after"] is True
                sealed = manifest.parent / entry["path"]
                assert hashlib.sha256(sealed.read_bytes()).hexdigest() == entry["sha256"]
            (attempt / "crash-now").touch()
            assert process.wait(timeout=10) == 37
        finally:
            if process.poll() is None:
                for run in attempt.glob("archive/ams2-*/run.json"):
                    run.with_name("stop").touch()
                process.wait(timeout=10)
    assert (attempt / "launch.json").read_bytes() == receipt
    assert len(list(attempt.glob("archive/ams2-*/run.json"))) == 1
    if mode == "crash_test":
        assert list(attempt.glob("archive/ams2-*/*.partial")), "interrupted artifacts must survive"
    else:
        assert not list(attempt.glob("archive/ams2-*/*.partial"))
        assert hashlib.sha256(sealed.read_bytes()).hexdigest() == entry["sha256"]
        with gzip.open(sealed, "rt", encoding="utf-8") as stream:
            assert [json.loads(line)["sequence"] for line in stream] == [2, 4, 6, 8, 10]
    repeated = child(root, attempt, prepared["release_id"], claim=name)
    assert repeated.returncode != 0 and "FileExistsError" in repeated.stderr
    assert (attempt / "launch.json").read_bytes() == receipt
    with CollectorClaim(name):
        pass


def test_hidden_powershell_wrapper_preserves_failed_launch_with_spaced_paths(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 wrapper requires pwsh")
    root, attempt = tmp_path / "selected snapshot", tmp_path / "failed attempt"
    prepare_release(REPO, root)
    # Deliberately wrong identity must fail before production ownership or mapping.
    arguments = [pwsh, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File",
        str(REPO / "scripts/start_collector_release.ps1"), "-Python", sys.executable,
        "-Release", str(root), "-ReleaseId", "0" * 64, "-Attempt", str(attempt)]
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode != 0
    receipt = json.loads((attempt / "launch.json").read_text())
    assert receipt["error"] == "selected_release_id_mismatch"
    assert not (attempt / "archive").exists()
    logs = Path(str(attempt) + ".launcher")
    terminal = json.loads((logs / "exit.json").read_text())
    assert terminal["wrapper_exit_code"] != 0 and terminal["automatic_retry"] is False
    stderr = (logs / "stderr.log").read_bytes()
    assert b"selected_release_id_mismatch" in stderr
    second = subprocess.run(arguments, capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert second.returncode != 0 and (logs / "stderr.log").read_bytes() == stderr
    nested_arguments = arguments[:-1] + [str(root / "must-not-mutate-release")]
    nested = subprocess.run(nested_arguments, capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert nested.returncode != 0
    assert not (root / "must-not-mutate-release.launcher").exists()
    assert verify_release(root)["integrity"] == "PASS"
