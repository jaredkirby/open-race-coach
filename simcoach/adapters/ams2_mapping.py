"""Read-only access to existing AMS2 shared-memory mappings."""

from __future__ import annotations

import ctypes
import struct
import time

SHARED_MEMORY_NAME = "$pcars2$"


class ExistingMapping:
    """Open an existing mapping read-only; never create a zero-filled simulator page."""

    def __init__(self, size: int, sequence_offset: int, *, name: str = SHARED_MEMORY_NAME):
        self.name = name
        self.size = size
        self.sequence_offset = sequence_offset
        self.handle = None
        self.view = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel.OpenFileMappingW.restype = ctypes.c_void_p
        self.kernel.MapViewOfFile.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t,
        ]
        self.kernel.MapViewOfFile.restype = ctypes.c_void_p
        self.kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self.kernel.UnmapViewOfFile.restype = ctypes.c_int
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = ctypes.c_int

    def open(self) -> None:
        self.handle = self.kernel.OpenFileMappingW(4, False, self.name)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "AMS2 shared-memory mapping unavailable")
        self.view = self.kernel.MapViewOfFile(self.handle, 4, 0, 0, self.size)
        if not self.view:
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "Cannot map AMS2 shared memory")

    def read(self) -> bytes | None:
        if self.view is None:
            self.open()
        # A poll can coincide with the simulator writing the shared page. Retry
        # once after yielding, always starting the entire sequence check again.
        raw = self._copy_once()
        if raw is None:
            time.sleep(0.001)
            raw = self._copy_once()
        return raw

    def _copy_once(self) -> bytes | None:
        before = ctypes.c_uint32.from_address(self.view + self.sequence_offset).value
        if before % 2:
            return None
        raw = ctypes.string_at(self.view, self.size)
        after = ctypes.c_uint32.from_address(self.view + self.sequence_offset).value
        copied = struct.unpack_from("<I", raw, self.sequence_offset)[0]
        return raw if before == copied == after and after % 2 == 0 else None

    def close(self) -> None:
        if self.view is not None:
            self.kernel.UnmapViewOfFile(self.view)
            self.view = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
