from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from simcoach.adapters.acc import ACCAdapter, ACCGraphics, ACCPhysics, ACCStatic
from simcoach.adapters.ams2 import (
    SHARED_MEMORY_VERSION,
    AMS2Adapter,
    SharedMemory,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_raw_ams2_fixture_sidecars_parse_when_present() -> None:
    sidecars = sorted((FIXTURE_ROOT / "ams2").glob("*.json"))
    if not sidecars:
        pytest.skip("no raw AMS2 fixture sidecars committed yet")

    for sidecar_path in sidecars:
        metadata = read_json(sidecar_path)
        raw_path = sidecar_path.parent / metadata["raw_path"]
        raw = raw_path.read_bytes()
        assert len(raw) == ctypes.sizeof(SharedMemory)
        assert len(raw) == metadata["struct_size_bytes"]
        assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
        assert metadata["adapter_shared_memory_version_expected"] == SHARED_MEMORY_VERSION

        snapshot = SharedMemory.from_buffer_copy(raw)
        assert int(snapshot.mVersion) == metadata["snapshot"]["mVersion"]
        assert int(snapshot.mSequenceNumber) == metadata["snapshot"]["mSequenceNumber"]

        info = AMS2Adapter.session_info_from_snapshot(snapshot)
        if metadata["snapshot"]["mTrackLocation"] and metadata["snapshot"]["mCarName"]:
            assert info is not None
            assert info["track_raw"].startswith(metadata["snapshot"]["mTrackLocation"])
            assert info["car_raw"] == metadata["snapshot"]["mCarName"]

        participant = metadata["snapshot"]["participant"]
        if participant["mIsActive"] and metadata["snapshot"]["mTrackLength"] > 0:
            tick = AMS2Adapter.tick_from_snapshot(snapshot, capture_t=0.0)
            assert tick is not None
            assert tick.speed == pytest.approx(metadata["snapshot"]["mSpeed"])
            assert tick.rpm == pytest.approx(metadata["snapshot"]["mRpm"])
            assert tick.gear == metadata["snapshot"]["mGear"]


def test_raw_acc_fixture_sidecars_parse_when_present() -> None:
    sidecars = sorted((FIXTURE_ROOT / "acc").glob("*.json"))
    if not sidecars:
        pytest.skip("no raw ACC fixture sidecars committed yet")

    for sidecar_path in sidecars:
        metadata = read_json(sidecar_path)
        raw_pages = {
            name: read_page_bytes(sidecar_path.parent, metadata, name)
            for name in ("physics", "graphics", "static")
        }
        combined = raw_pages["physics"] + raw_pages["graphics"] + raw_pages["static"]
        assert hashlib.sha256(combined).hexdigest() == metadata["combined_sha256"]

        physics = ACCPhysics.from_buffer_copy(raw_pages["physics"])
        graphics = ACCGraphics.from_buffer_copy(raw_pages["graphics"])
        static = ACCStatic.from_buffer_copy(raw_pages["static"])
        assert int(physics.packet_id) == metadata["snapshot"]["physics"]["packet_id"]
        assert int(graphics.packet_id) == metadata["snapshot"]["graphics"]["packet_id"]

        info = ACCAdapter.session_info_from_snapshots(graphics, static)
        if metadata["snapshot"]["static"]["track"] and metadata["snapshot"]["static"]["car_model"]:
            assert info is not None
            assert info["track_raw"] == metadata["snapshot"]["static"]["track"]
            assert info["car_raw"] == metadata["snapshot"]["static"]["car_model"]

        if 0.0 <= metadata["snapshot"]["graphics"]["normalized_car_position"] <= 1.0:
            tick = ACCAdapter.tick_from_snapshots(physics, graphics, capture_t=0.0)
            assert tick is not None
            assert tick.speed == pytest.approx(metadata["snapshot"]["physics"]["speed_kmh"] / 3.6)
            assert tick.rpm == pytest.approx(metadata["snapshot"]["physics"]["rpm"])


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_page_bytes(root: Path, metadata: dict[str, Any], name: str) -> bytes:
    page = metadata["pages"][name]
    raw = (root / page["raw_path"]).read_bytes()
    struct_type = {
        "physics": ACCPhysics,
        "graphics": ACCGraphics,
        "static": ACCStatic,
    }[name]
    assert len(raw) == ctypes.sizeof(struct_type)
    assert len(raw) == page["struct_size_bytes"]
    assert hashlib.sha256(raw).hexdigest() == page["sha256"]
    return raw
