"""Review bounded offline fuel evidence; never approves fuel or touches the simulator."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.fuel_budget import analyze_fuel_budget, draft_plan, write_report
from simcoach.live.supervisor import Context, strict_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--segment", action="append", required=True)
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--end-at", required=True)
    parser.add_argument("--context", type=Path, required=True, help="Exact Context JSON object")
    parser.add_argument("--participant-index", type=int, required=True)
    parser.add_argument("--timed-laps", type=int, required=True)
    parser.add_argument("--out-laps", type=int, default=1)
    parser.add_argument("--in-laps", type=int, default=1)
    parser.add_argument("--reserve-laps", type=int, default=2)
    parser.add_argument("--margin", type=float, default=.25)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        context = Context(**strict_json(args.context.read_text(encoding="utf8")))
        report = analyze_fuel_budget(args.archive, segment_names=args.segment,
            start_at=args.start_at, end_at=args.end_at, context=context,
            participant_index=args.participant_index, margin_fraction=args.margin)
        report_path = write_report(report, args.out)
        candidate = None
        if report["estimate"]["status"] == "review_required":
            plan = draft_plan(report, timed_laps=args.timed_laps, out_laps=args.out_laps,
                              in_laps=args.in_laps, reserve_laps=args.reserve_laps)
            # Separate wrapper prevents treating an unreviewed suggestion as an
            # already accepted FuelPlan. No application/approval is performed.
            candidate = report_path.parent / "candidate.json"
            write_status(candidate, dict(schema_version=1, status="chief_review_required",
                plan=dict(schema_version=1, **asdict(plan)),
                required_load_litres=plan.required_load,
                fuel_approval=False, evidence_path=str(report_path),
                evidence_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest()))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(f"Fuel evidence refused: {type(exc).__name__}")
    print(json.dumps(dict(report_path=str(report_path), candidate_path=str(candidate)
        if candidate else None, integrity=report["integrity_result"],
        estimate=report["estimate"]["status"])))
    return 0 if candidate else 1


if __name__ == "__main__":
    raise SystemExit(main())
