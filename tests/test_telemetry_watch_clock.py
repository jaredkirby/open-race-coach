"""A slow copy must not collapse successive archive observation times."""
import base64
import gzip
import json
import math

import pytest

from simcoach.adapters.ams2 import SharedMemory
from simcoach.cli import telemetry_watch


def capture_with_source_delay(tmp_path, monkeypatch, *, quantized=False):
    # 16.2 ms of source work leaves a short sleep at 60 Hz. A 15.625 ms
    # quantized clock can give this sample and the next one the same offset.
    elapsed = 0.0
    payloads = []

    class Source:
        def read(self):
            nonlocal elapsed
            if not payloads:
                elapsed += 0.0162
            page = SharedMemory()
            page.mVersion = 14
            page.mSequenceNumber = 2 * (len(payloads) + 1)
            payloads.append(bytes(page))
            return payloads[-1]

        def close(self):
            pass

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds
        if len(payloads) == 3:
            (tmp_path / "stop").touch()

    monkeypatch.setattr(telemetry_watch.time, "perf_counter", lambda: elapsed)
    options = {"monotonic": lambda: math.floor(elapsed * 64) / 64} if quantized else {}
    assert telemetry_watch.watch(tmp_path, Source(), SharedMemory, hz=60,
                                 sleep=sleep, free_bytes=lambda: 2 * 1024 ** 3,
                                 **options) == 0
    manifest = json.loads((tmp_path / "segments.jsonl").read_text())
    with gzip.open(tmp_path / manifest["path"], "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == len(payloads) == 3
    assert [base64.b64decode(row["raw_base64"]) for row in rows] == payloads
    return rows


def test_default_archive_clock_retains_sub_tick_observation_spacing(tmp_path, monkeypatch):
    rows = capture_with_source_delay(tmp_path, monkeypatch)
    assert [row["offset_s"] for row in rows] == pytest.approx([0.0162, 1 / 60, 2 / 60])
    assert all(b["offset_s"] > a["offset_s"] for a, b in zip(rows, rows[1:], strict=False))


def test_injected_coarse_clock_retains_original_offsets_without_fabrication(tmp_path, monkeypatch):
    rows = capture_with_source_delay(tmp_path, monkeypatch, quantized=True)
    assert [row["offset_s"] for row in rows] == [1 / 64, 1 / 64, 2 / 64]
