from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from simcoach.adapters.base import NormalizedTick, SessionInfo
from simcoach.ingest.session import (
    create_recorded_session,
    derive_lap_records,
    finalize_recorded_session,
    validate_tick_invariants,
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
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


def test_derive_lap_records_does_not_invalidate_slow_lap_by_itself() -> None:
    ticks = build_ticks()
    slow_ticks = [replace_tick(tick, t=tick.t * 20.0, speed=2.0) for tick in ticks]

    frame = pd.DataFrame([tick.as_record() for tick in slow_ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is True
    assert laps[0]["invalid_reason"] is None
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


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
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


def test_derive_lap_records_invalidates_long_flat_lap_progress() -> None:
    ticks = build_ticks(rows_per_lap=220)
    for index in range(1, 190):
        ticks[index] = replace_tick(ticks[index], lap_dist_pct=ticks[0].lap_dist_pct)

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "bad_lap_progress"
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


def test_derive_lap_records_invalidates_teleport_or_reset() -> None:
    ticks = build_ticks()
    ticks[10] = replace_tick(ticks[10], pos_x=10_000.0, pos_y=10_000.0)

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "teleport_or_reset"
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


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
    assert laps[0]["valid"] is True
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


def test_derive_lap_records_invalidates_capture_boundary_fragments() -> None:
    ticks = build_ticks()
    partial_first_lap = [
        replace_tick(tick, lap_dist_pct=tick.lap_dist_pct + 0.1)
        for tick in ticks[:10]
    ]
    ticks = partial_first_lap + ticks[20:]

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "metadata_boundary"
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


def test_derive_lap_records_accepts_final_lap_with_observed_finish() -> None:
    ticks = build_ticks()
    ticks.append(
        replace_tick(
            ticks[-1],
            t=ticks[-1].t + 1.0 / 60.0,
            lap_dist_pct=1.0,
            lap_dist_m=4309.0,
        )
    )

    frame = pd.DataFrame([tick.as_record() for tick in ticks])
    laps = derive_lap_records(frame, "01test")

    assert all(lap["valid"] is True for lap in laps)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("t", float("nan"), "t must not contain null"),
        ("speed", float("inf"), "speed must contain only finite values"),
        ("lap", 0, "lap must be 1-indexed"),
        ("lap", 2_147_483_648, "lap exceeds the int32 schema range"),
        ("gear", -2, "gear must be -1, 0, or a forward gear"),
        ("gear", 128, "gear exceeds the int8 schema range"),
        ("throttle", -0.01, "throttle outside 0.0..1.0"),
        ("brake", 1.01, "brake outside 0.0..1.0"),
        ("steering", 1.01, "steering outside -1.0..1.0"),
        ("vehicle_state", "garage", "unsupported vehicle_state"),
    ],
)
def test_validate_tick_invariants_rejects_invalid_contract_values(
    field: str,
    value: object,
    message: str,
) -> None:
    ticks = build_ticks()
    ticks[0] = replace_tick(ticks[0], **{field: value})
    frame = pd.DataFrame([tick.as_record() for tick in ticks])

    with pytest.raises(ValueError, match=message):
        validate_tick_invariants(frame)


def test_validate_tick_invariants_rejects_non_integer_lap_and_gear() -> None:
    frame = pd.DataFrame([tick.as_record() for tick in build_ticks()])
    frame["lap"] = frame["lap"].astype(float)

    with pytest.raises(ValueError, match="lap must use an integer dtype"):
        validate_tick_invariants(frame)

    frame = pd.DataFrame([tick.as_record() for tick in build_ticks()])
    frame["gear"] = frame["gear"].astype(float)
    with pytest.raises(ValueError, match="gear must use an integer dtype"):
        validate_tick_invariants(frame)


def test_validate_tick_invariants_rejects_lap_reversal() -> None:
    frame = pd.DataFrame([tick.as_record() for tick in build_ticks()])
    frame.loc[25, "lap"] = 1

    with pytest.raises(ValueError, match="lap must be monotonic"):
        validate_tick_invariants(frame)


def test_finalize_recorded_session_keeps_bad_progress_as_invalid_lap(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    ticks = build_ticks()
    ticks[10] = replace_tick(ticks[10], lap_dist_pct=0.99)

    final_yaml = finalize_recorded_session(paths, session_id, info, started_at, ticks)

    assert final_yaml["complete"] is True
    laps = [json.loads(line) for line in paths.laps_jsonl.read_text().splitlines()]
    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "bad_lap_progress"
    assert laps[1]["valid"] is False
    assert laps[1]["invalid_reason"] == "metadata_boundary"


def test_validate_tick_invariants_accepts_nullable_distance_and_legitimate_states() -> None:
    ticks = build_ticks()
    states = ["running", "pit", "paused", "menu", "replay", "unknown"]
    gears = [-1, 0, 1, 2, 3, 4]
    for index, (state, gear) in enumerate(zip(states, gears, strict=True)):
        ticks[index] = replace_tick(
            ticks[index],
            lap_dist_m=None,
            vehicle_state=state,
            gear=gear,
        )

    validate_tick_invariants(pd.DataFrame([tick.as_record() for tick in ticks]))


def test_finalize_recorded_session_rejects_invalid_ticks_before_parquet_write(
    tmp_path: Path,
) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    ticks = build_ticks()
    ticks[0] = replace_tick(ticks[0], throttle=1.1)

    final_yaml = finalize_recorded_session(paths, session_id, info, started_at, ticks)

    assert final_yaml["complete"] is False
    assert final_yaml["failure_reason"] == "artifact_write_failed"
    assert not paths.ticks_parquet.exists()


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
