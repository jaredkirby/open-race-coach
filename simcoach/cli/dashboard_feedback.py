"""Prepare a private feedback card or explicitly select an already reviewed one."""
import argparse
import hashlib
import json
from pathlib import Path

from simcoach.live.consistency import compare_plan, prepare_plan
from simcoach.live.dashboard_feedback import prepare_feedback, select_feedback
from simcoach.live.visit_review import _io


def main(argv=None):
    from simcoach.live.visit_release import require_guarded_entry
    require_guarded_entry()
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--visit", type=Path, required=True)
    prepare.add_argument("--session", type=Path, required=True)
    prepare.add_argument("--target-laps", type=int, default=3)
    prepare.add_argument("--target-spread", type=float, default=.5)
    select = actions.add_parser("select")
    select.add_argument("--visit", type=Path, required=True)
    select.add_argument("--review", type=Path, required=True)
    objective = actions.add_parser(
        "plan-consistency", help="Freeze baseline and future lap numbers")
    comparison = actions.add_parser("compare-consistency", help="Grade only the frozen lap numbers")
    for command in (objective, comparison):
        command.add_argument("--visit", type=Path, required=True)
        command.add_argument("--session", type=Path, required=True)
    objective.add_argument("--baseline-first", type=int, required=True)
    objective.add_argument("--followup-first", type=int, required=True)
    objective.add_argument("--count", type=int, required=True)
    objective.add_argument("--target-spread", type=float, required=True)
    objective.add_argument("--conditions", required=True)
    comparison.add_argument("--baseline-visit", type=Path, required=True)
    comparison.add_argument("--plan", type=Path, required=True)
    comparison.add_argument("--plan-sha256", required=True)
    comparison.add_argument(
        "--conditions-match", choices=("matched", "different", "unknown"), required=True)
    comparison.add_argument("--evidence", required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        path = prepare_feedback(args.visit, args.session, target_laps=args.target_laps,
                                target_spread_s=args.target_spread)
    elif args.action == "select":
        path = select_feedback(args.visit, args.review)
    elif args.action == "plan-consistency":
        path = prepare_plan(args.visit, args.session, baseline_first=args.baseline_first,
                            followup_first=args.followup_first, count=args.count,
                            spread_s=args.target_spread, conditions=args.conditions)
    else:
        path = compare_plan(args.baseline_visit, args.plan, args.visit, args.session,
                            plan_sha256=args.plan_sha256, conditions_match=args.conditions_match,
                            evidence=args.evidence)
    result = dict(path=str(path), driver_delivery="NOT RUN")
    if args.action == "plan-consistency":
        result["plan_sha256"] = hashlib.sha256(_io(path).read_bytes()).hexdigest()
    elif args.action == "compare-consistency":
        comparison = json.loads(_io(path).read_text(encoding="utf-8"))
        result.update({key: comparison[key] for key in ("status", "reason", "change")})
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
