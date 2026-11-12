"""Validate a finalized Recorded Session."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from simcoach.validation import validate_recorded_session


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Validate finalized Recorded Session artifacts.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    parser.add_argument("session_dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = validate_recorded_session(args.session_dir)
    print(
        f"{args.session_dir} | valid session_id={summary['recorded_session_id']} "
        f"ticks={summary['tick_count']} laps={summary['lap_count']} "
        f"valid_laps={summary['valid_lap_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
