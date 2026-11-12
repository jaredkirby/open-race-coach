"""Record simulator telemetry."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from simcoach.adapters.acc import ACCAdapter
from simcoach.adapters.ams2 import AMS2Adapter
from simcoach.ingest.recorder import Recorder, RecorderConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record simulator telemetry.")
    parser.add_argument("--sim", choices=["ams2", "acc"], required=True)
    parser.add_argument("--out", type=Path, default=Path("data/sessions"))
    parser.add_argument("--max-seconds", type=float, default=None, help=argparse.SUPPRESS)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    adapter = AMS2Adapter() if args.sim == "ams2" else ACCAdapter()
    recorder = Recorder(
        adapter,
        RecorderConfig(out_dir=args.out, max_seconds=args.max_seconds),
    )
    session_dir = recorder.run()
    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
