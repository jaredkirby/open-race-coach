from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scripts.analyze import validate_cli_args


def args(**overrides: object) -> argparse.Namespace:
    defaults = {
        "session_dir": Path("session"),
        "coach": False,
        "coach_mode": None,
        "reference": "best",
        "sessions_root": None,
        "analysis_run": None,
        "chatgpt_response": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_coach_mode_requires_coach_flag() -> None:
    with pytest.raises(SystemExit, match="--coach-mode requires --coach"):
        validate_cli_args(args(coach_mode="api"))


def test_coach_requires_explicit_mode() -> None:
    with pytest.raises(SystemExit, match="--coach requires --coach-mode"):
        validate_cli_args(args(coach=True))


def test_chatgpt_response_refuses_generation_flags() -> None:
    with pytest.raises(SystemExit, match="cannot be combined"):
        validate_cli_args(
            args(
                coach=True,
                coach_mode="chatgpt",
                analysis_run=Path("run"),
                chatgpt_response=Path("response.json"),
            )
        )


def test_analysis_run_refuses_reference_options() -> None:
    with pytest.raises(SystemExit, match="invalid with --analysis-run"):
        validate_cli_args(
            args(
                coach=True,
                coach_mode="api",
                analysis_run=Path("run"),
                reference="personal",
            )
        )


def test_sessions_root_requires_personal_reference() -> None:
    with pytest.raises(SystemExit, match="--sessions-root is valid only"):
        validate_cli_args(args(sessions_root=Path("sessions")))


def test_valid_new_chatgpt_analysis_args() -> None:
    validate_cli_args(args(coach=True, coach_mode="chatgpt"))
