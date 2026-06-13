"""Adapter contracts for simulator telemetry.

All numeric units in `NormalizedTick` match `schema/tick.md`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Literal, NotRequired, TypedDict

VehicleState = Literal["running", "pit", "paused", "menu", "replay", "unknown"]
SessionType = Literal["practice", "qualifying", "race", "hotlap"]
LapDistanceSource = Literal["sim", "derived_from_track_length", "unavailable"]
ValidityMethod = Literal["sim_flag_plus_inferred", "inferred", "unknown_plus_inferred"]


@dataclass(frozen=True, slots=True)
class NormalizedTick:
    """One normalized telemetry row.

    `t` is recorder-owned Capture Time in seconds from recording start.
    Speed is m/s, inputs are normalized 0..1, steering is -1..1, position is meters.
    """

    t: float
    lap: int
    lap_dist_pct: float
    lap_dist_m: float | None
    speed: float
    throttle: float
    brake: float
    steering: float
    gear: int
    rpm: float
    vehicle_state: VehicleState
    pos_x: float
    pos_y: float
    pos_z: float

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


class SessionInfo(TypedDict):
    sim: Literal["ams2", "acc", "ac"]
    track_raw: str
    track: str
    car_raw: str
    car: str
    session_type: SessionType
    track_length_m: float | None
    lap_dist_m_source: LapDistanceSource
    adapter_version: str
    validity_method: ValidityMethod
    session_schema_version: int
    tick_schema_version: int
    lap_schema_version: int
    lap_invalidated: NotRequired[bool]


class SimAdapter(ABC):
    """Base interface every simulator adapter must satisfy."""

    sim_name: str
    adapter_version: str

    @abstractmethod
    def connect(self) -> None:
        """Open the simulator data source."""

    @abstractmethod
    def read_tick(self) -> NormalizedTick | None:
        """Read one normalized tick, or `None` when no usable tick is available."""

    @abstractmethod
    def read_session_info(self) -> SessionInfo | None:
        """Return current session metadata, or `None` when required metadata is unavailable."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the simulator data source."""
