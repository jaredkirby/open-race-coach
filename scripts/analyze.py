#!/usr/bin/env python3
"""Run deterministic SIM-COACH analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

from simcoach.analysis.run import analyze_session
from simcoach.coach.coach import (
    import_chatgpt_response,
    refine_with_openai_api,
    start_chatgpt_refinement,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--coach", action="store_true")
    parser.add_argument("--coach-mode", choices=["api", "chatgpt"])
    parser.add_argument("--reference", choices=["best", "personal"], default="best")
    parser.add_argument("--sessions-root", type=Path)
    parser.add_argument("--analysis-run", type=Path)
    parser.add_argument("--chatgpt-response", type=Path)
    args = parser.parse_args()
    validate_cli_args(args)

    if args.chatgpt_response is not None:
        import_chatgpt_response(args.session_dir, args.analysis_run, args.chatgpt_response)
        return 0

    if args.analysis_run is not None:
        if args.coach_mode == "api":
            refine_with_openai_api(args.session_dir, args.analysis_run)
        elif args.coach_mode == "chatgpt":
            start_chatgpt_refinement(args.session_dir, args.analysis_run)
        return 0

    analysis_run = analyze_session(
        args.session_dir,
        reference_mode=args.reference,
        sessions_root=args.sessions_root,
    )
    if args.coach_mode == "api":
        refine_with_openai_api(args.session_dir, analysis_run)
    elif args.coach_mode == "chatgpt":
        start_chatgpt_refinement(args.session_dir, analysis_run)
    return 0


def validate_cli_args(args: argparse.Namespace) -> None:
    if args.coach_mode is not None and not args.coach:
        raise SystemExit("--coach-mode requires --coach")
    if args.coach and args.coach_mode is None:
        raise SystemExit("--coach requires --coach-mode")
    if args.chatgpt_response is not None:
        if args.analysis_run is None:
            raise SystemExit("--chatgpt-response requires --analysis-run")
        if args.coach or args.coach_mode is not None:
            raise SystemExit("--chatgpt-response cannot be combined with --coach or --coach-mode")
        if args.reference != "best" or args.sessions_root is not None:
            raise SystemExit("--reference and --sessions-root are invalid with --chatgpt-response")
        return
    if args.analysis_run is not None:
        if not args.coach:
            raise SystemExit("--analysis-run refinement requires --coach")
        if args.reference != "best" or args.sessions_root is not None:
            raise SystemExit("--reference and --sessions-root are invalid with --analysis-run")
        return
    if args.sessions_root is not None and args.reference != "personal":
        raise SystemExit("--sessions-root is valid only with --reference personal")


if __name__ == "__main__":
    raise SystemExit(main())
