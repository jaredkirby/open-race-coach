from __future__ import annotations

import base64
import ctypes
import gzip
import hashlib
import json

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli.telemetry_watch import ExistingMapping, Segments, watch
from simcoach.live.archive_status import ArchiveStatusPublisher


def windows_access_error(code):
    error = PermissionError("synthetic Windows replacement contention")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32])
def test_status_publication_retries_only_replace_with_same_flushed_temp(tmp_path, monkeypatch,
                                                                     code):
    from simcoach.cli import telemetry_watch as module

    path = tmp_path / "status.json"
    path.write_text("original", encoding="utf-8")
    original_replace, original_fsync = module.os.replace, module.os.fsync
    replacements, flushes, sleeps = [], [], []

    def fsync(handle):
        flushes.append(handle)
        original_fsync(handle)

    def replace(source, target):
        replacements.append((source, target, source.read_bytes(), source.stat().st_mtime_ns))
        assert target.read_text() == "original"
        if len(replacements) < 3:
            raise windows_access_error(code)
        original_replace(source, target)

    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    module.write_status(path, {"result": "new"})
    assert json.loads(path.read_text()) == {"result": "new"}
    assert len(flushes) == 1 and sleeps == [0.01, 0.01]
    assert replacements[0] == replacements[1] == replacements[2]
    assert not path.with_suffix(".tmp").exists()


def test_status_publication_exhaustion_retains_destination_and_failed_temp(tmp_path, monkeypatch):
    from simcoach.cli import telemetry_watch as module

    path = tmp_path / "status.json"
    path.write_bytes(b"old immutable status")
    calls, sleeps = [], []
    failure = windows_access_error(5)

    def replace(source, target):
        calls.append(source.read_bytes())
        raise failure

    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        module.write_status(path, {"result": "failed publication"})
    assert caught.value is failure
    assert len(calls) == 6 and len(set(calls)) == 1 and sleeps == [0.01] * 5
    assert path.read_bytes() == b"old immutable status"
    assert json.loads(path.with_suffix(".tmp").read_text()) == {"result": "failed publication"}


@pytest.mark.parametrize("failure", [OSError("unrelated I/O failure"), windows_access_error(13),
                                     PermissionError("non-Windows permission failure")])
def test_status_publication_unrelated_errors_raise_immediately(tmp_path, monkeypatch, failure):
    from simcoach.cli import telemetry_watch as module

    calls, sleeps = [], []

    def replace(*args):
        calls.append(args)
        raise failure

    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    with pytest.raises(OSError) as caught:
        module.write_status(tmp_path / "status.json", {"new": True})
    assert caught.value is failure and len(calls) == 1 and sleeps == []


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


def test_mapping_loss_seals_prefix_before_next_available_page(tmp_path):
    clock = Clock(tmp_path, 4)
    first = [sample(2), sample(4)]
    publisher = ArchiveStatusPublisher(tmp_path / "status.json")
    submit = publisher.submit
    early_status = []

    def observe(status):
        if status["coverage"] == "mapping_unavailable" and not early_status:
            early_status.append(status)
            assert not (tmp_path / "segments.jsonl").exists()
        return submit(status)

    publisher.submit = observe

    class Source:
        def read(self):
            if clock.now < 1:
                return first[int(clock.now * 2)]
            if clock.now == 1:
                raise OSError("generated mapping loss")
            rows = (tmp_path / "segments.jsonl").read_text(encoding="utf-8").splitlines()
            assert len(rows) == 1
            entry = json.loads(rows[0])
            assert entry["continuity_break_after"] is True
            with gzip.open(tmp_path / entry["path"], "rt", encoding="utf-8") as stream:
                raw = [base64.b64decode(json.loads(line)["raw_base64"]) for line in stream]
                assert raw == first
            return sample(6)

        def close(self):
            pass

    assert watch(tmp_path, Source(), SharedMemory, hz=2, segment_seconds=60,
                 monotonic=clock.read, sleep=clock.sleep, status_publisher=publisher) == 0
    assert len(early_status) == 1 and early_status[0]["latest_is_current"] is False
    rows = [json.loads(line) for line in (tmp_path / "segments.jsonl").read_text().splitlines()]
    assert [row["samples"] for row in rows] == [2, 1]
    assert "continuity_break_after" not in rows[1]


@pytest.mark.parametrize("missing", [1, 4])
def test_unstable_samples_seal_only_after_sustained_gap(tmp_path, missing):
    clock = Clock(tmp_path, missing + 2)
    source = Source([sample(2), *([None] * missing), sample(4)])
    assert watch(tmp_path, source, SharedMemory, hz=2, segment_seconds=60,
                 monotonic=clock.read, sleep=clock.sleep) == 0
    rows = [json.loads(line) for line in (tmp_path / "segments.jsonl").read_text().splitlines()]
    if missing == 1:
        assert len(rows) == 1 and "continuity_break_after" not in rows[0]
    else:
        assert len(rows) == 2 and rows[0]["continuity_break_after"]


@pytest.mark.parametrize("reason", ["disk_reserve", "disk_probe_unavailable"])
def test_disk_hold_retains_first_prefix_and_resumes_in_same_run(tmp_path, reason):
    clock = Clock(tmp_path, 5)
    source = Source([sample(sequence) for sequence in (2, 4, 6, 8)])
    reads = []

    def capacity():
        reads.append(clock.now)
        if clock.now == 1:
            if reason == "disk_probe_unavailable":
                raise PermissionError("generated probe failure")
            return 0
        if clock.now == 2:
            entry = json.loads((tmp_path / "segments.jsonl").read_text())
            assert entry["samples"] == 2 and entry["continuity_break_after"] is True
        return 2 * 1024 ** 3

    assert watch(tmp_path, source, SharedMemory, hz=2, segment_seconds=60,
                 monotonic=clock.read, sleep=clock.sleep, free_bytes=capacity) == 0
    assert reads == [0, 1, 2]
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["current"] for e in events if e["event"] == "coverage"] == [
        "available", reason, "available", "stopped",
    ]
    assert json.loads((tmp_path / "status.json").read_text())["samples"] == 4


def test_failed_segment_finish_retains_partial_and_is_never_retried(tmp_path, monkeypatch):
    from simcoach.cli import telemetry_watch

    writer = Segments(tmp_path, 60)
    writer.append({"offset_s": 0})
    calls = []

    def fail(handle):
        calls.append(handle)
        raise OSError("generated segment sync failure")

    monkeypatch.setattr(telemetry_watch.os, "fsync", fail)
    with pytest.raises(OSError, match="generated segment sync failure"):
        writer.finish(complete=True, continuity_break=True)
    writer.finish(complete=True)
    assert len(calls) == 1
    assert list(tmp_path.glob("*.partial")) and not (tmp_path / "segments.jsonl").exists()


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
        sequence.value += 2
        return raw

    monkeypatch.setattr(ctypes, "string_at", copy_then_change)
    assert mapping.read() is None

    # Matching before/after counters still cannot admit a different copied counter.
    monkeypatch.setattr(ctypes, "string_at", lambda *_: sample(12))
    assert sequence.value == 8
    assert mapping.read() is None

    monkeypatch.setattr(ctypes, "string_at", original)
    sequence.value = 9
    monkeypatch.setattr("simcoach.adapters.ams2_mapping.time.sleep",
                        lambda _: setattr(sequence, "value", 10))
    assert mapping.read() == sample(10)


def test_segment_writer_never_overwrites_an_existing_segment(tmp_path):
    existing = tmp_path / "samples-000001.jsonl.gz.partial"
    existing.write_bytes(b"existing evidence")
    writer = Segments(tmp_path, 60)
    with pytest.raises(FileExistsError):
        writer.append({"offset_s": 0})
    assert existing.read_bytes() == b"existing evidence"

def test_frozen_odd_copy_releases_mapping_for_publisher_replacement(tmp_path):
    values = [sample(), None, None, None, None, sample(10)]
    code, status, events, source = run_watch(tmp_path, values)
    assert code == 0 and status["samples"] == 2
    assert source.closed == 2  # Released the pinned unstable view, then final cleanup.
    assert [e["current"] for e in events if e["event"] == "coverage"] == [
        "available", "unstable_copy", "available", "stopped",
    ]
    first = json.loads((tmp_path / "segments.jsonl").read_text().splitlines()[0])
    assert first["continuity_break_after"] is True

def test_healthy_heartbeat_retains_gap_generation_after_reacquisition(tmp_path):
    values = [sample(10), OSError("disposable mapping gone"), sample(14), sample(16)]
    code, status, events, _ = run_watch(tmp_path, values)
    assert code == 0 and status["samples"] == 3
    assert status["continuity_generation"] == 1
    breaks = [event for event in events if event["event"] == "continuity_break"]
    assert len(breaks) == 1 and breaks[0]["reason"] == "mapping_unavailable"

def test_collector_marks_a_counter_regression_without_waiting_for_a_missing_mapping(tmp_path):
    code, status, events, _ = run_watch(tmp_path, [sample(20), sample(22), sample(4), sample(6)])
    assert code == 0 and status["continuity_generation"] == 1
    assert [event["reason"] for event in events if event["event"] == "continuity_break"] == [
        "sequence_regressed",
    ]
    segments = [json.loads(line) for line in (tmp_path / "segments.jsonl").read_text().splitlines()]
    assert segments[0]["continuity_break_after"] is True
    assert [segment["samples"] for segment in segments] == [2, 2]


def test_brief_unstable_copy_preserves_generation(tmp_path):
    code, status, events, _ = run_watch(tmp_path, [sample(2), None, sample(4)])
    assert code == 0 and status["continuity_generation"] == 0
    assert not any(event["event"] == "continuity_break" for event in events)


def test_recovered_page_reaches_mailbox_before_the_next_periodic_heartbeat(tmp_path):
    clock = Clock(tmp_path, 3)
    publisher = ArchiveStatusPublisher(tmp_path / "status.json")
    submitted = []
    submit = publisher.submit

    def trace(value):
        submitted.append(value)
        return submit(value)

    publisher.submit = trace
    assert watch(tmp_path, Source([sample(2), None, sample(4)]), SharedMemory, hz=20,
                 monotonic=clock.read, sleep=clock.sleep, status_publisher=publisher) == 0
    assert any(row["coverage"] == "unstable_copy" and not row["latest_is_current"]
               for row in submitted)
    recovered, = [row for row in submitted if row["latest_is_current"]]
    assert recovered["latest"]["mSequenceNumber"] == 4
    assert recovered["continuity_generation"] == 0 and recovered["offset_s"] < 1
