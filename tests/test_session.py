from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from simcoach.adapters.base import NormalizedTick, SessionInfo
from simcoach.ingest.session import (
    create_recorded_session,
    derive_lap_records,
    finalize_recorded_session,
)


def test_finalize_recorded_session_writes_complete_artifacts(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    ticks = build_ticks()

    final_yaml = finalize_recorded_session(paths, session_id, info, started_at, ticks)

    assert final_yaml["complete"] is True
    assert final_yaml["failure_reason"] is None
    assert paths.session_yaml.exists()
    assert paths.ticks_parquet.exists()
    assert paths.laps_jsonl.exists()

    loaded = yaml.safe_load(paths.session_yaml.read_text())
    assert loaded["recorded_session_id"] == session_id
    assert loaded["complete"] is True
    lines = paths.laps_jsonl.read_text().strip().splitlines()
    assert len(lines) == 2


def test_derive_lap_records_invalidates_non_running_lap() -> None:
    ticks = build_ticks(vehicle_state="paused")

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert all(not lap["valid"] for lap in laps)
    assert {lap["invalid_reason"] for lap in laps} == {"non_running_vehicle_state"}


def test_derive_lap_records_invalidates_pit_state_inside_completed_lap() -> None:
    ticks = build_ticks()
    ticks[3] = replace_tick(ticks[3], vehicle_state="pit")

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "non_running_vehicle_state"
    assert laps[1]["valid"] is True


def test_derive_lap_records_does_not_invalidate_slow_lap_by_itself() -> None:
    ticks = build_ticks()
    slow_ticks = [replace_tick(tick, t=tick.t * 20.0, speed=2.0) for tick in ticks]

    frame = pd.DataFrame([tick.as_record() for tick in slow_ticks])
    laps = derive_lap_records(frame, "01test")

    assert all(lap["valid"] is True for lap in laps)
    assert all(lap["invalid_reason"] is None for lap in laps)


def test_derive_lap_records_invalidates_sim_flagged_lap() -> None:
    ticks = build_ticks()

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test", sim_invalidated_laps={2})

    assert laps[0]["valid"] is True
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "sim_invalidated"


def test_derive_lap_records_invalidates_bad_lap_progress() -> None:
    ticks = build_ticks()
    ticks[10] = replace_tick(ticks[10], lap_dist_pct=0.99)

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "bad_lap_progress"
    assert laps[1]["valid"] is True


def test_derive_lap_records_invalidates_long_flat_lap_progress() -> None:
    ticks = build_ticks(rows_per_lap=220)
    for index in range(1, 190):
        ticks[index] = replace_tick(ticks[index], lap_dist_pct=ticks[0].lap_dist_pct)

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "bad_lap_progress"
    assert laps[1]["valid"] is True


def test_derive_lap_records_invalidates_teleport_or_reset() -> None:
    ticks = build_ticks()
    ticks[10] = replace_tick(ticks[10], pos_x=10_000.0, pos_y=10_000.0)

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "teleport_or_reset"
    assert laps[1]["valid"] is True


def test_finalize_recorded_session_marks_unknown_validity_method(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)

    final_yaml = finalize_recorded_session(
        paths,
        session_id,
        info,
        started_at,
        build_ticks(vehicle_state="unknown"),
    )

    assert final_yaml["complete"] is True
    assert final_yaml["validity_method"] == "unknown_plus_inferred"
    laps = [json.loads(line) for line in paths.laps_jsonl.read_text().splitlines()]
    assert all(lap["valid"] is True for lap in laps)


def build_ticks(
    vehicle_state: str = "running",
    *,
    rows_per_lap: int = 20,
) -> list[NormalizedTick]:
    ticks: list[NormalizedTick] = []
    t = 0.0
    for lap in (1, 2):
        for index in range(rows_per_lap):
            pct = index / rows_per_lap
            ticks.append(
                NormalizedTick(
                    t=t,
                    lap=lap,
                    lap_dist_pct=pct,
                    lap_dist_m=pct * 4309.0,
                    speed=40.0,
                    throttle=0.5,
                    brake=0.0,
                    steering=0.0,
                    gear=3,
                    rpm=8000.0,
                    vehicle_state=vehicle_state,  # type: ignore[arg-type]
                    pos_x=float(index + lap * 100),
                    pos_y=float(index),
                    pos_z=0.0,
                )
            )
            t += 1.0 / 60.0
    return ticks


def replace_tick(tick: NormalizedTick, **updates: object) -> NormalizedTick:
    values = tick.as_record()
    values.update(updates)
    return NormalizedTick(**values)


def session_info() -> SessionInfo:
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
        "validity_method": "inferred",
        "session_schema_version": 1,
        "tick_schema_version": 1,
        "lap_schema_version": 1,
    }
