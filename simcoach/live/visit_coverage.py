"""Bounded, offline coverage across a declared Driver Visit envelope."""

from __future__ import annotations

import hashlib
import math
import time
from datetime import datetime

from simcoach.live.archive_coverage import (
    ALIGNMENT_TOLERANCE_S,
    MAX_METADATA_BYTES,
    MAX_SEGMENTS,
    _Budget,
    _compressed,
    _Limit,
    _manifest,
    _small,
    _source,
    _stamp,
    _utc,
    analyze_archive_coverage,
    write_archive_coverage_report,
)
from simcoach.live.supervisor import number, strict_json
from simcoach.live.visit_arrival import verify_arrival_claim
from simcoach.live.visit_review import _departure, _path, _publish, _visit_id

TILE_SECONDS = 300
MAX_TILES = 30
MAX_WORK_SECONDS = 120


def _read_small(path):
    raw = _small(path)
    return strict_json(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()


def _manifest_or_empty(archive):
    try:
        return _manifest(archive)
    except FileNotFoundError:
        return [], None


def _windows(start, end, rows):
    # Closure times select candidates only. Include neighboring segments to
    # preserve verifiable bridges at tile edges and late closure boundaries.
    closures = [_utc(row["closed_at"]) for row in rows]
    if closures != sorted(closures):
        raise ValueError("manifest_closure_time_regressed")
    result = []
    cursor = start
    while cursor < end:
        following = min(end, cursor + TILE_SECONDS)
        candidates = [i for i, at in enumerate(closures) if at >= cursor
                      and (i == 0 or closures[i - 1] <= following)]
        names = []
        if candidates:
            names = [row["path"] for row in rows[max(0, candidates[0]-1):candidates[-1]+2]]
        result.append(dict(index=len(result), start_at=_stamp(cursor), end_at=_stamp(following),
                           duration_s=following-cursor, segment_names=names))
        cursor = following
    return result


def _envelope(visit, archive, arrival_path):
    host, host_sha = _read_small(visit / "status.json")
    if (not isinstance(host, dict) or type(host.get("schema_version")) is not int
            or host["schema_version"] != 1 or host.get("mode") != "live"
            or host.get("status") not in ("complete", "incomplete")
            or not host.get("archive") or _path(host["archive"]) != archive):
        raise ValueError("visit_archive_or_terminal_status_unverified")
    boundary_files = ("stop", "clock/status.json", "clock/summary.json")
    if any((visit / name).stat().st_size > MAX_METADATA_BYTES for name in boundary_files):
        raise ValueError("visit_boundary_file_too_large")
    end, bindings = _departure(visit, time.time(), host["mode"], _visit_id(host))
    if end is None:
        raise ValueError("declared_departure_unverified")
    clock, summary_sha = _read_small(visit / "clock/summary.json")
    if summary_sha != bindings[-1]["sha256"]:
        raise ValueError("terminal_clock_changed")
    start = host_start = _utc(clock["started_at"])
    arrival_binding = None
    scope = "host_start_to_departure"
    host_elapsed = clock["elapsed_s"]
    if "arrival" in host:
        if arrival_path is not None:
            raise ValueError("claimed_arrival_cannot_be_replaced")
        arrival = verify_arrival_claim(visit, host["arrival"])
        host_start = _utc(arrival["at"])
        prehost_elapsed = number(arrival["elapsed_s"])
        host_elapsed = number(host_elapsed) - prehost_elapsed
        stop, stop_sha = _read_small(visit / "stop")
        if (arrival["arrived_at"] != clock["started_at"] or not start <= host_start <= end
                or host_elapsed < 0 or number(clock["activity_s"]["unknown"]) < prehost_elapsed
                or stop_sha != bindings[0]["sha256"]
                or not math.isclose(number(stop["elapsed"]) - arrival["elapsed"], host_elapsed,
                                    rel_tol=0, abs_tol=0.000001)):
            raise ValueError("arrival_claim_clock_mismatch")
        scope = "declared_arrival_to_departure"
        arrival_binding = arrival
    elif arrival_path is not None:
        arrival, digest = _read_small(arrival_path)
        if (not isinstance(arrival, dict) or type(arrival.get("schema_version")) is not int
                or arrival["schema_version"] != 1
                or arrival.get("kind") != "reviewed_arrival_declaration"
                or arrival.get("visit_id") != clock["visit_id"]
                or not isinstance(arrival.get("basis"), str) or not arrival["basis"].strip()):
            raise ValueError("arrival_declaration_invalid_or_unbound")
        stamp = datetime.fromisoformat(arrival["at"])
        if stamp.utcoffset() is None or stamp.timestamp() > host_start:
            raise ValueError("arrival_time_invalid")
        start = stamp.timestamp()
        arrival_binding = dict(path=str(arrival_path), sha256=digest, reported_at=arrival["at"])
        scope = "reviewed_arrival_to_departure"
    if not 0 < end - start <= MAX_TILES * TILE_SECONDS:
        raise ValueError("visit_envelope_duration_limit")
    return dict(start_at=_stamp(start), end_at=_stamp(end), duration_s=end-start,
                scope=scope,
                host_clock_started_at=_stamp(host_start), prehost_declared_s=host_start-start,
                host_elapsed_s=host_elapsed, host_wall_duration_s=end-host_start,
                arrival=arrival_binding,
                visit_id=clock["visit_id"], host_status_sha256=host_sha, departure_sources=bindings)


def audit_visit_coverage(visit, archive, *, source_id, output_dir, arrival=None):
    """Publish an explicit visit audit without changing capture or clock inputs."""
    visit, archive = _path(visit), _path(archive)
    output = _path(output_dir)
    arrival_path = _path(arrival) if arrival is not None else None
    if output.parent != visit / "coverage" or output.is_relative_to(archive) or not visit.is_dir():
        raise ValueError("visit_audit_output_must_be_new_child_of_visit_coverage")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    try:
        envelope = _envelope(visit, archive, arrival_path)
        if _source(archive) != source_id:
            raise ValueError("archive_identity_differs_from_selected_source")
        rows, manifest_sha = _manifest_or_empty(archive)
        windows = _windows(_utc(envelope["start_at"]), _utc(envelope["end_at"]), rows)
        plan = dict(schema_version=1, kind="visit_archive_coverage_plan", archive=str(archive),
                    source_id=source_id, initial_manifest_sha256=manifest_sha,
                    envelope=envelope, max_gap_s=.1, tiles=windows)
        _publish(output / "plan.json", plan)

        tiles, credits, anchors, contributing = [], [], [], {}
        for window in windows:
            names, index = window["segment_names"], window["index"]
            summary = dict(index=index, duration_s=window["duration_s"], integrity_result="NOT RUN",
                           metric_known=False, report=None, reason=None)
            if not names:
                summary["reason"] = "no_closed_candidates"
            elif len(names) > MAX_SEGMENTS or time.perf_counter() - started >= MAX_WORK_SECONDS:
                summary["reason"] = "visit_audit_work_limit"
            else:
                report = analyze_archive_coverage(archive, start_at=window["start_at"],
                    end_at=window["end_at"], segment_names=names, max_gap_s=.1)
                path = write_archive_coverage_report(
                    report, output / f"tile-{index:02d}")["report_path"]
                summary.update(integrity_result=report["integrity_result"],
                               metric_known=report["metric_known"],
                               reason=report["metric_unknown_reason"], report=path)
                scope_ok = report["source_id"] == source_id and report["scope_complete"]
                if not scope_ok:
                    summary.update(metric_known=False, reason="tile_scope_or_source_unverified")
                if summary["metric_known"]:
                    provenance = report["provenance"]
                    anchor = provenance["alignment_anchor_utc_minus_offset_s"]
                    deviation = provenance["max_alignment_deviation_s"]
                    anchors.extend((anchor-deviation, anchor+deviation))
                    credits.append(report["verified_sample_span_s"])
                    contributing.update((row["path"], row["sha256"]) for row in report["segments"]
                                        if row["result"] == "PASS")
            tiles.append(summary)

        compatible = bool(anchors) and max(anchors)-min(anchors) <= ALIGNMENT_TOLERANCE_S
        failed = any(tile["integrity_result"] == "FAIL" for tile in tiles)
        recheck_failure = None
        if compatible and not failed:
            budget = _Budget()
            try:
                for name, digest in contributing.items():
                    if time.perf_counter() - started >= MAX_WORK_SECONDS:
                        raise _Limit("visit_audit_work_limit")
                    if hashlib.sha256(_compressed(archive, name, budget)).hexdigest() != digest:
                        recheck_failure = "contributing_segment_changed"
                        break
            except _Limit:
                recheck_failure = "final_recheck_work_limit"
            except (OSError, ValueError):
                recheck_failure = "contributing_segment_unavailable"
        final_rows, _ = _manifest_or_empty(archive)
        stable = (recheck_failure is None and _source(archive) == source_id
                  and final_rows[:len(rows)] == rows
                  and _envelope(visit, archive, arrival_path) == envelope)
        candidate_span = math.fsum(credits)
        known = stable and compatible and not failed and candidate_span > 0
        span = candidate_span if known else None
        duration = envelope["duration_s"]
        if known and not 0 <= span <= duration:
            raise ValueError("verified_span_outside_declared_envelope")
        if not stable:
            reason = recheck_failure or "visit_or_archive_binding_changed"
        elif failed:
            reason = "selected_segment_integrity_failed"
        elif anchors and not compatible:
            reason = "tile_clock_alignment_disagrees"
        else:
            reason = None if known else "no_verified_span_inside_visit"
        result = dict(schema_version=1, kind="visit_archive_coverage",
                      result=("FAIL" if failed or not stable
                      else "PASS" if known else "NOT RUN"), envelope=envelope, source_id=source_id,
                      metric_known=known, metric_unknown_reason=reason,
                      verified_span_lower_bound_s=span,
                      lower_bound_percent=100*span/duration if known else None,
                      unverified_or_unknown_s=duration-span if known else None,
                      original_bindings_stable=stable, compatible_tile_anchors=compatible,
                      tiles=tiles, live_validation="NOT RUN")
        _publish(output / "report.json", result)
        return result
    except (OSError, ValueError, TypeError, AttributeError, KeyError,
            RecursionError, _Limit) as error:
        _publish(output / "failure.json", dict(schema_version=1, result="FAIL",
                 error_type=type(error).__name__, reason=str(error), live_validation="NOT RUN"))
        raise
