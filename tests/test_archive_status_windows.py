"""Real Windows reader contention against disposable synthetic archive files."""

import base64
import ctypes
import json
import sys
import time

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli import telemetry_watch
from simcoach.live.archive_status import ArchiveStatusPublisher
from tests.test_archive_status import records
from tests.test_telemetry_watch import Clock, Source, sample

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file-sharing semantics")


def wait_until(predicate, *, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("native status contention barrier timed out")


def test_reader_without_delete_sharing_does_not_stop_raw_and_status_recovers(tmp_path):
    path = tmp_path / "status.json"
    original = b'{"status":"running","marker":"original before contention"}\n'
    path.write_bytes(original)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    # GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING.
    # Omitting FILE_SHARE_DELETE creates actual Windows replacement contention.
    handle = kernel.CreateFileW(str(path.resolve()), 0x80000000, 1 | 2, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    def release_reader():
        nonlocal handle
        if handle is not None:
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None

    publisher = ArchiveStatusPublisher(path, retry_min_s=.02, retry_max_s=.04)
    values = [sample(sequence * 2) for sequence in range(1, 21)]
    clock = Clock(tmp_path, len(values))
    evidence = {}

    class ContendedSource(Source):
        reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                wait_until(lambda: publisher.snapshot()["failures"] >= 1)
            if self.reads == 9:
                # The reader is still held after eight samples and three closed
                # segment rotations. No mocked writer or replacement is used.
                wait_until(lambda: publisher.snapshot()["failures"] >= 2)
                assert handle is not None and path.read_bytes() == original
                raw, manifests = records(tmp_path)
                assert len(manifests) == 3 and len(raw) == 6
                assert [base64.b64decode(row["raw_base64"]) for row in raw] == values[:6]
                state = publisher.snapshot()
                assert state["last_failure"]["winerror"] in (5, 32)
                failed = [tmp_path / f"status-publication-{attempt:08d}.tmp"
                          for attempt in range(1, state["last_failure"]["attempt"] + 1)]
                evidence["failed_bytes"] = {file: file.read_bytes() for file in failed}
                assert len(failed) >= 2
                assert all(json.loads(raw)["status"] == "running"
                           for raw in evidence["failed_bytes"].values())
                release_reader()
                wait_until(lambda: publisher.snapshot()["confirmed_sequence"] > 0)
                recovered = json.loads(path.read_text())
                assert recovered["status"] == "running" and recovered["samples"] >= 3
                assert recovered["status_publication"]["prior_state"]["failures"] >= 2
                evidence["recovered"] = recovered
            return super().read()

    try:
        code = telemetry_watch.watch(
            tmp_path, ContendedSource(values), SharedMemory, hz=2, segment_seconds=1,
            monotonic=clock.read, sleep=clock.sleep, status_publisher=publisher,
        )
        assert code == 0
        assert "recovered" in evidence
        raw, manifests = records(tmp_path)
        assert len(manifests) == 10 and len(raw) == len(values)
        assert [base64.b64decode(row["raw_base64"]) for row in raw] == values
        assert not list(tmp_path.glob("*.partial"))
        for file, retained in evidence["failed_bytes"].items():
            assert file.read_bytes() == retained
        final = json.loads(path.read_text())
        assert final["status"] == "complete" and final["samples"] == len(values)
        assert final["status_publication"]["prior_state"]["failures"] >= 2
        events = [json.loads(row) for row in (tmp_path / "events.jsonl").read_text().splitlines()]
        assert any(row["event"] == "status_publication_failure" for row in events)
        assert events[-1]["terminal_confirmed"] is True
        assert events[-1]["capture_status"] == "complete"
    finally:
        release_reader()
        publisher.close(timeout_s=2)
