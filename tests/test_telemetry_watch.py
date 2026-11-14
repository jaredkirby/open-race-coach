from __future__ import annotations

import base64
import ctypes
import gzip
import hashlib
import json

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli.telemetry_watch import ExistingMapping, Segments, watch


def sample(sequence=2, *, version=14, game=2, fuel=0.25):
    snapshot = SharedMemory()
    snapshot.mVersion = version
    snapshot.mSequenceNumber = sequence
    snapshot.mGameState = game
    snapshot.mFuelLevel = fuel
    snapshot.mFuelCapacity = 33
    snapshot.mTrackLocation = b"BrandsHatch"
    snapshot.mCarName = b"Formula Trainer Advanced"
    return bytes(snapshot)


class Clock:
    def __init__(self, run, iterations):
        self.now = 0.0
        self.iterations = iterations
        self.run = run

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.iterations -= 1
        if self.iterations == 0:
            (self.run / "stop").touch()


class Source:
    def __init__(self, values):
        self.values = iter(values)
        self.closed = 0

    def read(self):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed += 1


def run_watch(tmp_path, values, **kwargs):
    clock = Clock(tmp_path, len(values))
    source = Source(values)
    code = watch(tmp_path, source, SharedMemory, hz=2, segment_seconds=1,
                 monotonic=clock.read, sleep=clock.sleep, **kwargs)
    status = json.loads((tmp_path / "status.json").read_text())
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    return code, status, events, source


def test_preserves_full_raw_fuel_and_menu_data_across_segments(tmp_path):
    values = [sample(2), sample(4, game=4), sample(6, fuel=0), sample(8)]
    code, status, _, _ = run_watch(tmp_path, values)
    assert code == 0 and status["status"] == "complete"
    assert status["samples"] == 4
    manifest = [json.loads(line) for line in (tmp_path / "segments.jsonl").read_text().splitlines()]
    assert len(manifest) == 2
    records = []
    for item in manifest:
        path = tmp_path / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            records.extend(json.loads(line) for line in handle)
    assert [base64.b64decode(record["raw_base64"]) for record in records] == values
    assert records[1]["summary"]["mGameState"] == 4
    assert records[2]["summary"]["mFuelLevel"] == 0
    assert records[2]["summary"]["mFuelCapacity"] == 33
    assert [record["offset_s"] for record in records] == [0, 0.5, 1, 1.5]
    assert all(record["at"].endswith("+00:00") for record in records)


def test_missing_simulator_and_new_version_are_gaps_then_recover(tmp_path):
    code, status, events, source = run_watch(
        tmp_path, [OSError("missing"), sample(version=15), None, sample(4)]
    )
    assert code == 0 and status["samples"] == 1
    assert source.closed >= 2
    assert [event["current"] for event in events if event["event"] == "coverage"] == [
        "mapping_unavailable", "unsupported_version", "unstable_copy", "available", "stopped",
    ]


def test_stale_mapping_reopens_and_new_sequence_recovers(tmp_path):
    code, status, events, source = run_watch(tmp_path, [sample()] * 6 + [sample(10)])
    assert code == 0
    assert source.closed == 3  # Two stale copies plus final cleanup.
    assert any(event.get("current") == "stale_mapping" for event in events)
    assert status["latest"]["mSequenceNumber"] == 10


def test_failure_preserves_partial_segment_and_records_failed_status(tmp_path):
    code, status, _, _ = run_watch(tmp_path, [sample(), RuntimeError("sample failure")])
    assert code == 1 and status["status"] == "failed"
    assert "sample failure" in status["error"]
    partials = list(tmp_path.glob("*.partial"))
    assert len(partials) == 1
    with gzip.open(partials[0], "rt", encoding="utf-8") as handle:
        assert base64.b64decode(json.loads(handle.readline())["raw_base64"]) == sample()
    assert not (tmp_path / "segments.jsonl").exists()


def test_disk_reserve_marks_gap_without_deleting_data(tmp_path):
    space = iter([0, 2 * 1024 ** 3])
    code, status, events, _ = run_watch(tmp_path, [sample(2), sample(4)],
                                        free_bytes=lambda: next(space))
    assert code == 0 and status["samples"] == 1
    assert events[0]["current"] == "disk_reserve"


def test_mapping_reader_rejects_odd_and_changed_sequence(monkeypatch):
    buffer = ctypes.create_string_buffer(sample())
    mapping = ExistingMapping.__new__(ExistingMapping)
    mapping.view = ctypes.addressof(buffer)
    mapping.size = ctypes.sizeof(SharedMemory)
    mapping.sequence_offset = SharedMemory.mSequenceNumber.offset
    assert mapping.read() == sample()
    sequence = ctypes.c_uint32.from_address(mapping.view + mapping.sequence_offset)
    sequence.value = 3
    assert mapping.read() is None
    sequence.value = 4
    original = ctypes.string_at

    def copy_then_change(address, size):
        raw = original(address, size)
        sequence.value = 6
        return raw

    monkeypatch.setattr(ctypes, "string_at", copy_then_change)
    assert mapping.read() is None


def test_segment_writer_never_overwrites_an_existing_segment(tmp_path):
    existing = tmp_path / "samples-000001.jsonl.gz.partial"
    existing.write_bytes(b"existing evidence")
    writer = Segments(tmp_path, 60)
    with pytest.raises(FileExistsError):
        writer.append({"offset_s": 0})
    assert existing.read_bytes() == b"existing evidence"
