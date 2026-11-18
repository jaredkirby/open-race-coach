"""Opt-in, single-process collector launch from an explicitly selected snapshot."""
from __future__ import annotations

import ctypes
import math
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

from simcoach.live.collector_release import verify_release
from simcoach.live.visit_recovery import probe_process

COLLECTOR_NAME = r"Local\OpenRaceCoachContinuousTelemetry"


class CollectorClaim:
    """Create AND own the mutex used by the legacy PowerShell launcher.

    An existing object denies admission, even when unowned or abandoned. Thread
    ownership also excludes the legacy launcher's WaitOne. Capture runs on this
    same process's main thread: there is no child surviving a supervisor crash.
    Private names are injectable for tests only; the CLI always uses production.
    """

    def __init__(self, name=COLLECTOR_NAME):
        if (not isinstance(name, str) or not name.startswith("Local\\")
                or not 6 < len(name) < 260 or "\0" in name or "\\" in name[6:]):
            raise ValueError("invalid_collector_claim")
        self.name, self.handle, self.kernel = name, None, None

    def __enter__(self):
        if os.name != "nt" or self.kernel is not None:
            raise RuntimeError("collector_claim_requires_new_windows_owner")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, True, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise RuntimeError(f"collector_claim_create_failed_{error}")
        if error == 183:
            if not kernel.CloseHandle(handle):
                raise RuntimeError("collector_claim_refusal_close_failed")
            raise RuntimeError("collector_owner_exists_reconcile_before_launch")
        self.handle = handle
        return self

    def __exit__(self, *_):
        released = self.kernel.ReleaseMutex(self.handle)
        closed = self.kernel.CloseHandle(self.handle)
        self.handle = None
        if not released or not closed:
            raise RuntimeError("collector_claim_release_unconfirmed")


def run_release(release_id, attempt, *, hz=60):
    """One explicit attempt. No loop, task edit, stop, presence change or retry."""
    from simcoach.cli.telemetry_watch import main as collect
    from simcoach.cli.telemetry_watch import write_status

    root = Path(__file__).resolve().parents[2]
    attempt = Path(attempt).absolute()
    if attempt.resolve() != attempt or attempt == root or root in attempt.parents:
        raise ValueError("attempt_must_be_outside_release_without_redirects")
    if not math.isfinite(hz) or not 1 <= hz <= 60:
        raise ValueError("invalid_capture_frequency")
    # Allocate before admission; a refusal or ambiguous process death remains an
    # occupied attempt directory, never an invitation to repeat the same effect.
    attempt.mkdir(parents=True, exist_ok=False)
    state = dict(schema_version=1, kind="collector_launch", state="admitting",
                 requested_release_id=release_id, started_at=time.time(),
                 process=probe_process(os.getpid()), archive_parent=str(attempt / "archive"),
                 release_path=str(root), target_hz=hz, automatic_retry=False,
                 live_acceptance="NOT RUN", exit_code=None)
    receipt = attempt / "launch.json"
    write_status(receipt, state)
    try:
        if not sys.flags.isolated or not sys.dont_write_bytecode:
            raise ValueError("isolated_no_bytecode_interpreter_required")
        verified = verify_release(root)
        if verified["release_id"] != release_id:
            raise ValueError("selected_release_id_mismatch")
        for name, module in tuple(sys.modules.items()):
            if name == "simcoach" or name.startswith("simcoach."):
                path = getattr(module, "__file__", None)
                if path is None or not Path(path).resolve().is_relative_to(root):
                    raise ValueError("mixed_package_sources")
        if state["process"]["state"] != "running" or not state["process"]["created_100ns"]:
            raise RuntimeError("native_process_identity_unconfirmed")
        state["verified_release"] = verified
        with CollectorClaim():
            state.update(state="collector_entering", admitted_at=time.time())
            write_status(receipt, state)
            code = collect(["--out", str(attempt / "archive"), "--hz", str(hz)])
            # This is an observed function return, not a claim of healthy capture.
            state.update(state="collector_returned", exit_code=code, ended_at=time.time(),
                         archives=[str(p.parent) for p in
                                   sorted((attempt / "archive").glob("ams2-*/run.json"))])
            write_status(receipt, state)
        state["ownership_released"] = True
        write_status(receipt, state)
        return code
    except BaseException as exc:
        state.update(state="failed", error_type=type(exc).__name__, error=str(exc),
                     ended_at=time.time())
        write_status(receipt, state)
        raise
