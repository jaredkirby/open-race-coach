from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.validate_session import validate_recorded_session
from simcoach.ingest.session import create_recorded_session, finalize_recorded_session
from simcoach.validation import validate_recorded_session as validate_recorded_session_package
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
        "valid_lap_count": 1,
    }
    assert validate_recorded_session_package(paths.root) == summary


def test_package_validator_treats_session_path_as_a_locator(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    moved = tmp_path / "moved_recorded_session"
    paths.root.rename(moved)

    summary = validate_recorded_session_package(moved)

    assert summary["recorded_session_id"] == session_id


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


@pytest.mark.parametrize("invalid_reason", ["metadata_boundary", "unknown_invalidity"])
def test_validate_recorded_session_accepts_external_invalidity_provenance(
    tmp_path: Path,
    invalid_reason: str,
) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    laps = [json.loads(line) for line in paths.laps_jsonl.read_text().splitlines()]
    laps[0]["valid"] = False
    laps[0]["invalid_reason"] = invalid_reason
    paths.laps_jsonl.write_text(
        "".join(json.dumps(lap, sort_keys=True) + "\n" for lap in laps),
        encoding="utf-8",
    )

    summary = validate_recorded_session(paths.root)

    assert summary["valid_lap_count"] == 0


def test_validate_recorded_session_rejects_duplicate_lap_identity(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    laps = [json.loads(line) for line in paths.laps_jsonl.read_text().splitlines()]
    laps[1]["lap"] = laps[0]["lap"]
    laps[1]["lap_id"] = laps[0]["lap_id"]
    paths.laps_jsonl.write_text(
        "".join(json.dumps(lap, sort_keys=True) + "\n" for lap in laps),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate lap numbers"):
        validate_recorded_session(paths.root)


def test_validate_recorded_session_rejects_non_ulid_identity(tmp_path: Path) -> None:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(paths, session_id, info, started_at, build_ticks())
    session = yaml.safe_load(paths.session_yaml.read_text(encoding="utf-8"))
    session["recorded_session_id"] = "not-a-ulid"
    paths.session_yaml.write_text(yaml.safe_dump(session, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="lowercase ULID"):
        validate_recorded_session(paths.root)
