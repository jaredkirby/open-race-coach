"""Recorded Session lifecycle and durable artifact writing."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import ulid
import yaml

from simcoach.adapters.base import NormalizedTick, SessionInfo
from simcoach.utils.llm_logger import get_logger

LOGGER = get_logger(__name__)

FailureReason = Literal[
    "metadata_unavailable",
    "artifact_write_failed",
    "recording_interrupted",
    "no_ticks_collected",
    "directory_collision",
    "session_boundary_finalization_failed",
    "unknown_failure",
]


@dataclass(frozen=True, slots=True)
class RecordedSessionPaths:
    root: Path
    session_yaml: Path
    ticks_parquet: Path
    laps_jsonl: Path
    coach_report: Path


def new_recorded_session_id() -> str:
    return str(ulid.new()).lower()


def utc_local_now() -> datetime:
    return datetime.now().astimezone()


def build_session_dir(
    out_dir: Path, info: SessionInfo, started_at: datetime, session_id: str
) -> Path:
    stamp = started_at.strftime("%Y-%m-%d_%H%M")
    name = f"{stamp}_{info['sim']}_{info['track']}_{info['session_type']}_{session_id}"
    return out_dir / name


def paths_for(root: Path) -> RecordedSessionPaths:
    return RecordedSessionPaths(
        root=root,
        session_yaml=root / "session.yaml",
        ticks_parquet=root / "ticks.parquet",
        laps_jsonl=root / "laps.jsonl",
        coach_report=root / "coach_report.md",
    )


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write_text(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def write_ticks_parquet(path: Path, ticks: list[NormalizedTick]) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    temp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        frame.to_parquet(temp_path, index=False)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return frame


def initial_session_yaml(
    session_id: str,
    info: SessionInfo,
    started_at: datetime,
    *,
    complete: bool,
    failure_reason: FailureReason | None,
    ended_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "session_schema_version": info["session_schema_version"],
        "tick_schema_version": info["tick_schema_version"],
        "lap_schema_version": info["lap_schema_version"],
        "recorded_session_id": session_id,
        "sim": info["sim"],
        "track_raw": info["track_raw"],
        "track": info["track"],
        "car_raw": info["car_raw"],
        "car": info["car"],
        "session_type": info["session_type"],
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat() if ended_at else None,
        "tick_rate_hz": 60,
        "track_length_m": info["track_length_m"],
        "lap_dist_m_source": info["lap_dist_m_source"],
        "adapter_version": info["adapter_version"],
        "validity_method": info["validity_method"],
        "complete": complete,
        "failure_reason": failure_reason,
        "notes": "",
    }


def create_recorded_session(
    out_dir: Path, info: SessionInfo
) -> tuple[str, datetime, RecordedSessionPaths]:
    session_id = new_recorded_session_id()
    started_at = utc_local_now()
    root = build_session_dir(out_dir, info, started_at, session_id)
    if root.exists():
        raise FileExistsError(f"Recorded Session directory already exists: {root}")
    root.mkdir(parents=True)
    paths = paths_for(root)
    atomic_write_yaml(
        paths.session_yaml,
        initial_session_yaml(
            session_id,
            info,
            started_at,
            complete=False,
            failure_reason="recording_interrupted",
        ),
    )
    return session_id, started_at, paths


def finalize_recorded_session(
    paths: RecordedSessionPaths,
    session_id: str,
    info: SessionInfo,
    started_at: datetime,
    ticks: list[NormalizedTick],
    *,
    interrupted: bool = False,
    terminal_failure_reason: FailureReason | None = None,
    sim_invalidated_laps: set[int] | None = None,
) -> dict[str, Any]:
    LOGGER.info(
        "[START] finalize_recorded_session | path=%s ticks=%s interrupted=%s",
        paths.root,
        len(ticks),
        interrupted,
    )
    ended_at = utc_local_now()
    if not ticks:
        session_yaml = initial_session_yaml(
            session_id,
            info,
            started_at,
            complete=False,
            failure_reason=terminal_failure_reason or "no_ticks_collected",
            ended_at=ended_at,
        )
        atomic_write_yaml(paths.session_yaml, session_yaml)
        return session_yaml

    try:
        frame = write_ticks_parquet(paths.ticks_parquet, ticks)
        validate_tick_invariants(frame)
        laps = derive_lap_records(
            frame,
            session_id,
            sim_invalidated_laps=sim_invalidated_laps,
        )
        atomic_write_jsonl(paths.laps_jsonl, laps)
        session_yaml = initial_session_yaml(
            session_id,
            info,
            started_at,
            complete=terminal_failure_reason is None,
            failure_reason=terminal_failure_reason,
            ended_at=ended_at,
        )
        if any_lap_uses_unknown_vehicle_state(frame, laps):
            session_yaml["validity_method"] = "unknown_plus_inferred"
        atomic_write_yaml(paths.session_yaml, session_yaml)
        LOGGER.info("[END] finalize_recorded_session | complete=true laps=%s", len(laps))
        return session_yaml
    except Exception as exc:
        LOGGER.exception("[ERROR] finalize_recorded_session | path=%s error=%s", paths.root, exc)
        failure_reason: FailureReason = (
            "recording_interrupted" if interrupted else "artifact_write_failed"
        )
        session_yaml = initial_session_yaml(
            session_id,
            info,
            started_at,
            complete=False,
            failure_reason=failure_reason,
            ended_at=ended_at,
        )
        try:
            atomic_write_yaml(paths.session_yaml, session_yaml)
        except Exception:
            LOGGER.exception("[ERROR] finalize_recorded_session | failed to write failure metadata")
        return session_yaml


def validate_tick_invariants(frame: pd.DataFrame) -> None:
    required = {
        "t",
        "lap",
        "lap_dist_pct",
        "lap_dist_m",
        "speed",
        "throttle",
        "brake",
        "steering",
        "gear",
        "rpm",
        "vehicle_state",
        "pos_x",
        "pos_y",
        "pos_z",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ticks.parquet missing columns: {sorted(missing)}")
    if not frame["t"].is_monotonic_increasing or frame["t"].duplicated().any():
        raise ValueError("ticks.parquet `t` must be strictly monotonic")
    bad_pct = frame["lap_dist_pct"].lt(0.0) | frame["lap_dist_pct"].gt(1.0)
    if bad_pct.any():
        raise ValueError("lap_dist_pct outside 0.0..1.0")


def derive_lap_records(
    frame: pd.DataFrame,
    session_id: str,
    *,
    sim_invalidated_laps: set[int] | None = None,
) -> list[dict[str, Any]]:
    sim_invalidated_laps = sim_invalidated_laps or set()
    laps: list[dict[str, Any]] = []
    for lap, group in frame.groupby("lap", sort=True):
        if group.empty:
            continue
        start_idx = int(group.index.min())
        end_idx = int(group.index.max()) + 1
        invalid_reason = infer_invalid_reason(group)
        if invalid_reason is None and int(lap) in sim_invalidated_laps:
            invalid_reason = "sim_invalidated"
        lap_time_s = float(group["t"].iloc[-1] - group["t"].iloc[0])
        laps.append(
            {
                "lap_id": f"{session_id}:lap:{int(lap)}",
                "lap": int(lap),
                "lap_time_s": round(lap_time_s, 6),
                "lap_time_source": "derived_from_ticks",
                "sector_times_s": None,
                "valid": invalid_reason is None,
                "invalid_reason": invalid_reason,
                "tick_range": [start_idx, end_idx],
            }
        )
    ensure_contiguous_lap_ranges(laps)
    LOGGER.info(
        "[STATE] laps | count=%s valid=%s", len(laps), sum(1 for lap in laps if lap["valid"])
    )
    return laps


def infer_invalid_reason(group: pd.DataFrame) -> str | None:
    if group.empty:
        return "missing_tick_range"
    if group["vehicle_state"].isin(["paused", "menu", "replay"]).any():
        return "non_running_vehicle_state"
    if group["vehicle_state"].eq("pit").any():
        return "non_running_vehicle_state"
    known_non_running = group["vehicle_state"].isin(["paused", "menu", "replay", "pit"])
    if len(group) and float(known_non_running.mean()) > 0.2:
        return "non_running_vehicle_state"
    if not lap_progress_usable(group["lap_dist_pct"].astype(float).tolist()):
        return "bad_lap_progress"
    if position_has_teleport(group):
        return "teleport_or_reset"
    return None


def lap_progress_usable(values: list[float]) -> bool:
    if len(values) < 2:
        return False
    reversals = 0
    flat_run = 0
    for previous, current in zip(values, values[1:], strict=False):
        delta = current - previous
        if delta < -0.01:
            reversals += 1
        if delta > 0.08:
            return False
        if abs(delta) < 1e-5:
            flat_run += 1
        else:
            flat_run = 0
        if flat_run > 180:
            return False
    return reversals == 0


def position_has_teleport(group: pd.DataFrame) -> bool:
    if len(group) < 2:
        return False
    dx = group["pos_x"].diff()
    dy = group["pos_y"].diff()
    distances = (dx.pow(2) + dy.pow(2)).apply(
        lambda value: math.sqrt(value) if pd.notna(value) else 0
    )
    return bool(distances.gt(500.0).any())


def ensure_contiguous_lap_ranges(laps: list[dict[str, Any]]) -> None:
    previous_end: int | None = None
    for lap in laps:
        start, end = lap["tick_range"]
        if start >= end:
            lap["valid"] = False
            lap["invalid_reason"] = "missing_tick_range"
        if previous_end is not None and start != previous_end:
            raise ValueError("lap tick ranges must be contiguous with no gaps or overlaps")
        previous_end = end


def any_lap_uses_unknown_vehicle_state(frame: pd.DataFrame, laps: list[dict[str, Any]]) -> bool:
    for lap in laps:
        if not lap["valid"]:
            continue
        start, end = lap["tick_range"]
        if frame.iloc[start:end]["vehicle_state"].eq("unknown").any():
            return True
    return False
