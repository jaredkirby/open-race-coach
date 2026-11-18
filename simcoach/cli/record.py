"""Record simulator telemetry."""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path

from simcoach.adapters.acc import ACCAdapter
from simcoach.adapters.ams2 import AMS2Adapter
from simcoach.ingest.recorder import Recorder, RecorderConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record simulator telemetry.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--sim", choices=["ams2", "acc"])
    action.add_argument("--stop", type=Path, metavar="CONTROL_DIR",
                        help="Save a cooperative stop request; does not wait for completion")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--control-dir", type=Path,
                        help="Fresh private run directory; continuous capture with stop control")
    parser.add_argument("--max-seconds", type=float, default=None, help=argparse.SUPPRESS)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.stop and (args.out is not None or args.control_dir or args.max_seconds is not None):
        parser.error("--stop cannot be combined with recording options")
    if args.control_dir and (args.out is not None or args.max_seconds is not None):
        parser.error("--control-dir owns its sessions directory and has no duration cutoff")
    if args.max_seconds is not None and (
            not math.isfinite(args.max_seconds) or args.max_seconds <= 0):
        parser.error("--max-seconds must be finite and positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.stop:
        from simcoach.live.record_control import request_stop
        print(request_stop(args.stop))
        return 0
    if args.control_dir:
        from simcoach.live.record_control import controlled_record
        factory = AMS2Adapter if args.sim == "ams2" else ACCAdapter
        return controlled_record(args.control_dir, sim=args.sim, adapter_factory=factory)
    adapter = AMS2Adapter() if args.sim == "ams2" else ACCAdapter()
    recorder = Recorder(
        adapter,
        RecorderConfig(out_dir=args.out or Path("data/sessions"), max_seconds=args.max_seconds),
    )
    session_dir = recorder.run()
    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
