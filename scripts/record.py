#!/usr/bin/env python3
"""Backwards-compatible wrapper for the installed ``record`` command."""

from simcoach.cli.record import build_parser, main, parse_args

__all__ = ["build_parser", "main", "parse_args"]


if __name__ == "__main__":
    raise SystemExit(main())
