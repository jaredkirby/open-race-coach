"""Freeze a lap window before driving and grade only those attempts afterward."""

import time
from datetime import datetime
from uuid import uuid4

from simcoach.live.dashboard_feedback import bound, read_feedback_session
from simcoach.live.lap_timing import BASIS, summarize_timing
from simcoach.live.supervisor import integer, number, strict_json
from simcoach.live.visit_review import _io, _path, _publish


def _new_output(visit):
    visit = _path(visit)
    root = visit / "output/consistency"
    if not _io(visit).is_dir() or root.resolve() != root:
        raise ValueError("invalid_consistency_output_root")
    directory = root / uuid4().hex
    _io(directory).mkdir(parents=True, exist_ok=False)
    return directory


def _note(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
        raise ValueError("condition_evidence_required")
    return value.strip()


def _epoch(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("session_timestamp_requires_zone")
    return stamp.timestamp()


def _session(visit, session, first_lap, count):
    metadata, laps, sources = read_feedback_session(visit, session)
    if len(laps) > 128:
        raise ValueError("consistency_lap_limit")
    rows = [{key: row[key] for key in ("lap_id", "lap", "lap_time_s", "lap_time_source",
                                       "valid", "invalid_reason")} for row in laps]
    by_lap = {row["lap"]: row for row in rows}
    window = [dict(lap=lap, record=by_lap.get(lap)) for lap in range(first_lap, first_lap + count)]
    selected = [item["record"] for item in window if item["record"] is not None]
    timing = summarize_timing(selected)
    sufficient = (timing["statistics"]["valid_laps"] == count
                  and timing["timing_basis"] in BASIS.values())
    return dict(path=str(_path(session)), sources=sources,
                recorded_session_id=metadata["recorded_session_id"],
                context={key: metadata[key] for key in (
                    "sim", "car", "track", "session_type", "validity_method")},
                adapter_version=metadata["adapter_version"],
                started_at=_epoch(metadata["started_at"]),
                ended_at=_epoch(metadata["ended_at"]), laps=rows, window=window,
                window_sufficient=sufficient, timing_basis=timing["timing_basis"],
                statistics=timing["statistics"] if sufficient else None)


def prepare_plan(visit, session, *, baseline_first, followup_first, count, spread_s, conditions):
    """One explicit Chief choice. The baseline and exact future lap slots are frozen."""
    directory = _new_output(visit)
    _publish(directory / "attempt.json", dict(schema_version=1, action="prepare", at=time.time(),
                                              session=str(_path(session))))
    try:
        count = integer(count, minimum=3, maximum=20)
        baseline_first = integer(baseline_first, minimum=1)
        followup_first = integer(followup_first, minimum=1)
        target = number(spread_s, minimum=.05, maximum=10)
        conditions = _note(conditions)
        baseline = _session(visit, session, baseline_first, count)
        now = time.time()
        if baseline["ended_at"] > now or baseline["started_at"] >= baseline["ended_at"]:
            raise ValueError("invalid_baseline_time_order")
        path = directory / "plan.json"
        _publish(path, dict(schema_version=1, kind="consistency_plan", prepared_at=now,
                            baseline=baseline, baseline_first=baseline_first,
                            followup_first=followup_first,
                            count=count, spread_s=target, conditions=conditions))
        return path
    except Exception as exc:
        _publish(directory / "failure.json", dict(schema_version=1, error_type=type(exc).__name__))
        raise


def compare_plan(baseline_visit, plan_path, visit, session, *, plan_sha256,
                 conditions_match, evidence):
    """Keep all observed records. Withhold scores for missing or incomparable evidence."""
    directory = _new_output(visit)
    _publish(directory / "attempt.json", dict(schema_version=1, action="compare", at=time.time(),
                                              plan=str(_path(plan_path)),
                                              session=str(_path(session))))
    try:
        if conditions_match not in ("matched", "different", "unknown"):
            raise ValueError("invalid_comparability_verdict")
        evidence = _note(evidence)
        origin = _path(baseline_visit)
        path = _path(plan_path)
        if (path.parent.parent != origin / "output/consistency" or path.name != "plan.json"):
            raise ValueError("plan_outside_baseline_visit")
        raw, binding = bound(path, origin)
        if binding["sha256"] != plan_sha256:
            raise ValueError("consistency_plan_hash_mismatch")
        plan = strict_json(raw.decode("utf-8"))
        if (not isinstance(plan, dict) or type(plan.get("schema_version")) is not int
                or plan["schema_version"] != 1 or plan.get("kind") != "consistency_plan"):
            raise ValueError("unsupported_consistency_plan")
        count = integer(plan["count"], minimum=3, maximum=20)
        prepared = number(plan["prepared_at"])
        target = number(plan["spread_s"], minimum=.05, maximum=10)
        baseline = _session(origin, plan["baseline"]["path"],
                            integer(plan["baseline_first"], minimum=1), count)
        if baseline != plan["baseline"] or baseline["ended_at"] > prepared:
            raise ValueError("baseline_source_changed_or_out_of_order")
        followup = _session(visit, session, integer(plan["followup_first"], minimum=1), count)
        reason = None
        if (followup["recorded_session_id"] == baseline["recorded_session_id"]
                or not prepared < followup["started_at"] < followup["ended_at"] <= time.time()):
            reason = "followup_not_after_plan"
        elif baseline["context"] != followup["context"]:
            reason = "recorded_context_differs"
        elif conditions_match != "matched":
            reason = "conditions_" + conditions_match
        elif not baseline["window_sufficient"] or not followup["window_sufficient"]:
            reason = "planned_attempts_incomplete_invalid_or_mixed"
        elif baseline["timing_basis"] != followup["timing_basis"]:
            reason = "timing_basis_differs"
        delta = None
        if reason is None:
            before, after = baseline["statistics"], followup["statistics"]
            delta = dict(median_s=after["median_s"] - before["median_s"],
                         spread_s=after["spread_s"] - before["spread_s"],
                         target_met=round(after["spread_s"], 6) <= target)
        if bound(path, origin)[1] != binding:
            raise ValueError("plan_changed_during_comparison")
        result = directory / "comparison.json"
        _publish(result, dict(schema_version=1, kind="consistency_comparison", at=time.time(),
            status="compared" if reason is None else "withheld", reason=reason,
            plan=binding, baseline=baseline, followup=followup, goal_spread_s=target,
            conditions=_note(plan["conditions"]), conditions_match=conditions_match,
            condition_evidence=evidence, change=delta, delivery_verified=False))
        return result
    except Exception as exc:
        _publish(directory / "failure.json", dict(schema_version=1, error_type=type(exc).__name__))
        raise
