"""Recorder poll loop: adapter -> in-memory buffer -> durable artifacts."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
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


class RecordingCancelled(RuntimeError):
    """Cooperative cancellation before any Recorded Session could be allocated."""


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    out_dir: Path
    tick_rate_hz: int = 60
    metadata_stable_s: float = 1.0
    metadata_missing_grace_s: float = 2.0
    tick_missing_grace_s: float = 2.0
    max_seconds: float | None = None
    continuous_source_gaps: bool = False


class Recorder:
    def __init__(self, adapter: SimAdapter, config: RecorderConfig, *,
                 stop_requested: Callable[[], bool] | None = None) -> None:
        self.adapter = adapter
        self.config = config
        self.stop_requested = stop_requested or (lambda: False)
        self.session_paths: tuple[Path, ...] = ()
        self.ticks_observed = 0
        self.active_ticks = 0
        self.last_tick_at: float | None = None
        self.intake_active = False
        self.capture_phase = "starting"
        self.source_gap_reason: str | None = None
        self.source_gap_count = 0
        self._checkpoint_lock = threading.Lock()
        self._checkpoint_request = None
        self._checkpoint_result = None

    def request_checkpoint(self, request_id):
        """Reserve one prefix handoff; the caller owns durable intent and completion."""
        with self._checkpoint_lock:
            if self._checkpoint_request is not None or self._checkpoint_result is not None:
                return False
            self._checkpoint_request = request_id
            return True

    def take_checkpoint(self):
        with self._checkpoint_lock:
            result, self._checkpoint_result = self._checkpoint_result, None
            return result

    def run(self) -> Path:
        self.session_paths = ()
        self.ticks_observed = 0
        self.active_ticks = 0
        self.last_tick_at = None
        self.intake_active = False
        self.capture_phase = "starting"
        self.source_gap_reason = None
        self.source_gap_count = 0
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
        waiting_for_source = False
        continuous = self.config.continuous_source_gaps is True
        try:
            info = self._wait_for_stable_metadata()
            session_id, started_at, paths = create_recorded_session(self.config.out_dir, info)
            self.session_paths += (paths.root,)
            self.intake_active = True
            LOGGER.info("[STATE] recorded_session | id=%s path=%s", session_id, paths.root)
            period = 1.0 / self.config.tick_rate_hz
            loop_started = time.monotonic()
            last_metadata_seen = time.monotonic()
            last_tick_seen: float | None = None
            last_info = info
            while True:
                if self.stop_requested():
                    LOGGER.info("[BRANCH] recorder.run | cooperative stop requested")
                    break
                loop_time = time.monotonic()
                if (
                    self.config.max_seconds is not None
                    and loop_time - loop_started >= self.config.max_seconds
                ):
                    LOGGER.info("[BRANCH] recorder.run | max_seconds reached")
                    break

                current_info, tick = self._read_observation()
                observation_time = time.monotonic() if continuous else loop_time
                if waiting_for_source:
                    if current_info is None or tick is None:
                        sleep_for = period - (time.monotonic() - loop_time)
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                        continue
                    # The closed prefix remains finalized if successor allocation fails.
                    info = current_info
                    session_id, started_at, paths = create_recorded_session(
                        self.config.out_dir, info
                    )
                    current_session_finalized = False
                    self.session_paths += (paths.root,)
                    waiting_for_source = False
                    self.source_gap_reason = None
                    last_info = current_info
                    last_metadata_seen = observation_time
                    last_tick_seen = None

                if current_info is None:
                    tick = None
                gap_reason = None
                if (current_info is None
                        and observation_time - last_metadata_seen
                        > self.config.metadata_missing_grace_s):
                    gap_reason = "metadata_unavailable"
                elif ((continuous or tick is None) and ticks and last_tick_seen is not None
                      and observation_time - last_tick_seen > self.config.tick_missing_grace_s):
                    gap_reason = "tick_stream_stalled"
                if continuous and ticks and gap_reason is not None:
                    self.intake_active = False
                    self.active_ticks = 0
                    self.last_tick_at = None
                    self.capture_phase = "finalizing"
                    self.source_gap_reason = gap_reason
                    self.source_gap_count += 1
                    # Even an unexpected finalizer exception must not retry or rewrite.
                    current_session_finalized = True
                    final_yaml = finalize_recorded_session(
                        paths, session_id, info, started_at, ticks, interrupted=False,
                        sim_invalidated_laps=sim_invalidated_laps,
                    )
                    if final_yaml.get("complete") is not True:
                        raise RuntimeError(f"Source gap prefix finalization failed: {paths.root}")
                    ticks = []
                    sim_invalidated_laps = set()
                    active_lap = None
                    active_lap_start_t = None
                    last_tick_seen = None
                    waiting_for_source = True
                    self.capture_phase = "waiting_for_source"
                    if current_info is None or tick is None:
                        continue
                    # A late but usable observation starts the successor immediately,
                    # retaining its original Capture Time rather than losing or bridging it.
                    info = current_info
                    session_id, started_at, paths = create_recorded_session(
                        self.config.out_dir, info
                    )
                    current_session_finalized = False
                    self.session_paths += (paths.root,)
                    waiting_for_source = False
                    self.source_gap_reason = None
                    last_info = current_info
                    last_metadata_seen = observation_time

                if current_info is None:
                    # Never bind a newly read tick to stale metadata during the grace period.
                    tick = None
                    self.intake_active = False
                    if (observation_time - last_metadata_seen
                            > self.config.metadata_missing_grace_s):
                        terminal_failure_reason = "metadata_unavailable"
                        raise RuntimeError("required metadata became unavailable")
                else:
                    self.intake_active = True
                    last_metadata_seen = observation_time
                    boundary_reason = session_boundary_reason(last_info, current_info)
                    if tick is None and (continuous or boundary_reason == "source_epoch_changed"):
                        boundary_reason = None
                    if (boundary_reason is None and tick is not None and ticks
                            and type(tick.lap) is int and type(ticks[-1].lap) is int
                            and tick.lap < ticks[-1].lap):
                        boundary_reason = "lap_counter_regressed"
                    if boundary_reason is not None:
                        self.intake_active = False
                        self.capture_phase = "finalizing"
                        LOGGER.info(
                            "[BRANCH] recorded_session_boundary reason=%s id=%s old_lap=%s "
                            "incoming_lap=%s",
                            boundary_reason, session_id, ticks[-1].lap if ticks else None,
                            tick.lap if tick is not None else None,
                        )
                        if continuous:
                            current_session_finalized = True
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
                        # Successor creation can fail. Never finalize the completed predecessor
                        # again using empty ticks or the successor's metadata during cleanup.
                        current_session_finalized = True
                        ticks = []
                        sim_invalidated_laps = set()
                        active_lap = None
                        active_lap_start_t = None
                        info = current_info
                        session_id, started_at, paths = create_recorded_session(
                            self.config.out_dir, info
                        )
                        current_session_finalized = False
                        self.session_paths += (paths.root,)
                        self.active_ticks = 0
                        self.last_tick_at = None
                        last_tick_seen = None
                        self.intake_active = True
                        LOGGER.info(
                            "[STATE] recorded_session | id=%s path=%s",
                            session_id,
                            paths.root,
                        )
                    if tick is not None or (not continuous and current_info.get("source_epoch")
                                            == last_info.get("source_epoch")):
                        last_info = current_info

                if tick is not None:
                    last_tick_seen = observation_time
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
                    self.ticks_observed += 1
                    self.active_ticks += 1
                    self.last_tick_at = time.perf_counter()
                    self.capture_phase = "recording"
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
                    with self._checkpoint_lock:
                        checkpoint_id = self._checkpoint_request
                    if checkpoint_id is not None:
                        # Allocate the successor before transferring this immutable
                        # prefix. Finalization/analysis run off the intake thread.
                        rotation_start = time.perf_counter()
                        prefix = dict(request_id=checkpoint_id, paths=paths,
                                      session_id=session_id, info=dict(info), started_at=started_at,
                                      ticks=ticks, sim_invalidated_laps=sim_invalidated_laps,
                                      sessions=self.session_paths)
                        try:
                            successor = create_recorded_session(self.config.out_dir, current_info)
                        except Exception as exc:
                            result = dict(request_id=checkpoint_id, error_type=type(exc).__name__)
                        else:
                            session_id, started_at, paths = successor
                            info = dict(current_info)
                            self.session_paths += (paths.root,)
                            ticks, sim_invalidated_laps = [], set()
                            active_lap = active_lap_start_t = None
                            self.active_ticks = 0
                            current_session_finalized = False
                            result = dict(prefix=prefix, request_id=checkpoint_id,
                                          successor=paths.root, rotation_s=(
                                              time.perf_counter() - rotation_start))
                        with self._checkpoint_lock:
                            self._checkpoint_request = None
                            self._checkpoint_result = result
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
            self.capture_phase = "failed"
            if terminal_failure_reason is None:
                terminal_failure_reason = "unknown_failure"
            LOGGER.exception("[ERROR] recorder.run | stopping after error=%s", exc)
        finally:
            self.intake_active = False
            if self.capture_phase not in ("failed", "waiting_for_source"):
                self.capture_phase = "closed"
            try:
                self.adapter.disconnect()
            except Exception as exc:
                disconnect_error = exc
                LOGGER.exception("[ERROR] recorder.run | disconnect failed error=%s", exc)

        if info is None or started_at is None or paths is None:
            if isinstance(stop_error, RecordingCancelled) and disconnect_error is not None:
                raise disconnect_error
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

    def _read_observation(self) -> tuple[SessionInfo | None, NormalizedTick | None]:
        reader = getattr(self.adapter, "read_observation", None)
        if reader is not None:
            return reader()
        # Compatibility with older duck-typed replay adapters; no coherence claim.
        info = self.adapter.read_session_info()
        return info, self.adapter.read_tick() if info is not None else None

    def _wait_for_stable_metadata(self) -> SessionInfo:
        LOGGER.info("[START] recorder.wait_for_stable_metadata")
        stable_info: SessionInfo | None = None
        stable_since: float | None = None
        while True:
            if self.stop_requested():
                raise RecordingCancelled("Recording stopped before stable metadata")
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
    if left.get("source_epoch") != right.get("source_epoch"):
        return "source_epoch_changed"
    return None
