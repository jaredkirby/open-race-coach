"""Prepare/verify source, or explicitly run one selected collector snapshot."""
import argparse
import json
from pathlib import Path

from simcoach.live.collector_release import prepare_release, verify_release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--repo", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("release", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--release-id", required=True)
    run.add_argument("--attempt", type=Path, required=True)
    run.add_argument("--hz", type=float, default=60)
    args = parser.parse_args(argv)
    if args.action == "run":
        from simcoach.live.collector_launch import run_release
        return run_release(args.release_id, args.attempt, hz=args.hz)
    result = prepare_release(args.repo, args.out) if args.action == "prepare" \
        else verify_release(args.release)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
