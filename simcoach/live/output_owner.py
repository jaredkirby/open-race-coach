"""Windows-session output exclusion by named-object lifetime, not a timed lease.

No audio or kernel object is opened on import or construction. The production
name is shared across journals and checkouts. This is cooperative exclusion,
not endpoint ownership or protection against unrelated audio applications.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

OUTPUT_NAME = r"Local\OpenRaceCoach.RadioOutput.v1"


def _kernel():
    if os.name != "nt":
        raise RuntimeError("windows_output_ownership_required")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    dll.CreateMutexW.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    return dll


class WindowsOutputClaim:
    """Only the creator may output; an existing object always denies admission.

    We never wait for thread ownership or call ReleaseMutex. Even a second
    claim on the same thread is refused. A dead thread cannot make a living
    process's audio claim available. Native handles are non-inheritable.
    Test-only injection can select a private name; production uses OUTPUT_NAME.
    """

    def __init__(self, *, name=OUTPUT_NAME, kernel=None, last_error=None):
        if (not isinstance(name, str) or not name.startswith("Local\\")
                or not 6 < len(name) < 260 or "\0" in name or "\\" in name[6:]):
            raise ValueError("invalid_output_claim_name")
        self.name, self.kernel, self.last_error = name, kernel, last_error
        self.handle = None
        self.state = "unclaimed"
        self.error = None

    def acquire(self):
        if self.state != "unclaimed":
            raise RuntimeError("output_claim_not_reusable")
        try:
            if self.kernel is None:
                self.kernel = _kernel()
            if self.last_error is None:
                self.last_error = ctypes.get_last_error
            if os.name == "nt":
                ctypes.set_last_error(0)
            # False: object existence is the claim; no thread owns the mutex.
            self.handle = self.kernel.CreateMutexW(None, False, self.name)
            error = self.last_error()
            if not self.handle:
                raise RuntimeError(f"output_claim_create_failed_{error}")
            if error == 183:  # ERROR_ALREADY_EXISTS: close only our extra handle.
                if not self.kernel.CloseHandle(self.handle):
                    raise RuntimeError(f"output_claim_close_failed_{self.last_error()}")
                self.handle = None
                raise RuntimeError("radio_output_already_owned")
            self.state = "held"
        except Exception as exc:
            self.state = "unavailable"
            self.error = str(exc)
            raise

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                self.error = f"output_claim_close_failed_{self.last_error()}"
                raise RuntimeError(self.error)
            self.handle = None
        self.state = "closed"

    def snapshot(self):
        return dict(schema_version=1, scope="windows_session", state=self.state,
                    reason=self.error, audio_health_verified=False)
