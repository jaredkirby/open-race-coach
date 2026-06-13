"""Assetto Corsa Competizione adapter.

ACC exposes three shared-memory pages: physics, graphics, and static. The field
names here follow the public ACC shared-memory documentation mirrors. Live field
fidelity still must be validated on Windows with ACC running.
"""

from __future__ import annotations

import ctypes
import mmap
import sys
import time
from typing import Any

from simcoach.adapters.ams2 import normalize_matching_key
from simcoach.adapters.base import NormalizedTick, SessionInfo, SimAdapter, VehicleState
from simcoach.utils.llm_logger import get_logger

LOGGER = get_logger(__name__)

ADAPTER_VERSION = "0.1.0"
PHYSICS_PAGE = "Local\\acpmf_physics"
GRAPHICS_PAGE = "Local\\acpmf_graphics"
STATIC_PAGE = "Local\\acpmf_static"

ACC_OFF = 0
ACC_REPLAY = 1
ACC_LIVE = 2
ACC_PAUSE = 3

ACC_PRACTICE = 0
ACC_QUALIFY = 1
ACC_RACE = 2
ACC_HOTLAP = 3
ACC_TIME_ATTACK = 4
ACC_HOTSTINT = 7
ACC_HOTLAP_SUPERPOLE = 8

VEC3 = ctypes.c_float * 3
WHEELS_FLOAT = ctypes.c_float * 4
CAR_DAMAGE = ctypes.c_float * 5
CONTACT_POINTS = VEC3 * 4
CAR_COORDINATES = VEC3 * 60
CAR_IDS = ctypes.c_int * 60


def utf16_array(length: int) -> type[ctypes.Array[ctypes.c_uint16]]:
    return ctypes.c_uint16 * length


class ACCPhysics(ctypes.Structure):
    _fields_ = [
        ("packet_id", ctypes.c_int),
        ("gas", ctypes.c_float),
        ("brake", ctypes.c_float),
        ("fuel", ctypes.c_float),
        ("gear", ctypes.c_int),
        ("rpm", ctypes.c_int),
        ("steer_angle", ctypes.c_float),
        ("speed_kmh", ctypes.c_float),
        ("velocity", VEC3),
        ("g_force", VEC3),
        ("wheel_slip", WHEELS_FLOAT),
        ("wheel_pressure", WHEELS_FLOAT),
        ("wheel_angular_speed", WHEELS_FLOAT),
        ("tyre_core_temp", WHEELS_FLOAT),
        ("suspension_travel", WHEELS_FLOAT),
        ("tc", ctypes.c_float),
        ("heading", ctypes.c_float),
        ("pitch", ctypes.c_float),
        ("roll", ctypes.c_float),
        ("car_damage", CAR_DAMAGE),
        ("pit_limiter_on", ctypes.c_int),
        ("abs", ctypes.c_float),
        ("autoshifter_on", ctypes.c_int),
        ("turbo_boost", ctypes.c_float),
        ("air_temp", ctypes.c_float),
        ("road_temp", ctypes.c_float),
        ("local_angular_velocity", VEC3),
        ("final_ff", ctypes.c_float),
        ("brake_temp", WHEELS_FLOAT),
        ("clutch", ctypes.c_float),
        ("is_ai_controlled", ctypes.c_int),
        ("tyre_contact_point", CONTACT_POINTS),
        ("tyre_contact_normal", CONTACT_POINTS),
        ("tyre_contact_heading", CONTACT_POINTS),
        ("brake_bias", ctypes.c_float),
        ("local_velocity", VEC3),
        ("slip_ratio", WHEELS_FLOAT),
        ("slip_angle", WHEELS_FLOAT),
        ("suspension_damage", WHEELS_FLOAT),
        ("water_temp", ctypes.c_float),
        ("brake_pressure", WHEELS_FLOAT),
        ("front_brake_compound", ctypes.c_int),
        ("rear_brake_compound", ctypes.c_int),
        ("pad_life", WHEELS_FLOAT),
        ("disc_life", WHEELS_FLOAT),
        ("ignition_on", ctypes.c_int),
        ("starter_engine_on", ctypes.c_int),
        ("is_engine_running", ctypes.c_int),
        ("kerb_vibration", ctypes.c_float),
        ("slip_vibration", ctypes.c_float),
        ("g_vibration", ctypes.c_float),
        ("abs_vibration", ctypes.c_float),
    ]


class ACCGraphics(ctypes.Structure):
    _fields_ = [
        ("packet_id", ctypes.c_int),
        ("status", ctypes.c_int),
        ("session_type", ctypes.c_int),
        ("current_time_str", utf16_array(15)),
        ("last_time_str", utf16_array(15)),
        ("best_time_str", utf16_array(15)),
        ("last_sector_time_str", utf16_array(15)),
        ("completed_laps", ctypes.c_int),
        ("position", ctypes.c_int),
        ("current_time_ms", ctypes.c_int),
        ("last_time_ms", ctypes.c_int),
        ("best_time_ms", ctypes.c_int),
        ("session_time_left", ctypes.c_float),
        ("distance_traveled", ctypes.c_float),
        ("is_in_pit", ctypes.c_int),
        ("current_sector_index", ctypes.c_int),
        ("last_sector_time_ms", ctypes.c_int),
        ("number_of_laps", ctypes.c_int),
        ("tyre_compound", utf16_array(33)),
        ("replay_time_multiplier", ctypes.c_float),
        ("normalized_car_position", ctypes.c_float),
        ("active_cars", ctypes.c_int),
        ("car_coordinates", CAR_COORDINATES),
        ("car_id", CAR_IDS),
        ("player_car_id", ctypes.c_int),
        ("penalty_time", ctypes.c_float),
        ("flag", ctypes.c_int),
        ("penalty", ctypes.c_int),
        ("ideal_line_on", ctypes.c_int),
        ("is_in_pit_lane", ctypes.c_int),
        ("surface_grip", ctypes.c_float),
        ("mandatory_pit_done", ctypes.c_int),
        ("wind_speed", ctypes.c_float),
        ("wind_direction", ctypes.c_float),
        ("is_setup_menu_visible", ctypes.c_int),
        ("main_display_index", ctypes.c_int),
        ("secondary_display_index", ctypes.c_int),
        ("tc_level", ctypes.c_int),
        ("tc_cut_level", ctypes.c_int),
        ("engine_map", ctypes.c_int),
        ("abs_level", ctypes.c_int),
        ("fuel_per_lap", ctypes.c_float),
        ("rain_light", ctypes.c_int),
        ("flashing_light", ctypes.c_int),
        ("lights_stage", ctypes.c_int),
        ("exhaust_temp", ctypes.c_float),
        ("wiper_stage", ctypes.c_int),
        ("driver_stint_total_time_left_ms", ctypes.c_int),
        ("driver_stint_time_left_ms", ctypes.c_int),
        ("rain_tyres", ctypes.c_int),
        ("session_index", ctypes.c_int),
        ("used_fuel", ctypes.c_float),
        ("delta_lap_time_str", utf16_array(15)),
        ("delta_lap_time_ms", ctypes.c_int),
        ("estimated_lap_time_str", utf16_array(15)),
        ("estimated_lap_time_ms", ctypes.c_int),
        ("is_delta_positive", ctypes.c_int),
        ("split_time_ms", ctypes.c_int),
        ("is_valid_lap", ctypes.c_int),
        ("fuel_estimated_laps", ctypes.c_float),
        ("track_status", utf16_array(33)),
        ("missing_mandatory_pits", ctypes.c_int),
        ("clock", ctypes.c_float),
        ("direction_light_left", ctypes.c_int),
        ("direction_light_right", ctypes.c_int),
        ("global_yellow", ctypes.c_int),
        ("global_yellow_s1", ctypes.c_int),
        ("global_yellow_s2", ctypes.c_int),
        ("global_yellow_s3", ctypes.c_int),
        ("global_white", ctypes.c_int),
        ("global_green", ctypes.c_int),
        ("global_chequered", ctypes.c_int),
        ("global_red", ctypes.c_int),
    ]


class ACCStatic(ctypes.Structure):
    _fields_ = [
        ("sm_version", utf16_array(15)),
        ("ac_version", utf16_array(15)),
        ("number_of_sessions", ctypes.c_int),
        ("num_cars", ctypes.c_int),
        ("car_model", utf16_array(33)),
        ("track", utf16_array(33)),
        ("player_name", utf16_array(33)),
        ("player_surname", utf16_array(33)),
        ("player_nick", utf16_array(33)),
        ("sector_count", ctypes.c_int),
        ("max_rpm", ctypes.c_int),
        ("max_fuel", ctypes.c_float),
        ("penalty_enabled", ctypes.c_int),
        ("aid_fuel_rate", ctypes.c_float),
        ("aid_tyre_rate", ctypes.c_float),
        ("aid_mechanical_damage", ctypes.c_float),
        ("aid_stability", ctypes.c_float),
        ("aid_auto_clutch", ctypes.c_int),
        ("pit_window_start", ctypes.c_int),
        ("pit_window_end", ctypes.c_int),
        ("is_online", ctypes.c_int),
        ("dry_tyres_name", utf16_array(33)),
        ("wet_tyres_name", utf16_array(33)),
    ]


def important_offsets() -> dict[str, dict[str, int]]:
    return {
        "physics": {
            "gas": ACCPhysics.gas.offset,
            "brake": ACCPhysics.brake.offset,
            "gear": ACCPhysics.gear.offset,
            "rpm": ACCPhysics.rpm.offset,
            "speed_kmh": ACCPhysics.speed_kmh.offset,
            "tyre_contact_point": ACCPhysics.tyre_contact_point.offset,
        },
        "graphics": {
            "status": ACCGraphics.status.offset,
            "session_type": ACCGraphics.session_type.offset,
            "completed_laps": ACCGraphics.completed_laps.offset,
            "normalized_car_position": ACCGraphics.normalized_car_position.offset,
            "is_valid_lap": ACCGraphics.is_valid_lap.offset,
        },
        "static": {
            "car_model": ACCStatic.car_model.offset,
            "track": ACCStatic.track.offset,
        },
    }


class ACCAdapter(SimAdapter):
    sim_name = "acc"
    adapter_version = ADAPTER_VERSION

    def __init__(self) -> None:
        self._physics_mmap: mmap.mmap | None = None
        self._graphics_mmap: mmap.mmap | None = None
        self._static_mmap: mmap.mmap | None = None
        self._capture_started_at: float | None = None

    def connect(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("ACC shared-memory capture requires Windows")
        LOGGER.info("[START] acc.connect")
        self._physics_mmap = _open_page(PHYSICS_PAGE, ACCPhysics)
        self._graphics_mmap = _open_page(GRAPHICS_PAGE, ACCGraphics)
        self._static_mmap = _open_page(STATIC_PAGE, ACCStatic)
        self._capture_started_at = time.monotonic()
        LOGGER.info(
            "[END] acc.connect | physics=%s graphics=%s static=%s",
            ctypes.sizeof(ACCPhysics),
            ctypes.sizeof(ACCGraphics),
            ctypes.sizeof(ACCStatic),
        )

    def disconnect(self) -> None:
        LOGGER.info("[START] acc.disconnect")
        for page in (self._physics_mmap, self._graphics_mmap, self._static_mmap):
            if page is not None:
                page.close()
        self._physics_mmap = None
        self._graphics_mmap = None
        self._static_mmap = None
        LOGGER.info("[END] acc.disconnect")

    def read_tick(self) -> NormalizedTick | None:
        physics, graphics, _static = self._read_snapshots()
        if physics is None or graphics is None:
            return None
        capture_t = time.monotonic() - (self._capture_started_at or time.monotonic())
        return self.tick_from_snapshots(physics, graphics, capture_t)

    def read_session_info(self) -> SessionInfo | None:
        _physics, graphics, static = self._read_snapshots()
        if graphics is None or static is None:
            return None
        return self.session_info_from_snapshots(graphics, static)

    def _read_snapshots(self) -> tuple[ACCPhysics | None, ACCGraphics | None, ACCStatic | None]:
        return (
            _read_page(self._physics_mmap, ACCPhysics),
            _read_page(self._graphics_mmap, ACCGraphics),
            _read_page(self._static_mmap, ACCStatic),
        )

    @staticmethod
    def tick_from_snapshots(
        physics: ACCPhysics,
        graphics: ACCGraphics,
        capture_t: float,
    ) -> NormalizedTick | None:
        lap_dist_pct = float(graphics.normalized_car_position)
        if lap_dist_pct < 0.0 or lap_dist_pct > 1.0:
            LOGGER.info(
                "[SKIP] acc.tick_from_snapshots | bad normalized_car_position=%s", lap_dist_pct
            )
            return None
        pos_x, pos_y, pos_z = _average_contact_position(physics)
        return NormalizedTick(
            t=capture_t,
            lap=int(graphics.completed_laps) + 1,
            lap_dist_pct=lap_dist_pct,
            lap_dist_m=None,
            speed=max(0.0, float(physics.speed_kmh) / 3.6),
            throttle=max(0.0, min(float(physics.gas), 1.0)),
            brake=max(0.0, min(float(physics.brake), 1.0)),
            steering=max(-1.0, min(float(physics.steer_angle), 1.0)),
            gear=_normalize_acc_gear(int(physics.gear)),
            rpm=max(0.0, float(physics.rpm)),
            vehicle_state=_vehicle_state(graphics),
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
        )

    @staticmethod
    def session_info_from_snapshots(graphics: ACCGraphics, static: ACCStatic) -> SessionInfo | None:
        track_raw = decode_utf16_array(static.track)
        car_raw = decode_utf16_array(static.car_model)
        track = normalize_matching_key(track_raw)
        car = normalize_matching_key(car_raw)
        if not track or not car:
            LOGGER.info(
                "[SKIP] acc.session_info_from_snapshots | metadata_unavailable track=%r car=%r",
                track_raw,
                car_raw,
            )
            return None
        return {
            "sim": "acc",
            "track_raw": track_raw,
            "track": track,
            "car_raw": car_raw,
            "car": car,
            "session_type": _session_type(int(graphics.session_type)),  # type: ignore[typeddict-item]
            "track_length_m": None,
            "lap_dist_m_source": "unavailable",
            "adapter_version": ADAPTER_VERSION,
            "validity_method": "sim_flag_plus_inferred",
            "session_schema_version": 1,
            "tick_schema_version": 1,
            "lap_schema_version": 1,
            "lap_invalidated": not bool(graphics.is_valid_lap),
        }


def decode_utf16_array(value: Any) -> str:
    code_units: list[int] = []
    for code_unit in value:
        if int(code_unit) == 0:
            break
        code_units.append(int(code_unit))
    raw = b"".join(int(unit).to_bytes(2, "little") for unit in code_units)
    return raw.decode("utf-16-le", errors="replace").strip()


def _open_page(name: str, struct_type: type[ctypes.Structure]) -> mmap.mmap:
    return mmap.mmap(-1, ctypes.sizeof(struct_type), tagname=name, access=mmap.ACCESS_READ)


def _read_page(
    page: mmap.mmap | None,
    struct_type: type[ctypes.Structure],
) -> Any | None:
    if page is None:
        return None
    page.seek(0)
    raw = page.read(ctypes.sizeof(struct_type))
    if len(raw) != ctypes.sizeof(struct_type):
        LOGGER.error("[ERROR] acc.read_page | short_read size=%s", len(raw))
        return None
    return struct_type.from_buffer_copy(raw)


def _vehicle_state(graphics: ACCGraphics) -> VehicleState:
    if graphics.status == ACC_REPLAY:
        return "replay"
    if graphics.status == ACC_PAUSE:
        return "paused"
    if graphics.status == ACC_LIVE:
        if graphics.is_in_pit or graphics.is_in_pit_lane or graphics.is_setup_menu_visible:
            return "pit"
        return "running"
    if graphics.status == ACC_OFF:
        return "menu"
    return "unknown"


def _session_type(value: int) -> str:
    return {
        ACC_PRACTICE: "practice",
        ACC_QUALIFY: "qualifying",
        ACC_RACE: "race",
        ACC_HOTLAP: "hotlap",
        ACC_TIME_ATTACK: "hotlap",
        ACC_HOTSTINT: "hotlap",
        ACC_HOTLAP_SUPERPOLE: "hotlap",
    }.get(value, "practice")


def _normalize_acc_gear(value: int) -> int:
    return value - 1


def _average_contact_position(physics: ACCPhysics) -> tuple[float, float, float]:
    xs = [float(point[0]) for point in physics.tyre_contact_point]
    ys = [float(point[2]) for point in physics.tyre_contact_point]
    zs = [float(point[1]) for point in physics.tyre_contact_point]
    return sum(xs) / 4.0, sum(ys) / 4.0, sum(zs) / 4.0
