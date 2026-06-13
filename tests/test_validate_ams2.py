from __future__ import annotations

import pytest

from scripts.validate_ams2 import (
    assert_lap_increment,
    assert_lap_invalidated,
    assert_pedal_response,
    assert_smooth_position,
    assert_stationary_speed,
)
from simcoach.adapters.base import NormalizedTick, SessionInfo


def test_validate_ams2_assertions_accept_expected_samples() -> None:
    assert_stationary_speed([tick(speed=0.2)])
    assert_pedal_response(
        [tick(throttle=0.0, brake=0.0)],
        [tick(throttle=1.0, brake=0.0)],
        [tick(throttle=0.0, brake=1.0)],
    )
    assert_lap_increment([tick(lap=1)], [tick(lap=2)])
    assert_lap_invalidated([info(lap_invalidated=False), info(lap_invalidated=True)])
    assert_smooth_position([tick(pos_x=0.0), tick(pos_x=10.0), tick(pos_x=20.0)])


def test_validate_ams2_assertions_reject_stale_or_implausible_samples() -> None:
    with pytest.raises(AssertionError, match="stationary speed"):
        assert_stationary_speed([tick(speed=8.0)])
    with pytest.raises(AssertionError, match="throttle"):
        assert_pedal_response(
            [tick(throttle=0.0, brake=0.0)],
            [tick(throttle=0.3, brake=0.0)],
            [tick(throttle=0.0, brake=1.0)],
        )
    with pytest.raises(AssertionError, match="lap did not increment"):
        assert_lap_increment([tick(lap=3)], [tick(lap=3)])
    with pytest.raises(AssertionError, match="lap_invalidated"):
        assert_lap_invalidated([info(lap_invalidated=False)])
    with pytest.raises(AssertionError, match="jumped"):
        assert_smooth_position([tick(pos_x=0.0), tick(pos_x=500.0)])


def tick(
    *,
    speed: float = 30.0,
    throttle: float = 0.0,
    brake: float = 0.0,
    lap: int = 1,
    pos_x: float = 0.0,
    pos_y: float = 0.0,
) -> NormalizedTick:
    return NormalizedTick(
        t=0.0,
        lap=lap,
        lap_dist_pct=0.0,
        lap_dist_m=None,
        speed=speed,
        throttle=throttle,
        brake=brake,
        steering=0.0,
        gear=1,
        rpm=1000.0,
        vehicle_state="running",
        pos_x=pos_x,
        pos_y=pos_y,
        pos_z=0.0,
    )


def info(*, lap_invalidated: bool) -> SessionInfo:
    return {
        "sim": "ams2",
        "track_raw": "Interlagos GP",
        "track": "interlagos_gp",
        "car_raw": "Formula Inter",
        "car": "formula_inter",
        "session_type": "practice",
        "track_length_m": 4309.0,
        "lap_dist_m_source": "sim",
        "adapter_version": "0.1.0",
        "validity_method": "sim_flag_plus_inferred",
        "session_schema_version": 1,
        "tick_schema_version": 1,
        "lap_schema_version": 1,
        "lap_invalidated": lap_invalidated,
    }
