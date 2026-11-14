"""Continuously archive private AMS2 shared-memory samples across simulator sessions."""

from __future__ import annotations

import argparse
import base64
import ctypes
import gzip
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_status(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class ExistingMapping:
    """Open an existing mapping read-only; never create a zero-filled simulator page."""

    def __init__(self, size: int, sequence_offset: int):
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
        self.handle = self.kernel.OpenFileMappingW(4, False, "$pcars2$")
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


class Segments:
    """Append gzip JSONL samples and retain partial files when a run fails."""

    def __init__(self, run: Path, seconds: float):
        self.run = run
        self.seconds = seconds
        self.number = 0
        self.file = None
        self.compressed = None
        self.path = None
        self.started = 0.0
        self.count = 0

    def append(self, record: dict) -> None:
        if self.file is not None and record["offset_s"] - self.started >= self.seconds:
            self.finish(complete=True)
        if self.file is None:
            self.number += 1
            self.path = self.run / f"samples-{self.number:06d}.jsonl.gz.partial"
            self.file = self.path.open("xb")
            self.compressed = gzip.GzipFile(fileobj=self.file, mode="wb", mtime=0)
            self.started = record["offset_s"]
            self.count = 0
        self.compressed.write((json.dumps(record, allow_nan=False) + "\n").encode("utf-8"))
        self.count += 1

    def flush(self) -> None:
        if self.file is not None:
            self.compressed.flush()
            self.file.flush()
            os.fsync(self.file.fileno())

    def finish(self, *, complete: bool) -> None:
        if self.file is None:
            return
        self.compressed.close()
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()
        self.file = None
        self.compressed = None
        if complete:
            final = self.path.with_suffix("")
            self.path.rename(final)
            with final.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest = dict(path=final.name, samples=self.count, started_offset_s=self.started,
                            sha256=digest, closed_at=utc_now())
            with (self.run / "segments.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def snapshot_summary(snapshot) -> dict:
    fields = (
        "mVersion", "mSequenceNumber", "mGameState", "mSessionState", "mRaceState",
        "mPitMode", "mSpeed", "mRpm", "mThrottle", "mBrake", "mSteering", "mGear",
        "mFuelLevel", "mFuelCapacity", "mFuelPressureKPa", "mLapInvalidated",
    )
    result = {}
    for field in fields:
        value = getattr(snapshot, field)
        result[field] = value if not isinstance(value, float) or math.isfinite(value) else None
    index = int(snapshot.mViewedParticipantIndex)
    if 0 <= index < min(int(snapshot.mNumParticipants), len(snapshot.mParticipantInfo)):
        participant = snapshot.mParticipantInfo[index]
        distance = float(participant.mCurrentLapDistance)
        result["mCurrentLap"] = int(participant.mCurrentLap)
        result["mCurrentLapDistance"] = distance if math.isfinite(distance) else None
    for field in ("mTrackLocation", "mTrackVariation", "mCarName"):
        label = bytes(getattr(snapshot, field)).split(b"\0", 1)[0]
        result[field] = label.decode("utf-8", "replace")
    return result


def watch(run: Path, source, layout, *, hz: float = 60, segment_seconds: float = 60,
          monotonic=time.monotonic, sleep=time.sleep, free_bytes=None) -> int:
    start = last_sequence_at = last_heartbeat = monotonic()
    sequence = None
    coverage = None
    count = 0
    latest = None
    status = "running"
    error = None
    previous_loop = start
    free_bytes = free_bytes or (lambda: shutil.disk_usage(run).free)
    archive = Segments(run, segment_seconds)
    events = (run / "events.jsonl").open("x", encoding="utf-8")

    def heartbeat(now: float) -> None:
        archive.flush()
        events.flush()
        os.fsync(events.fileno())
        write_status(run / "status.json", dict(
            schema_version=1, status=status, updated_at=utc_now(), offset_s=now - start,
            coverage=coverage, samples=count, target_hz=hz,
            latest=latest, latest_is_current=coverage == "available", error=error,
        ))

    def set_coverage(value: str, now: float) -> None:
        nonlocal coverage
        if value != coverage:
            events.write(json.dumps(dict(
                event="coverage", at=utc_now(), offset_s=now - start, previous=coverage,
                current=value,
            )) + "\n")
            events.flush()
            coverage = value

    try:
        heartbeat(start)
        while not (run / "stop").exists():
            loop = monotonic()
            if loop - previous_loop > 1:
                events.write(json.dumps(dict(event="sampling_delay", at=utc_now(),
                                             offset_s=loop - start,
                                             duration_s=loop - previous_loop)) + "\n")
            previous_loop = loop
            if free_bytes() < 1024 ** 3:
                set_coverage("disk_reserve", loop)
                heartbeat(loop)
                sleep(1)
                continue
            observed_coverage = "available"
            try:
                raw = source.read()
            except OSError:
                source.close()
                raw = None
                observed_coverage = "mapping_unavailable"
            else:
                if raw is None:
                    observed_coverage = "unstable_copy"
            if raw is not None:
                if len(raw) != ctypes.sizeof(layout):
                    observed_coverage = "unsupported_size"
                    source.close()
                elif struct.unpack_from("<I", raw)[0] != 14:
                    observed_coverage = "unsupported_version"
                    source.close()
                else:
                    snapshot = layout.from_buffer_copy(raw)
                    if snapshot.mSequenceNumber != sequence:
                        sequence = snapshot.mSequenceNumber
                        last_sequence_at = loop
                    if loop - last_sequence_at >= 2:
                        observed_coverage = "stale_mapping"
                        source.close()
                    else:
                        latest = snapshot_summary(snapshot)
                        archive.append(dict(
                            schema_version=1, at=utc_now(), offset_s=loop - start,
                            sequence=int(sequence), struct_size_bytes=len(raw),
                            summary=latest, raw_base64=base64.b64encode(raw).decode("ascii"),
                        ))
                        count += 1
            set_coverage(observed_coverage, loop)
            now = monotonic()
            if now - loop > max(1, 3 / hz):
                events.write(json.dumps(dict(event="sampling_delay", at=utc_now(),
                                             offset_s=now - start, duration_s=now - loop)) + "\n")
            if now - last_heartbeat >= 1:
                heartbeat(now)
                last_heartbeat = now
            sleep(max(0, 1 / hz - (monotonic() - loop)))
        status = "complete"
    except KeyboardInterrupt:
        status = "complete"
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        source.close()
        try:
            archive.finish(complete=status == "complete")
            set_coverage("stopped", monotonic())
            heartbeat(monotonic())
        finally:
            events.close()
    return 0 if status == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(".scratch/telemetry"))
    parser.add_argument("--hz", type=float, default=60)
    parser.add_argument("--stop", type=Path, metavar="RUN")
    args = parser.parse_args()
    if args.stop:
        if not (args.stop / "status.json").is_file():
            parser.error("Expected a telemetry archive run with status.json")
        (args.stop / "stop").touch()
        return 0
    if sys.platform != "win32":
        parser.error("Live AMS2 telemetry requires Windows")
    if not math.isfinite(args.hz) or not 1 <= args.hz <= 60:
        parser.error("--hz must be between 1 and 60")
    run = args.out / f"ams2-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    run.mkdir(parents=True)
    # Configure before importing the adapter, whose module initializes its logger.
    from simcoach.utils.llm_logger import configure_logging
    configure_logging(run / "logs")
    from simcoach.adapters.ams2 import SharedMemory
    print(f"Telemetry archive: {run.resolve()}", flush=True)
    write_status(run / "run.json", dict(
        schema_version=1, started_at=utc_now(), target_hz=args.hz, segment_seconds=60,
        shared_memory_name="$pcars2$", expected_version=14, pid=os.getpid(),
        struct_size_bytes=ctypes.sizeof(SharedMemory),
        sequence_offset=SharedMemory.mSequenceNumber.offset,
    ))
    try:
        source = ExistingMapping(ctypes.sizeof(SharedMemory), SharedMemory.mSequenceNumber.offset)
    except Exception as exc:
        write_status(run / "status.json", dict(
            schema_version=1, status="failed", updated_at=utc_now(), samples=0,
            error=f"{type(exc).__name__}: {exc}", latest=None, latest_is_current=False,
        ))
        return 1
    return watch(run, source, SharedMemory, hz=args.hz)


if __name__ == "__main__":
    raise SystemExit(main())
