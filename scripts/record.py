#!/usr/bin/env python3
"""Record simulator telemetry."""

from __future__ import annotations

import argparse
from pathlib import Path

from simcoach.adapters.acc import ACCAdapter
from simcoach.adapters.ams2 import AMS2Adapter
from simcoach.ingest.recorder import Recorder, RecorderConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", choices=["ams2", "acc"], required=True)
    parser.add_argument("--out", type=Path, default=Path("data/sessions"))
    parser.add_argument("--max-seconds", type=float, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
