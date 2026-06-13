#!/usr/bin/env python3
"""Capture raw AMS2/PC2 shared-memory bytes for adapter fixtures."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import mmap
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from simcoach.adapters.ams2 import (
    SHARED_MEMORY_NAME,
    SHARED_MEMORY_VERSION,
    SharedMemory,
    decode_c_string,
    important_offsets,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/ams2"))
    parser.add_argument("--prefix", default="ams2_pc2")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval-s", type=float, default=0.25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("--count must be >= 1")
    if args.interval_s < 0:
        raise SystemExit("--interval-s must be >= 0")
    if sys.platform != "win32":
        raise SystemExit("AMS2 raw fixture capture requires Windows shared memory")

    args.out.mkdir(parents=True, exist_ok=True)
    size = ctypes.sizeof(SharedMemory)
    with mmap.mmap(
        -1,
        size,
        tagname=SHARED_MEMORY_NAME,
        access=mmap.ACCESS_READ,
    ) as page:
        for index in range(args.count):
            raw = read_raw_snapshot(page, size)
            captured_at = datetime.now().astimezone()
            stem = f"{args.prefix}_{captured_at.strftime('%Y%m%d_%H%M%S_%f')}_{index:03d}"
            bin_path = args.out / f"{stem}.bin"
            json_path = args.out / f"{stem}.json"
            write_bytes_atomic(bin_path, raw)
            metadata = build_metadata(raw, bin_path, captured_at)
            write_json_atomic(json_path, metadata)
            print(f"{bin_path} {metadata['sha256']}")
            if index < args.count - 1:
                time.sleep(args.interval_s)
    return 0


def read_raw_snapshot(page: mmap.mmap, size: int) -> bytes:
    page.seek(0)
    raw = page.read(size)
    if len(raw) != size:
        raise RuntimeError(f"short shared-memory read: expected={size} actual={len(raw)}")
    return raw


def build_metadata(raw: bytes, bin_path: Path, captured_at: datetime) -> dict[str, Any]:
    snapshot = SharedMemory.from_buffer_copy(raw)
    participant = snapshot.mParticipantInfo[int(snapshot.mViewedParticipantIndex)]
    return {
        "fixture_schema_version": 1,
        "sim": "ams2",
        "shared_memory_name": SHARED_MEMORY_NAME,
        "struct": "simcoach.adapters.ams2.SharedMemory",
        "struct_size_bytes": ctypes.sizeof(SharedMemory),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "captured_at": captured_at.isoformat(),
        "raw_path": bin_path.name,
        "adapter_shared_memory_version_expected": SHARED_MEMORY_VERSION,
        "snapshot": {
            "mVersion": int(snapshot.mVersion),
            "mBuildVersionNumber": int(snapshot.mBuildVersionNumber),
            "mSequenceNumber": int(snapshot.mSequenceNumber),
            "sequence_stable": int(snapshot.mSequenceNumber) % 2 == 0,
            "mGameState": int(snapshot.mGameState),
            "mSessionState": int(snapshot.mSessionState),
            "mRaceState": int(snapshot.mRaceState),
            "mViewedParticipantIndex": int(snapshot.mViewedParticipantIndex),
            "mNumParticipants": int(snapshot.mNumParticipants),
            "mTrackLocation": decode_c_string(snapshot.mTrackLocation),
            "mTrackVariation": decode_c_string(snapshot.mTrackVariation),
            "mCarName": decode_c_string(snapshot.mCarName),
            "mTrackLength": float(snapshot.mTrackLength),
            "mLapInvalidated": bool(snapshot.mLapInvalidated),
            "mSpeed": float(snapshot.mSpeed),
            "mRpm": float(snapshot.mRpm),
            "mBrake": float(snapshot.mBrake),
            "mThrottle": float(snapshot.mThrottle),
            "mSteering": float(snapshot.mSteering),
            "mGear": int(snapshot.mGear),
            "participant": {
                "mIsActive": bool(participant.mIsActive),
                "mCurrentLap": int(participant.mCurrentLap),
                "mCurrentLapDistance": float(participant.mCurrentLapDistance),
                "mRacePosition": int(participant.mRacePosition),
                "mWorldPosition": [float(value) for value in participant.mWorldPosition],
            },
        },
        "important_offsets": important_offsets(),
        "notes": (
            "Raw bytes captured from the Windows $pcars2$ shared-memory page. "
            "Keep the .bin and .json sidecar together."
        ),
    }


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with temp_path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(content, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
