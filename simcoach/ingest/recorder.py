"""Recorder poll loop: adapter -> in-memory buffer -> durable artifacts."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from simcoach.adapters.base import NormalizedTick, SessionInfo, SimAdapter
from simcoach.ingest.session import (
    atomic_write_yaml,
    create_recorded_session,
    finalize_recorded_session,
    utc_local_now,
)
from simcoach.utils.llm_logger import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    out_dir: Path
    tick_rate_hz: int = 60
    metadata_stable_s: float = 1.0
    metadata_missing_grace_s: float = 2.0
    tick_missing_grace_s: float = 2.0
    max_seconds: float | None = None


class Recorder:
    def __init__(self, adapter: SimAdapter, config: RecorderConfig) -> None:
        self.adapter = adapter
        self.config = config

    def run(self) -> Path:
        LOGGER.info(
            "[START] recorder.run | sim=%s out=%s", self.adapter.sim_name, self.config.out_dir
        )
        try:
            self.adapter.connect()
        except Exception:
            try:
                self.adapter.disconnect()
            except Exception:
                LOGGER.exception("[ERROR] recorder.run | connect cleanup failed")
            raise
        ticks: list[NormalizedTick] = []
        sim_invalidated_laps: set[int] = set()
        interrupted = False
        terminal_failure_reason = None
        info: SessionInfo | None = None
        session_id = ""
        started_at = None
        paths = None
        stop_error: Exception | None = None
        disconnect_error: Exception | None = None
        current_session_finalized = False
        active_lap: int | None = None
        active_lap_start_t: float | None = None
        try:
            info = self._wait_for_stable_metadata()
            session_id, started_at, paths = create_recorded_session(self.config.out_dir, info)
            LOGGER.info("[STATE] recorded_session | id=%s path=%s", session_id, paths.root)
            period = 1.0 / self.config.tick_rate_hz
            loop_started = time.monotonic()
            last_metadata_seen = time.monotonic()
            last_tick_seen: float | None = None
            last_info = info
            while True:
                loop_time = time.monotonic()
                if (
                    self.config.max_seconds is not None
                    and loop_time - loop_started >= self.config.max_seconds
                ):
                    LOGGER.info("[BRANCH] recorder.run | max_seconds reached")
                    break

                current_info = self.adapter.read_session_info()
                if current_info is None:
                    if loop_time - last_metadata_seen > self.config.metadata_missing_grace_s:
                        terminal_failure_reason = "metadata_unavailable"
                        raise RuntimeError("required metadata became unavailable")
                else:
                    last_metadata_seen = loop_time
                    boundary_reason = session_boundary_reason(last_info, current_info)
                    if boundary_reason is not None:
                        LOGGER.info(
                            "[BRANCH] recorded_session_boundary reason=%s",
                            boundary_reason,
                        )
                        final_yaml = finalize_recorded_session(
                            paths,
                            session_id,
                            info,
                            started_at,
                            ticks,
                            interrupted=False,
                            sim_invalidated_laps=sim_invalidated_laps,
                        )
                        if final_yaml.get("complete") is not True:
                            current_session_finalized = True
                            final_yaml["complete"] = False
                            final_yaml["failure_reason"] = (
                                "session_boundary_finalization_failed"
                            )
                            final_yaml.setdefault("ended_at", utc_local_now().isoformat())
                            atomic_write_yaml(paths.session_yaml, final_yaml)
                            raise RuntimeError(
                                "Recorded Session Boundary finalization failed: "
                                f"{paths.root}"
                            )
                        ticks = []
                        sim_invalidated_laps = set()
                        active_lap = None
                        active_lap_start_t = None
                        info = current_info
                        session_id, started_at, paths = create_recorded_session(
                            self.config.out_dir, info
                        )
                        LOGGER.info(
                            "[STATE] recorded_session | id=%s path=%s",
                            session_id,
                            paths.root,
                        )
                        last_info = current_info
                        continue
                    last_info = current_info

                tick = self.adapter.read_tick()
                if tick is not None:
                    last_tick_seen = loop_time
                    if active_lap is None:
                        active_lap = tick.lap
                        active_lap_start_t = tick.t
                    elif tick.lap != active_lap:
                        if active_lap_start_t is not None:
                            lap_time_s = max(0.0, tick.t - active_lap_start_t)
                            print(f"lap {active_lap} provisional_time={lap_time_s:.3f}s")
                            LOGGER.info(
                                "[LOOP:PROGRESS] recorder | lap=%s provisional_time_s=%.3f",
                                active_lap,
                                lap_time_s,
                            )
                        active_lap = tick.lap
                        active_lap_start_t = tick.t
                    ticks.append(tick)
                    if current_info is not None and current_info.get("lap_invalidated") is True:
                        sim_invalidated_laps.add(tick.lap)
                        LOGGER.info(
                            "[BRANCH] lap %s invalid -> sim_invalidated",
                            tick.lap,
                        )
                    if len(ticks) == 1 or tick.lap != ticks[-2].lap:
                        LOGGER.info(
                            "[LOOP:PROGRESS] recorder | lap=%s ticks=%s state=%s",
                            tick.lap,
                            len(ticks),
                            tick.vehicle_state,
                        )
                elif (
                    ticks
                    and last_tick_seen is not None
                    and loop_time - last_tick_seen > self.config.tick_missing_grace_s
                ):
                    LOGGER.info(
                        "[BRANCH] recorder.run | tick stream ended after %.3fs",
                        loop_time - last_tick_seen,
                    )
                    break
                sleep_for = period - (time.monotonic() - loop_time)
                if sleep_for > 0:
                    time.sleep(sleep_for)
        except KeyboardInterrupt:
            interrupted = True
            LOGGER.info("[BRANCH] recorder.run | keyboard_interrupt")
        except Exception as exc:
            stop_error = exc
            if terminal_failure_reason is None:
                terminal_failure_reason = "unknown_failure"
            LOGGER.exception("[ERROR] recorder.run | stopping after error=%s", exc)
        finally:
            try:
                self.adapter.disconnect()
            except Exception as exc:
                disconnect_error = exc
                LOGGER.exception("[ERROR] recorder.run | disconnect failed error=%s", exc)

        if info is None or started_at is None or paths is None:
            if stop_error is not None:
                raise stop_error
            if disconnect_error is not None:
                raise disconnect_error
            raise RuntimeError("Recording never started; required metadata was unavailable")

        finalization_error: RuntimeError | None = None
        if not current_session_finalized:
            final_yaml = finalize_recorded_session(
                paths,
                session_id,
                info,
                started_at,
                ticks,
                interrupted=interrupted,
                terminal_failure_reason=terminal_failure_reason,
                sim_invalidated_laps=sim_invalidated_laps,
            )
            if terminal_failure_reason is None and final_yaml.get("complete") is not True:
                finalization_error = RuntimeError(
                    "Recorded Session finalization failed: "
                    f"{final_yaml.get('failure_reason', 'unknown_failure')}"
                )
            if terminal_failure_reason is not None and final_yaml.get("complete") is True:
                finalization_error = RuntimeError(
                    "Recorded Session finalization failed closed-state check"
                )
        LOGGER.info("[END] recorder.run | path=%s ticks=%s", paths.root, len(ticks))
        if stop_error is not None:
            raise stop_error
        if finalization_error is not None:
            raise finalization_error
        if disconnect_error is not None:
            raise disconnect_error
        return paths.root

    def _wait_for_stable_metadata(self) -> SessionInfo:
        LOGGER.info("[START] recorder.wait_for_stable_metadata")
        stable_info: SessionInfo | None = None
        stable_since: float | None = None
        while True:
            info = self.adapter.read_session_info()
            now = time.monotonic()
            if info is None:
                stable_info = None
                stable_since = None
                time.sleep(0.1)
                continue
            if stable_info is None or session_boundary_changed(stable_info, info):
                stable_info = info
                stable_since = now
                LOGGER.info(
                    "[STATE] metadata_candidate | sim=%s track=%s car=%s type=%s",
                    info["sim"],
                    info["track"],
                    info["car"],
                    info["session_type"],
                )
            elif stable_since is not None and now - stable_since >= self.config.metadata_stable_s:
                LOGGER.info("[END] recorder.wait_for_stable_metadata")
                return info
            time.sleep(0.1)


def session_boundary_changed(left: SessionInfo, right: SessionInfo) -> bool:
    return session_boundary_reason(left, right) is not None


def session_boundary_reason(left: SessionInfo, right: SessionInfo) -> str | None:
    fields = (
        "sim",
        "track",
        "car",
        "session_type",
        "adapter_version",
        "session_schema_version",
        "tick_schema_version",
        "lap_schema_version",
    )
    for field in fields:
        if left[field] != right[field]:
            return f"{field}_changed"
    return None
