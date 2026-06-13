#!/usr/bin/env python3
"""Capture raw ACC shared-memory page bytes for adapter fixtures."""

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

from simcoach.adapters.acc import (
    GRAPHICS_PAGE,
    PHYSICS_PAGE,
    STATIC_PAGE,
    ACCGraphics,
    ACCPhysics,
    ACCStatic,
    decode_utf16_array,
    important_offsets,
)

PAGE_SPECS: dict[str, tuple[str, type[ctypes.Structure]]] = {
    "physics": (PHYSICS_PAGE, ACCPhysics),
    "graphics": (GRAPHICS_PAGE, ACCGraphics),
    "static": (STATIC_PAGE, ACCStatic),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/acc"))
    parser.add_argument("--prefix", default="acc")
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
        raise SystemExit("ACC raw fixture capture requires Windows shared memory")

    args.out.mkdir(parents=True, exist_ok=True)
    pages = {
        name: mmap.mmap(
            -1,
            ctypes.sizeof(struct_type),
            tagname=page_name,
            access=mmap.ACCESS_READ,
        )
        for name, (page_name, struct_type) in PAGE_SPECS.items()
    }
    try:
        for index in range(args.count):
            captured_at = datetime.now().astimezone()
            stem = f"{args.prefix}_{captured_at.strftime('%Y%m%d_%H%M%S_%f')}_{index:03d}"
            raw_pages = {
                name: read_raw_page(pages[name], ctypes.sizeof(struct_type))
                for name, (_page_name, struct_type) in PAGE_SPECS.items()
            }
            raw_paths = {name: args.out / f"{stem}_{name}.bin" for name in PAGE_SPECS}
            for name, raw in raw_pages.items():
                write_bytes_atomic(raw_paths[name], raw)
            metadata = build_metadata(raw_pages, raw_paths, captured_at)
            json_path = args.out / f"{stem}.json"
            write_json_atomic(json_path, metadata)
            print(f"{json_path} {metadata['combined_sha256']}")
            if index < args.count - 1:
                time.sleep(args.interval_s)
    finally:
        for page in pages.values():
            page.close()
    return 0


def read_raw_page(page: mmap.mmap, size: int) -> bytes:
    page.seek(0)
    raw = page.read(size)
    if len(raw) != size:
        raise RuntimeError(f"short shared-memory read: expected={size} actual={len(raw)}")
    return raw


def build_metadata(
    raw_pages: dict[str, bytes],
    raw_paths: dict[str, Path],
    captured_at: datetime,
) -> dict[str, Any]:
    physics = ACCPhysics.from_buffer_copy(raw_pages["physics"])
    graphics = ACCGraphics.from_buffer_copy(raw_pages["graphics"])
    static = ACCStatic.from_buffer_copy(raw_pages["static"])
    return {
        "fixture_schema_version": 1,
        "sim": "acc",
        "captured_at": captured_at.isoformat(),
        "combined_sha256": hashlib.sha256(
            raw_pages["physics"] + raw_pages["graphics"] + raw_pages["static"]
        ).hexdigest(),
        "pages": {
            name: page_metadata(name, raw_pages[name], raw_paths[name])
            for name in ("physics", "graphics", "static")
        },
        "snapshot": {
            "physics": {
                "packet_id": int(physics.packet_id),
                "gas": float(physics.gas),
                "brake": float(physics.brake),
                "gear": int(physics.gear),
                "rpm": int(physics.rpm),
                "speed_kmh": float(physics.speed_kmh),
                "tyre_contact_point_0": [float(value) for value in physics.tyre_contact_point[0]],
            },
            "graphics": {
                "packet_id": int(graphics.packet_id),
                "status": int(graphics.status),
                "session_type": int(graphics.session_type),
                "completed_laps": int(graphics.completed_laps),
                "normalized_car_position": float(graphics.normalized_car_position),
                "is_valid_lap": int(graphics.is_valid_lap),
                "is_in_pit": int(graphics.is_in_pit),
                "is_in_pit_lane": int(graphics.is_in_pit_lane),
                "is_setup_menu_visible": int(graphics.is_setup_menu_visible),
            },
            "static": {
                "sm_version": decode_utf16_array(static.sm_version),
                "ac_version": decode_utf16_array(static.ac_version),
                "car_model": decode_utf16_array(static.car_model),
                "track": decode_utf16_array(static.track),
                "sector_count": int(static.sector_count),
                "max_rpm": int(static.max_rpm),
            },
        },
        "important_offsets": important_offsets(),
        "notes": (
            "Raw bytes captured from ACC physics, graphics, and static shared-memory pages. "
            "Keep all .bin page files and this .json sidecar together."
        ),
    }


def page_metadata(name: str, raw: bytes, raw_path: Path) -> dict[str, Any]:
    page_name, struct_type = PAGE_SPECS[name]
    return {
        "shared_memory_name": page_name,
        "struct": f"simcoach.adapters.acc.{struct_type.__name__}",
        "struct_size_bytes": ctypes.sizeof(struct_type),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw_path": raw_path.name,
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
