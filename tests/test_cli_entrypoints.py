from __future__ import annotations

import tomllib
from pathlib import Path

from scripts import analyze as analyze_script
from scripts import record as record_script
from scripts import validate_session as validate_session_script
from scripts.capture_acc_fixture import DEFAULT_OUTPUT_DIR as ACC_FIXTURE_DIR
from scripts.capture_acc_fixture import parse_args as parse_acc_fixture_args
from scripts.capture_ams2_fixture import DEFAULT_OUTPUT_DIR as AMS2_FIXTURE_DIR
from scripts.capture_ams2_fixture import parse_args as parse_ams2_fixture_args
from simcoach import validation
from simcoach.cli import analyze, record, validate_session

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_console_scripts_point_to_package_cli_orchestration() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["scripts"] == {
        "record": "simcoach.cli.record:main",
        "validate-session": "simcoach.cli.validate_session:main",
        "analyze": "simcoach.cli.analyze:main",
    }
    assert record_script.main is record.main
    assert analyze_script.main is analyze.main
    assert validate_session_script.main is validate_session.main
    assert (
        validate_session_script.validate_recorded_session
        is validate_session.validate_recorded_session
    )
    assert validate_session.validate_recorded_session is validation.validate_recorded_session


def test_raw_fixture_capture_defaults_are_ignored_scratch_paths() -> None:
    assert Path(".scratch/fixtures/ams2") == AMS2_FIXTURE_DIR
    assert Path(".scratch/fixtures/acc") == ACC_FIXTURE_DIR
    assert parse_ams2_fixture_args([]).out == AMS2_FIXTURE_DIR
    assert parse_acc_fixture_args([]).out == ACC_FIXTURE_DIR
