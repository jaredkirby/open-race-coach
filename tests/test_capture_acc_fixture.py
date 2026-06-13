from __future__ import annotations

import ctypes
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.capture_acc_fixture import build_metadata
from simcoach.adapters.acc import ACCGraphics, ACCPhysics, ACCStatic


def test_build_metadata_summarizes_raw_acc_fixture_pages() -> None:
    physics = ACCPhysics()
    physics.packet_id = 10
    physics.gas = 0.8
    physics.brake = 0.1
    physics.gear = 4
    physics.rpm = 7100
    physics.speed_kmh = 180.0
    physics.tyre_contact_point[0][0] = 1.0
    physics.tyre_contact_point[0][1] = 2.0
    physics.tyre_contact_point[0][2] = 3.0

    graphics = ACCGraphics()
    graphics.packet_id = 11
    graphics.status = 2
    graphics.session_type = 0
    graphics.completed_laps = 3
    graphics.normalized_car_position = 0.42
    graphics.is_valid_lap = 1

    static = ACCStatic()
    write_utf16(static.sm_version, "1.0")
    write_utf16(static.ac_version, "1.9")
    write_utf16(static.car_model, "ferrari_488_gt3_evo")
    write_utf16(static.track, "spa")
    static.sector_count = 3
    static.max_rpm = 8000

    metadata = build_metadata(
        {
            "physics": bytes(physics),
            "graphics": bytes(graphics),
            "static": bytes(static),
        },
        {
            "physics": Path("sample_physics.bin"),
            "graphics": Path("sample_graphics.bin"),
            "static": Path("sample_static.bin"),
        },
        datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )

    assert metadata["fixture_schema_version"] == 1
    assert metadata["sim"] == "acc"
    assert len(metadata["combined_sha256"]) == 64
    assert metadata["pages"]["physics"]["struct_size_bytes"] == ctypes.sizeof(ACCPhysics)
    assert metadata["pages"]["graphics"]["raw_path"] == "sample_graphics.bin"
    assert metadata["pages"]["static"]["struct"] == "simcoach.adapters.acc.ACCStatic"
    assert metadata["snapshot"]["physics"]["speed_kmh"] == 180.0
    assert metadata["snapshot"]["graphics"]["normalized_car_position"] == pytest.approx(0.42)
    assert metadata["snapshot"]["static"]["car_model"] == "ferrari_488_gt3_evo"
    assert metadata["snapshot"]["static"]["track"] == "spa"


def write_utf16(target: ctypes.Array[ctypes.c_uint16], value: str) -> None:
    encoded = value.encode("utf-16-le")
    for index in range(0, min(len(encoded), (len(target) - 1) * 2), 2):
        target[index // 2] = int.from_bytes(encoded[index : index + 2], "little")
