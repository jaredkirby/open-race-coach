"""Reusable validation for finalized Recorded Session artifacts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from simcoach.ingest.session import (
    ensure_contiguous_lap_ranges,
    infer_invalid_reason,
    validate_tick_invariants,
)

REQUIRED_SESSION_FIELDS = {
    "session_schema_version",
    "tick_schema_version",
    "lap_schema_version",
    "recorded_session_id",
    "sim",
    "track_raw",
    "track",
    "car_raw",
    "car",
    "session_type",
    "started_at",
    "ended_at",
    "tick_rate_hz",
    "track_length_m",
    "lap_dist_m_source",
    "adapter_version",
    "validity_method",
    "complete",
    "failure_reason",
    "notes",
}
RECORDED_SESSION_ID_PATTERN = re.compile(r"[0-7][0-9a-hjkmnp-tv-z]{25}")
INVALID_LAP_REASONS = {
    "sim_invalidated",
    "bad_lap_progress",
    "missing_tick_range",
    "non_running_vehicle_state",
    "teleport_or_reset",
    "metadata_boundary",
    "unknown_invalidity",
}
INFERRED_INVALID_LAP_REASONS = {
    "bad_lap_progress",
    "missing_tick_range",
    "non_running_vehicle_state",
    "teleport_or_reset",
}


def read_session_artifacts(
    session_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    session_yaml_path = session_dir / "session.yaml"
    ticks_path = session_dir / "ticks.parquet"
    laps_path = session_dir / "laps.jsonl"
    if not session_yaml_path.exists() or not ticks_path.exists() or not laps_path.exists():
        raise FileNotFoundError(
            "Recorded Session is missing session.yaml, ticks.parquet, or laps.jsonl"
        )
    session = yaml.safe_load(session_yaml_path.read_text(encoding="utf-8"))
    ticks = pd.read_parquet(ticks_path)
    laps = [
        json.loads(line)
        for line in laps_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return session, ticks, laps


def assert_supported_complete_session(session: dict[str, Any]) -> None:
    if session.get("complete") is not True:
        raise ValueError("Recorded Session is incomplete")
    for key in ("session_schema_version", "tick_schema_version", "lap_schema_version"):
        if session.get(key) != 1:
            raise ValueError(f"unsupported {key}: {session.get(key)!r}")


def read_validated_session_artifacts(
    session_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    session, ticks, laps = read_session_artifacts(session_dir)
    assert_supported_complete_session(session)
    validate_session_yaml(session)
    validate_tick_invariants(ticks)
    validate_laps(session, ticks, laps)
    return session, ticks, laps


def validate_recorded_session(session_dir: Path) -> dict[str, Any]:
    session, ticks, laps = read_validated_session_artifacts(session_dir)
    return {
        "recorded_session_id": session["recorded_session_id"],
        "tick_count": len(ticks),
        "lap_count": len(laps),
        "valid_lap_count": sum(1 for lap in laps if lap.get("valid") is True),
    }


def validate_laps(
    session: dict[str, Any],
    ticks: pd.DataFrame,
    laps: list[dict[str, Any]],
) -> None:
    if not laps:
        raise ValueError("laps.jsonl contains no lap records")
    for lap in laps:
        validate_lap_record_shape(lap)
    lap_numbers = [lap["lap"] for lap in laps]
    lap_ids = [lap["lap_id"] for lap in laps]
    if len(set(lap_numbers)) != len(lap_numbers):
        raise ValueError("laps.jsonl contains duplicate lap numbers")
    if len(set(lap_ids)) != len(lap_ids):
        raise ValueError("laps.jsonl contains duplicate lap_id values")
    ensure_contiguous_lap_ranges(laps)
    if laps[0]["tick_range"][0] != 0 or laps[-1]["tick_range"][1] != len(ticks):
        raise ValueError(
            "lap tick ranges must cover every ticks.parquet row exactly once: "
            f"first={laps[0]['tick_range']} last={laps[-1]['tick_range']} ticks={len(ticks)}"
        )
    expected_session_id = session["recorded_session_id"]
    for lap in laps:
        if lap["lap_id"] != f"{expected_session_id}:lap:{lap['lap']}":
            raise ValueError(f"lap_id does not match recorded_session_id/lap: {lap['lap_id']}")
        start, end = lap["tick_range"]
        if start < 0 or end > len(ticks):
            raise ValueError(f"lap tick_range outside ticks.parquet bounds: {lap['tick_range']}")
        group = ticks.iloc[start:end]
        if group.empty:
            raise ValueError(f"lap has empty tick_range: {lap['lap_id']}")
        if set(group["lap"].astype(int)) != {int(lap["lap"])}:
            raise ValueError(f"lap tick_range contains ticks for another lap: {lap['lap_id']}")
        validate_lap_time(lap, group)
        inferred_reason = infer_invalid_reason(group)
        if lap["valid"] is True and inferred_reason is not None:
            raise ValueError(
                f"lap marked valid but inferred invalid_reason={inferred_reason}: {lap['lap_id']}"
            )
        if lap["valid"] is False and lap["invalid_reason"] in INFERRED_INVALID_LAP_REASONS and lap[
            "invalid_reason"
        ] != inferred_reason:
            raise ValueError(
                f"lap invalid_reason mismatch for {lap['lap_id']}: "
                f"recorded={lap['invalid_reason']} inferred={inferred_reason}"
            )


def validate_session_yaml(session: dict[str, Any]) -> None:
    missing = REQUIRED_SESSION_FIELDS - set(session)
    if missing:
        raise ValueError(f"session.yaml missing fields: {sorted(missing)}")
    if session["complete"] is not True:
        raise ValueError("session.yaml complete must be true")
    if session["failure_reason"] is not None:
        raise ValueError("complete session must have failure_reason=null")
    recorded_session_id = str(session["recorded_session_id"])
    if RECORDED_SESSION_ID_PATTERN.fullmatch(recorded_session_id) is None:
        raise ValueError("recorded_session_id must be a lowercase ULID")
    if session["sim"] not in {"ams2", "acc", "ac"}:
        raise ValueError(f"unsupported sim: {session['sim']!r}")
    if session["session_type"] not in {"practice", "qualifying", "race", "hotlap"}:
        raise ValueError(f"unsupported session_type: {session['session_type']!r}")
    if session["tick_rate_hz"] != 60:
        raise ValueError(f"tick_rate_hz must be 60: {session['tick_rate_hz']!r}")
    if session["lap_dist_m_source"] not in {
        "sim",
        "derived_from_track_length",
        "unavailable",
    }:
        raise ValueError(f"unsupported lap_dist_m_source: {session['lap_dist_m_source']!r}")
    if session["validity_method"] not in {
        "sim_flag_plus_inferred",
        "inferred",
        "unknown_plus_inferred",
    }:
        raise ValueError(f"unsupported validity_method: {session['validity_method']!r}")
    for key in ("started_at", "ended_at"):
        value = session[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} must be an ISO timestamp string")
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{key} must be timezone-aware")
    if not session["track_raw"] or not session["track"]:
        raise ValueError("session.yaml track metadata is required")
    if not session["car_raw"] or not session["car"]:
        raise ValueError("session.yaml car metadata is required")


def validate_lap_record_shape(lap: dict[str, Any]) -> None:
    required = {
        "lap_id",
        "lap",
        "lap_time_s",
        "lap_time_source",
        "sector_times_s",
        "valid",
        "invalid_reason",
        "tick_range",
    }
    missing = required - set(lap)
    if missing:
        raise ValueError(f"lap record missing keys: {sorted(missing)}")
    if lap["valid"] is True and lap["invalid_reason"] is not None:
        raise ValueError(f"valid lap has invalid_reason: {lap['lap_id']}")
    if lap["valid"] is False and not lap["invalid_reason"]:
        raise ValueError(f"invalid lap lacks invalid_reason: {lap['lap_id']}")
    if lap["invalid_reason"] is not None and lap["invalid_reason"] not in INVALID_LAP_REASONS:
        raise ValueError(f"unsupported invalid_reason: {lap['invalid_reason']!r}")
    if lap["lap_time_source"] not in {"sim", "derived_from_ticks"}:
        raise ValueError(f"unsupported lap_time_source: {lap['lap_time_source']!r}")
    tick_range = lap["tick_range"]
    if (
        not isinstance(tick_range, list)
        or len(tick_range) != 2
        or not all(isinstance(value, int) for value in tick_range)
    ):
        raise ValueError(f"tick_range must be [int, int]: {tick_range!r}")


def validate_lap_time(lap: dict[str, Any], group: pd.DataFrame) -> None:
    if lap["lap_time_source"] != "derived_from_ticks":
        return
    expected = float(group["t"].iloc[-1] - group["t"].iloc[0])
    actual = float(lap["lap_time_s"])
    if abs(actual - expected) > 1e-6:
        raise ValueError(
            f"derived lap_time_s mismatch for {lap['lap_id']}: "
            f"recorded={actual:.6f} expected={expected:.6f}"
        )
