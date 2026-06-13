from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from simcoach.adapters.base import NormalizedTick
from simcoach.analysis.run import analyze_session, render_coach_report
from simcoach.ingest.session import (
    atomic_write_jsonl,
    atomic_write_yaml,
    create_recorded_session,
    finalize_recorded_session,
    paths_for,
)
from tests.test_session import session_info


def test_analyze_session_writes_complete_deterministic_artifacts(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(
        paths, session_id, info, started_at, build_synthetic_reportable_ticks()
    )

    result = subprocess.run(
        [sys.executable, "scripts/analyze.py", str(paths.root)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "reportable" in result.stdout
    analysis_root = paths.root / "analysis"
    run_dirs = list(analysis_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    analysis_yaml = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    selected_delta = json.loads((run_dir / "selected_delta.json").read_text())
    corner_segments = json.loads((run_dir / "corner_segments.json").read_text())
    report = (run_dir / "coach_report.md").read_text()

    assert analysis_yaml["run_status"] == "complete"
    assert analysis_yaml["analysis_status"] == "reportable"
    assert selected_delta["analysis_status"] == "reportable"
    assert selected_delta["selected_delta"]["dominant_cause"] == "min_speed"
    assert len(corner_segments["segments"]) >= 1
    assert "## The one thing" in report
    assert "carry more minimum speed" in report
    assert (paths.root / "coach_report.md").read_text() == report


def test_failed_analysis_run_writes_diagnostic_yaml_only(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    atomic_write_yaml(
        paths.session_yaml,
        {
            "session_schema_version": 1,
            "tick_schema_version": 1,
            "lap_schema_version": 1,
            "recorded_session_id": session_id,
            "sim": "ams2",
            "track_raw": "Interlagos GP",
            "track": "interlagos_gp",
            "car_raw": "Formula Inter",
            "car": "formula_inter",
            "session_type": "practice",
            "started_at": started_at.isoformat(),
            "ended_at": started_at.isoformat(),
            "tick_rate_hz": 60,
            "track_length_m": 4309.0,
            "lap_dist_m_source": "sim",
            "adapter_version": "0.1.0",
            "validity_method": "inferred",
            "complete": True,
            "failure_reason": None,
            "notes": "",
        },
    )
    pd.DataFrame(
        {
            "t": [0.0, 1.0],
            "lap": [1, 1],
            "lap_dist_pct": [0.0, 0.5],
            "speed": [10.0, 20.0],
        }
    ).to_parquet(paths.ticks_parquet, index=False)
    atomic_write_jsonl(
        paths.laps_jsonl,
        [
            {
                "lap_id": f"{session_id}:lap:1",
                "lap": 1,
                "lap_time_s": 1.0,
                "lap_time_source": "derived_from_ticks",
                "sector_times_s": None,
                "valid": True,
                "invalid_reason": None,
                "tick_range": [0, 2],
            }
        ],
    )

    try:
        analyze_session(paths.root)
    except KeyError:
        pass
    else:
        raise AssertionError("malformed ticks should fail deterministic analysis")

    run_dirs = list((paths.root / "analysis").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    analysis_yaml = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis_yaml["run_status"] == "failed"
    assert analysis_yaml["run_error"] == "invalid_artifact_contract"
    assert analysis_yaml["analysis_status"] is None
    assert not (run_dir / "corner_segments.json").exists()
    assert not (run_dir / "selected_delta.json").exists()
    assert not (run_dir / "coach_report.md").exists()


def test_incomplete_and_unsupported_targets_refuse_before_analysis_run(
    tmp_path: Path,
) -> None:
    incomplete = create_finalized_session(tmp_path, "incomplete")
    patch_session_yaml(incomplete, complete=False, failure_reason="recording_interrupted")

    with pytest.raises(ValueError, match="incomplete"):
        analyze_session(incomplete)

    assert not (incomplete / "analysis").exists()

    unsupported = create_finalized_session(tmp_path, "unsupported")
    patch_session_yaml(unsupported, session_schema_version=99)

    with pytest.raises(ValueError, match="unsupported session_schema_version"):
        analyze_session(unsupported)

    assert not (unsupported / "analysis").exists()


def test_personal_reference_includes_target_outside_sessions_root(tmp_path: Path) -> None:
    target = create_finalized_session(tmp_path / "external", "target")
    empty_sessions_root = tmp_path / "sessions"
    empty_sessions_root.mkdir()

    run_dir = analyze_session(target, reference_mode="personal", sessions_root=empty_sessions_root)

    analysis_yaml = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis_yaml["reference_lap_source_path"] == str(target)


def test_best_reference_ties_break_by_lowest_lap_number(tmp_path: Path) -> None:
    target = create_finalized_session(tmp_path, "target")
    patch_lap_times(target, {1: 80.0, 2: 80.0, 3: 81.0})

    run_dir = analyze_session(target, reference_mode="best")

    session = yaml.safe_load((target / "session.yaml").read_text())
    analysis_yaml = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis_yaml["reference_lap_id"] == f"{session['recorded_session_id']}:lap:1"
    assert analysis_yaml["reference_lap_source_path"] == str(target)


def test_personal_reference_ties_break_by_earliest_started_at(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    newer = create_finalized_session(sessions_root, "a_newer")
    older = create_finalized_session(sessions_root, "b_older")
    target = create_finalized_session(tmp_path / "external", "target")
    patch_first_lap_time(newer, 80.0)
    patch_first_lap_time(older, 80.0)
    patch_first_lap_time(target, 90.0)
    patch_session_yaml(newer, started_at="2026-06-12T10:00:00-07:00")
    patch_session_yaml(older, started_at="2026-06-12T09:00:00-07:00")

    run_dir = analyze_session(target, reference_mode="personal", sessions_root=sessions_root)

    analysis_yaml = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis_yaml["reference_lap_source_path"] == str(older)


def test_personal_reference_skips_unsupported_and_duplicate_candidates(
    tmp_path: Path,
) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    original = create_finalized_session(sessions_root, "a_original")
    duplicate = create_finalized_session(sessions_root, "z_duplicate")
    unsupported = create_finalized_session(sessions_root, "m_unsupported")
    target = create_finalized_session(tmp_path / "external", "target")

    original_id = yaml.safe_load((original / "session.yaml").read_text())["recorded_session_id"]
    patch_first_lap_time(original, 80.0)
    patch_first_lap_time(duplicate, 70.0)
    patch_session_yaml(duplicate, recorded_session_id=original_id)
    patch_first_lap_time(unsupported, 10.0)
    patch_session_yaml(unsupported, session_schema_version=99)
    patch_first_lap_time(target, 90.0)

    run_dir = analyze_session(target, reference_mode="personal", sessions_root=sessions_root)

    analysis_yaml = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis_yaml["reference_lap_source_path"] == str(original)


def test_unavailable_lap_distance_report_uses_percentage_not_meters() -> None:
    info = session_info()
    session = {
        **info,
        "recorded_session_id": "session01",
        "started_at": "2026-06-12T10:00:00-07:00",
        "complete": True,
    }
    selected_delta = {
        "analysis_status": "reportable",
        "reference_lap_id": "session01:lap:1",
        "selected_delta": {
            "corner_segment_id": "C1",
            "dominant_cause": "brake_point",
            "comparison_lap_count": 5,
            "median_corner_loss_s": 0.25,
            "robust_noise_s": 0.01,
            "cause_metric": {
                "metric": "brake_point",
                "unit": "lap_dist_pct",
                "reference_value": 0.534,
                "comparison_median": 0.52,
                "signed_delta": -0.014,
                "bad_direction_delta": 0.014,
                "lap_dist_m_source": "unavailable",
            },
            "lap_dist_m_source": "unavailable",
            "runner_up_margin_s": None,
        },
        "corner_summaries": [],
    }

    report = render_coach_report(
        session,
        Path("analysis/2026-06-12_100000_best"),
        [{"valid": True, "lap_time_s": 90.0}],
        "best",
        selected_delta,
    )

    assert "lap_dist_pct" in report
    assert "meter" not in report.lower()


def create_finalized_session(out_dir: Path, name: str) -> Path:
    info = session_info()
    out_dir.mkdir(parents=True, exist_ok=True)
    session_id, started_at, paths = create_recorded_session(out_dir, info)
    finalize_recorded_session(
        paths,
        session_id,
        info,
        started_at,
        build_synthetic_reportable_ticks(),
    )
    renamed = out_dir / name
    paths.root.rename(renamed)
    return renamed


def patch_session_yaml(session_dir: Path, **updates: object) -> None:
    session = yaml.safe_load((session_dir / "session.yaml").read_text())
    session.update(updates)
    atomic_write_yaml(session_dir / "session.yaml", session)


def patch_first_lap_time(session_dir: Path, lap_time_s: float) -> None:
    patch_lap_times(session_dir, {1: lap_time_s})


def patch_lap_times(session_dir: Path, lap_times: dict[int, float]) -> None:
    path = paths_for(session_dir).laps_jsonl
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows:
        if row["lap"] in lap_times:
            row["lap_time_s"] = lap_times[row["lap"]]
    atomic_write_jsonl(path, rows)


def build_synthetic_reportable_ticks() -> list[NormalizedTick]:
    ticks: list[NormalizedTick] = []
    rows_per_lap = 240
    base_t = 0.0
    for lap in range(1, 7):
        is_reference = lap == 1
        lap_duration = 90.0 if is_reference else 90.18
        for index in range(rows_per_lap):
            pct = index / rows_per_lap
            in_loss_region = 0.18 <= pct <= 0.42
            corner_delay = 0.0 if is_reference else 0.18 * smoothstep(0.18, 0.42, pct)
            elapsed = pct * lap_duration + corner_delay
            angle = 2.0 * math.pi * pct
            speed = 45.0
            if in_loss_region:
                speed = 42.0 if is_reference else 39.0
            ticks.append(
                NormalizedTick(
                    t=base_t + elapsed,
                    lap=lap,
                    lap_dist_pct=pct,
                    lap_dist_m=pct * 4309.0,
                    speed=speed,
                    throttle=0.7,
                    brake=0.0,
                    steering=0.2,
                    gear=4,
                    rpm=8500.0,
                    vehicle_state="running",
                    pos_x=100.0 * math.cos(angle),
                    pos_y=100.0 * math.sin(angle),
                    pos_z=0.0,
                )
            )
        base_t += lap_duration + 1.0
    return ticks


def smoothstep(start: float, end: float, value: float) -> float:
    if value <= start:
        return 0.0
    if value >= end:
        return 1.0
    x = (value - start) / (end - start)
    return x * x * (3.0 - 2.0 * x)
