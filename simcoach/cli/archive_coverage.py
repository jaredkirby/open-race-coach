"""Audit explicit closed archive segments offline; never starts or stops capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simcoach.live.archive_coverage import (
    analyze_archive_coverage,
    write_archive_coverage_report,
)
from simcoach.live.visit_coverage import audit_visit_coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--start-at", help="Explicit UTC window start, ISO 8601")
    parser.add_argument("--end-at", help="Explicit UTC window end, ISO 8601")
    parser.add_argument("--segment", action="append",
                        help="Closed manifested filename; repeat for each selected segment")
    parser.add_argument("--visit", type=Path, help="Use the declared envelope of a closed visit")
    parser.add_argument(
        "--archive-sha256", help="Selected archive run.json SHA-256 for the visit audit")
    parser.add_argument(
        "--arrival", type=Path, help="Optional reviewed earlier arrival declaration")
    parser.add_argument("--max-gap", type=float, default=0.1)
    parser.add_argument("--out", type=Path, required=True, help="New private report directory")
    args = parser.parse_args(argv)
    try:
        if args.visit is not None:
            if (args.start_at or args.end_at or args.segment or args.max_gap != .1
                    or not args.archive_sha256):
                parser.error(
                    "--visit requires --archive-sha256 and uses its own window and gap policy")
            report = audit_visit_coverage(args.visit, args.archive, source_id=args.archive_sha256,
                                          output_dir=args.out, arrival=args.arrival)
            print(json.dumps(dict(report_path=str(args.out / "report.json"),
                                  result=report["result"],
                                  metric_known=report["metric_known"], live_validation="NOT RUN")))
            return 0 if report["result"] == "PASS" else 1
        if (args.arrival or args.archive_sha256
                or not (args.start_at and args.end_at and args.segment)):
            parser.error("Provide either --visit or explicit --start-at, --end-at and --segment")
        report = analyze_archive_coverage(
            args.archive, start_at=args.start_at, end_at=args.end_at,
            segment_names=args.segment, max_gap_s=args.max_gap)
        written = write_archive_coverage_report(report, args.out)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        parser.error(f"Coverage audit refused: {type(exc).__name__}")
    print(json.dumps(dict(report_path=str(written["report_path"]),
                          integrity_result=report["integrity_result"],
                          metric_known=report["metric_known"],
                          scope_complete=report["scope_complete"])))
    return 0 if (report["integrity_result"] == "PASS" and report["metric_known"]
                 and report["scope_complete"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
