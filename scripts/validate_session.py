#!/usr/bin/env python3
"""Backwards-compatible wrapper for the installed ``validate-session`` command."""

from simcoach.cli.validate_session import (
    build_parser,
    main,
    parse_args,
    validate_recorded_session,
)

__all__ = [
    "build_parser",
    "main",
    "parse_args",
    "validate_recorded_session",
]


if __name__ == "__main__":
    raise SystemExit(main())
