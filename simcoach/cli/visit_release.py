"""Prepare/verify a visit snapshot or explicitly invoke one selected role."""
import argparse
import json
from pathlib import Path

from simcoach.live.visit_release import ROLES, prepare_release, run_release, verify_release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--repo", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("release", type=Path)
    run = actions.add_parser("run")
    run.add_argument("--release-id", required=True)
    run.add_argument("--attempt", type=Path, required=True)
    run.add_argument("--module", choices=sorted(ROLES), required=True)
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.action == "run":
        arguments = args.arguments
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        return run_release(args.release_id, args.module, args.attempt, arguments)
    result = (prepare_release(args.repo, args.out) if args.action == "prepare"
              else verify_release(args.release))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
