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

from simcoach.adapters.ams2_mapping import ExistingMapping

DISK_RESERVE_BYTES = 1024 ** 3
DISK_CHECK_INTERVAL_S = 1
SOURCE_GAP_S = 2
MAPPING_GAPS = {"mapping_unavailable", "stale_mapping", "unsupported_size", "unsupported_version"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_status(path: Path, value: dict, *, temporary: Path | None = None) -> None:
    """Publish for a single writer, tolerating brief Windows reader contention.

    Only replacement retries: at most five 10 ms waits plus filesystem time.
    Exhaustion preserves the old destination and flushed temporary for inspection.
    This is not a hard I/O deadline or coordination between multiple writers.
    An explicit temporary is created exclusively and is never overwritten.
    """
    supplied_temporary = temporary is not None
    temporary = temporary if supplied_temporary else path.with_suffix(".tmp")
    with temporary.open("x" if supplied_temporary else "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5:
                raise
            time.sleep(0.01)


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

    def finish(self, *, complete: bool, continuity_break: bool = False) -> None:
        if self.file is None:
            return
        file, compressed = self.file, self.compressed
        # The closing attempt owns this handle. Cleanup must not retry an
        # uncertain gzip finish or publish a failed segment as complete.
        self.file = None
        self.compressed = None
        with file:
            compressed.close()
            file.flush()
            os.fsync(file.fileno())
        if complete:
            final = self.path.with_suffix("")
            if final.exists():
                raise FileExistsError(final)
            self.path.rename(final)
            with final.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest = dict(path=final.name, samples=self.count, started_offset_s=self.started,
                            sha256=digest, closed_at=utc_now())
            if continuity_break:
                manifest["continuity_break_after"] = True
            with (self.run / "segments.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def snapshot_summary(snapshot) -> dict:
    fields = (
        "mVersion", "mSequenceNumber", "mGameState", "mSessionState", "mRaceState",
        "mPitMode", "mSpeed", "mRpm", "mThrottle", "mBrake", "mSteering", "mGear",
        "mFuelLevel", "mFuelCapacity", "mFuelPressureKPa", "mLapInvalidated",
        "mLastLapTime", "mBestLapTime", "mCurrentTime",
    )
    result = {}
    for field in fields:
        value = getattr(snapshot, field)
        result[field] = value if not isinstance(value, float) or math.isfinite(value) else None
    index = int(snapshot.mViewedParticipantIndex)
    count = int(snapshot.mNumParticipants)
    # Slot evidence only: the SDK does not identify a local player here.
    # Never include participant names in the operational summary.
    view = dict(schema_version=1, index=index, count=count, active=None,
                car=None, race_state=None, pit_mode=None)
    result["participant_observation"] = view
    if 0 <= index < min(int(snapshot.mNumParticipants), len(snapshot.mParticipantInfo)):
        participant = snapshot.mParticipantInfo[index]
        car = bytes(snapshot.mCarNames[index]).split(b"\0", 1)[0].decode("utf-8", "replace")
        view.update(active=bool(participant.mIsActive), car=car,
                    race_state=int(snapshot.mRaceStates[index]),
                    pit_mode=int(snapshot.mPitModes[index]))
        distance = float(participant.mCurrentLapDistance)
        result["mCurrentLap"] = int(participant.mCurrentLap)
        result["mCurrentLapDistance"] = distance if math.isfinite(distance) else None
    for field in ("mTrackLocation", "mTrackVariation", "mCarName"):
        label = bytes(getattr(snapshot, field)).split(b"\0", 1)[0]
        result[field] = label.decode("utf-8", "replace")
    return result


def watch(run: Path, source, layout, *, hz: float = 60, segment_seconds: float = 60,
          monotonic=None, sleep=time.sleep, free_bytes=None,
          status_publisher=None, publication_timeout_s=2.0) -> int:
    from simcoach.live.archive_status import ArchiveStatusPublisher

    # Older Windows Python builds use a coarse clock for time.monotonic().
    # Preserve real observation spacing and scheduling at the 60 Hz target.
    monotonic = time.perf_counter if monotonic is None else monotonic
    start = last_sequence_at = last_heartbeat = monotonic()
    sequence = None
    coverage = None
    count = 0
    continuity_generation = 0
    continuity_interrupted = False
    latest = None
    status = "running"
    error = None
    previous_loop = start
    free_bytes = free_bytes or (lambda: shutil.disk_usage(run).free)
    archive = Segments(run, segment_seconds)
    events = (run / "events.jsonl").open("x", encoding="utf-8")
    publisher = status_publisher or ArchiveStatusPublisher(run / "status.json")
    observed_at, observed_offset = utc_now(), 0.0
    publication_failures = 0
    publication_confirmed = False
    cleanup_errors = []
    last_sample_offset = None
    disk_reason = None
    next_disk_check = start

    def fail_cleanup(exc, phase):
        nonlocal status, error
        status = "failed"
        error = error or f"{type(exc).__name__}: {exc}"
        cleanup_errors.append(dict(phase=phase, error_type=type(exc).__name__))

    def publication_diagnostics():
        nonlocal publication_failures
        diagnostic = publisher.snapshot()
        if diagnostic["failures"] != publication_failures:
            events.write(json.dumps(dict(event="status_publication_failure", at=utc_now(),
                                         diagnostic=diagnostic)) + "\n")
            publication_failures = diagnostic["failures"]

    def status_snapshot():
        return dict(
            schema_version=1, status=status, updated_at=observed_at, offset_s=observed_offset,
            coverage=coverage, samples=count, target_hz=hz,
            continuity_generation=continuity_generation,
            latest=latest, latest_is_current=status == "running" and coverage == "available",
            error=error,
            cleanup_errors=list(cleanup_errors),
        )

    def heartbeat(now: float) -> None:
        # Bind source time before filesystem flush/publication, not after a delay.
        value = status_snapshot()
        publication_diagnostics()
        archive.flush()
        events.flush()
        os.fsync(events.fileno())
        publisher.submit(value)

    def break_continuity(reason: str) -> None:
        nonlocal continuity_generation, continuity_interrupted
        if count and not continuity_interrupted:
            continuity_generation += 1
            continuity_interrupted = True
            events.write(json.dumps(dict(
                event="continuity_break", at=observed_at, offset_s=observed_offset,
                generation=continuity_generation, reason=reason,
            )) + "\n")

    def set_coverage(value: str, now: float) -> None:
        nonlocal coverage
        if value != coverage:
            if status == "running" and coverage == "available":
                # Invalidate cached telemetry before potentially slow raw
                # finalization. The publisher still owns status I/O and retry.
                publisher.submit(dict(status_snapshot(), coverage=value, latest_is_current=False))
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
            if loop >= next_disk_check:
                next_disk_check = loop + DISK_CHECK_INTERVAL_S
                try:
                    remaining = free_bytes()
                    if type(remaining) is not int or remaining < 0:
                        raise ValueError("invalid_disk_capacity")
                except (OSError, ValueError):
                    disk_reason = "disk_probe_unavailable"
                else:
                    disk_reason = "disk_reserve" if remaining < DISK_RESERVE_BYTES else None
            if disk_reason:
                observed_at, observed_offset = utc_now(), monotonic() - start
                entered_hold = coverage not in {"disk_reserve", "disk_probe_unavailable"}
                break_continuity(disk_reason)
                set_coverage(disk_reason, loop)
                archive.finish(complete=True, continuity_break=True)
                if entered_hold:
                    source.close()
                heartbeat(loop)
                last_heartbeat = monotonic()
                sleep(DISK_CHECK_INTERVAL_S)
                continue
            generation_before = continuity_generation
            resuming_after_break = continuity_interrupted
            observed_coverage = "available"
            try:
                raw = source.read()
            except OSError:
                raw = None
                observed_coverage = "mapping_unavailable"
            else:
                if raw is None:
                    observed_coverage = "unstable_copy"
            observed_at, observed_offset = utc_now(), monotonic() - start
            long_gap = (last_sample_offset is not None
                        and observed_offset - last_sample_offset >= SOURCE_GAP_S)
            if raw is not None:
                if len(raw) != ctypes.sizeof(layout):
                    observed_coverage = "unsupported_size"
                elif struct.unpack_from("<I", raw)[0] != 14:
                    observed_coverage = "unsupported_version"
                else:
                    snapshot = layout.from_buffer_copy(raw)
                    regressed = sequence is not None and snapshot.mSequenceNumber < sequence
                    if snapshot.mSequenceNumber != sequence:
                        sequence = snapshot.mSequenceNumber
                        last_sequence_at = loop
                    if loop - last_sequence_at >= SOURCE_GAP_S:
                        observed_coverage = "stale_mapping"
                    else:
                        if regressed or long_gap:
                            reason = "sequence_regressed" if regressed else "accepted_sample_gap"
                            break_continuity(reason)
                            archive.finish(complete=True, continuity_break=True)
                        latest = snapshot_summary(snapshot)
                        archive.append(dict(
                            schema_version=1, at=observed_at, offset_s=observed_offset,
                            sequence=int(sequence), struct_size_bytes=len(raw),
                            summary=latest, raw_base64=base64.b64encode(raw).decode("ascii"),
                        ))
                        count += 1
                        last_sample_offset = observed_offset
                        continuity_interrupted = False
            unstable_timeout = (observed_coverage == "unstable_copy"
                                and loop - last_sequence_at >= SOURCE_GAP_S)
            release_mapping = observed_coverage in MAPPING_GAPS or unstable_timeout
            if release_mapping:
                break_continuity(observed_coverage)
            recovered_copy = coverage == "unstable_copy" and observed_coverage == "available"
            set_coverage(observed_coverage, loop)
            if release_mapping:
                archive.finish(complete=True, continuity_break=True)
                source.close()
            elif observed_coverage == "available" and (
                    recovered_copy or resuming_after_break
                    or continuity_generation != generation_before):
                publisher.submit(status_snapshot())
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
        observed_at, observed_offset = utc_now(), monotonic() - start
        try:
            source.close()
        except Exception as exc:
            fail_cleanup(exc, "source_close")
        try:
            archive.finish(complete=status == "complete")
        except Exception as exc:
            fail_cleanup(exc, "archive_finish")
        try:
            set_coverage("stopped", monotonic())
            publication_diagnostics()
            events.flush()
            os.fsync(events.fileno())
        except Exception as exc:
            fail_cleanup(exc, "event_finish")
        terminal_generation = None
        try:
            terminal_generation = publisher.submit(status_snapshot())
        except Exception as exc:
            fail_cleanup(exc, "terminal_submit")
        closed = publisher.close(timeout_s=publication_timeout_s)
        diagnostic = publisher.snapshot()
        publication_confirmed = (closed and terminal_generation is not None
                                 and diagnostic["confirmed_sequence"] == terminal_generation)
        try:
            publication_diagnostics()
            events.write(json.dumps(dict(event="status_publication_closed", at=utc_now(),
                                         terminal_confirmed=publication_confirmed,
                                         terminal_sequence=terminal_generation,
                                         capture_status=status, diagnostic=diagnostic)) + "\n")
            events.flush()
            os.fsync(events.fileno())
        except Exception as exc:
            fail_cleanup(exc, "terminal_receipt")
        finally:
            try:
                events.close()
            except Exception as exc:
                fail_cleanup(exc, "events_close")
    return 0 if status == "complete" and publication_confirmed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(".scratch/telemetry"))
    parser.add_argument("--hz", type=float, default=60)
    parser.add_argument("--stop", type=Path, metavar="RUN")
    args = parser.parse_args(argv)
    if args.stop:
        try:
            identity = json.loads((args.stop / "run.json").read_text(encoding="utf-8"))
            recognized = (type(identity.get("schema_version")) is int
                          and identity["schema_version"] == 1
                          and identity.get("shared_memory_name") == "$pcars2$"
                          and identity.get("expected_version") == 14)
        except (OSError, ValueError, AttributeError):
            recognized = False
        if not recognized:
            parser.error("Expected a recognized telemetry archive run.json")
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
    from simcoach.live.collector_release import runtime_provenance
    print(f"Telemetry archive: {run.resolve()}", flush=True)
    write_status(run / "run.json", dict(
        schema_version=1, started_at=utc_now(), target_hz=args.hz, segment_seconds=60,
        shared_memory_name="$pcars2$", expected_version=14, pid=os.getpid(),
        status_publication_mode="isolated_thread_v1",
        struct_size_bytes=ctypes.sizeof(SharedMemory),
        sequence_offset=SharedMemory.mSequenceNumber.offset,
        runtime=runtime_provenance(),
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
