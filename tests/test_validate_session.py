from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.validate_session import validate_recorded_session
from simcoach.ingest.session import create_recorded_session, finalize_recorded_session
from tests.test_session import build_ticks, session_info


def test_validate_recorded_session_accepts_finalized_session(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())

    summary = validate_recorded_session(paths.root)

    assert summary == {
        "recorded_session_id": session_id,
        "tick_count": 40,
        "lap_count": 2,
        "valid_lap_count": 2,
    }


def test_validate_recorded_session_rejects_lap_time_drift(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    laps = [json.loads(line) for line in paths.laps_jsonl.read_text().splitlines()]
    laps[0]["lap_time_s"] += 1.0
    paths.laps_jsonl.write_text(
        "".join(json.dumps(lap, sort_keys=True) + "\n" for lap in laps),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="derived lap_time_s mismatch"):
        validate_recorded_session(paths.root)


def test_validate_recorded_session_rejects_complete_session_failure_reason(
    tmp_path: Path,
) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    session = yaml.safe_load(paths.session_yaml.read_text(encoding="utf-8"))
    session["failure_reason"] = "recording_interrupted"
    paths.session_yaml.write_text(yaml.safe_dump(session, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="failure_reason=null"):
        validate_recorded_session(paths.root)


def test_validate_recorded_session_rejects_uncovered_tick_rows(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    laps = [json.loads(line) for line in paths.laps_jsonl.read_text().splitlines()]
    laps[0]["tick_range"][0] = 1
    paths.laps_jsonl.write_text(
        "".join(json.dumps(lap, sort_keys=True) + "\n" for lap in laps),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cover every ticks.parquet row"):
        validate_recorded_session(paths.root)
