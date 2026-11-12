#!/usr/bin/env python3
"""Backwards-compatible wrapper for the installed ``analyze`` command."""

from simcoach.cli.analyze import build_parser, main, parse_args, validate_cli_args

__all__ = ["build_parser", "main", "parse_args", "validate_cli_args"]


if __name__ == "__main__":
    raise SystemExit(main())
