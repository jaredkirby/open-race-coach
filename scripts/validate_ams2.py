#!/usr/bin/env python3
"""Interactive AMS2 shared-memory sanity checks."""

from __future__ import annotations

import time

from simcoach.adapters.ams2 import AMS2Adapter, important_offsets
from simcoach.adapters.base import NormalizedTick, SessionInfo


def sample(adapter: AMS2Adapter) -> tuple[NormalizedTick | None, SessionInfo | None]:
    tick = adapter.read_tick()
    info = adapter.read_session_info()
    if tick is None or info is None:
        print("No usable AMS2 tick/session metadata. Check Project CARS 2 shared-memory mode.")
        return tick, info
    print(
        {
            "track": info["track_raw"],
            "car": info["car_raw"],
            "lap": tick.lap,
            "lap_dist_pct": round(tick.lap_dist_pct, 4),
            "lap_dist_m": tick.lap_dist_m,
            "speed": round(tick.speed, 3),
            "throttle": round(tick.throttle, 3),
            "brake": round(tick.brake, 3),
            "steering": round(tick.steering, 3),
            "gear": tick.gear,
            "rpm": round(tick.rpm, 1),
            "vehicle_state": tick.vehicle_state,
            "position": (round(tick.pos_x, 2), round(tick.pos_y, 2), round(tick.pos_z, 2)),
            "lap_invalidated": info.get("lap_invalidated"),
        }
    )
    return tick, info


def collect_samples(
    adapter: AMS2Adapter,
    *,
    count: int = 5,
    delay_s: float = 0.2,
) -> tuple[list[NormalizedTick], list[SessionInfo]]:
    ticks: list[NormalizedTick] = []
    infos: list[SessionInfo] = []
    for _ in range(count):
        tick, info = sample(adapter)
        if tick is not None:
            ticks.append(tick)
        if info is not None:
            infos.append(info)
        time.sleep(delay_s)
    return ticks, infos


def assert_stationary_speed(ticks: list[NormalizedTick], *, max_speed_m_s: float = 1.0) -> None:
    if not ticks:
        raise AssertionError("no ticks sampled for stationary speed check")
    min_speed = min(tick.speed for tick in ticks)
    if min_speed > max_speed_m_s:
        raise AssertionError(f"stationary speed did not approach 0 m/s; min_speed={min_speed:.3f}")


def assert_pedal_response(
    released_ticks: list[NormalizedTick],
    throttle_ticks: list[NormalizedTick],
    brake_ticks: list[NormalizedTick],
    *,
    released_max: float = 0.2,
    pressed_min: float = 0.8,
) -> None:
    if not released_ticks or not throttle_ticks or not brake_ticks:
        raise AssertionError("pedal response check requires released, throttle, and brake samples")
    if min(tick.throttle for tick in released_ticks) > released_max:
        raise AssertionError("throttle did not sample near released state")
    if min(tick.brake for tick in released_ticks) > released_max:
        raise AssertionError("brake did not sample near released state")
    if max(tick.throttle for tick in throttle_ticks) < pressed_min:
        raise AssertionError("throttle did not sample near fully pressed state")
    if max(tick.brake for tick in brake_ticks) < pressed_min:
        raise AssertionError("brake did not sample near fully pressed state")


def assert_lap_increment(
    before_ticks: list[NormalizedTick], after_ticks: list[NormalizedTick]
) -> None:
    if not before_ticks or not after_ticks:
        raise AssertionError("lap increment check requires samples before and after start/finish")
    before_lap = before_ticks[-1].lap
    after_lap = after_ticks[-1].lap
    if after_lap <= before_lap:
        raise AssertionError(f"lap did not increment; before={before_lap} after={after_lap}")


def assert_lap_invalidated(infos: list[SessionInfo]) -> None:
    if not infos:
        raise AssertionError("lap invalidation check requires session metadata samples")
    if not any(info.get("lap_invalidated") is True for info in infos):
        raise AssertionError("lap_invalidated did not flip true after deliberate cut")


def assert_smooth_position(
    ticks: list[NormalizedTick],
    *,
    min_total_motion_m: float = 5.0,
    max_step_m: float = 100.0,
) -> None:
    if len(ticks) < 2:
        raise AssertionError("position smoothness check requires at least two ticks")
    steps = [
        ((right.pos_x - left.pos_x) ** 2 + (right.pos_y - left.pos_y) ** 2) ** 0.5
        for left, right in zip(ticks, ticks[1:], strict=False)
    ]
    if sum(steps) < min_total_motion_m:
        raise AssertionError("world position did not move enough while driving")
    if max(steps) > max_step_m:
        raise AssertionError(f"world position jumped unexpectedly; max_step_m={max(steps):.3f}")


def main() -> int:
    print("Important ctypes offsets:")
    for name, offset in important_offsets().items():
        print(f"  {name}: {offset}")

    adapter = AMS2Adapter()
    adapter.connect()
    try:
        input("\nSit stationary in the pit. Press Enter to sample...")
        stationary_ticks, _stationary_infos = collect_samples(adapter)
        assert_stationary_speed(stationary_ticks)

        input("\nRelease throttle and brake. Press Enter to sample...")
        released_ticks, _released_infos = collect_samples(adapter)
        input("\nPress throttle fully. Press Enter to sample...")
        throttle_ticks, _throttle_infos = collect_samples(adapter)
        input("\nPress brake fully. Press Enter to sample...")
        brake_ticks, _brake_infos = collect_samples(adapter)
        assert_pedal_response(released_ticks, throttle_ticks, brake_ticks)

        input("\nBefore crossing start/finish, press Enter to sample...")
        before_lap_ticks, _before_lap_infos = collect_samples(adapter)
        input("\nCross start/finish, then press Enter to sample...")
        after_lap_ticks, _after_lap_infos = collect_samples(adapter)
        assert_lap_increment(before_lap_ticks, after_lap_ticks)

        input("\nMake a deliberate cut. Press Enter after the sim marks the lap invalid...")
        _invalid_ticks, invalid_infos = collect_samples(adapter)
        assert_lap_invalidated(invalid_infos)

        input("\nDrive smoothly. Press Enter to sample position...")
        position_ticks, _position_infos = collect_samples(adapter, count=10)
        assert_smooth_position(position_ticks)
    finally:
        adapter.disconnect()
    print("\nAMS2 shared-memory sanity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
