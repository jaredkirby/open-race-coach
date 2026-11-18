"""Explicit reviewed display cards; never turns private review notes into UI text."""
from __future__ import annotations

import hashlib
import re
import time
from uuid import uuid4

from simcoach.live.dashboard import _label
from simcoach.live.lap_timing import BASIS, summarize_timing
from simcoach.live.supervisor import integer, number, strict_json
from simcoach.live.visit_outcome import worker_directories
from simcoach.live.visit_review import _io, _path, _publish, _read_bound

MAX_CARD = 128 * 1024


def bound(path, root):
    path, root = _path(path), _path(root)
    if not path.is_relative_to(root) or _io(path).stat().st_size > MAX_CARD:
        raise ValueError("feedback_source_outside_or_too_large")
    return _read_bound(path, root)


def public_card(value):
    """Rebuild the public projection; no unknown fields, notes or private paths."""
    if (not isinstance(value, dict) or value.get("kind") != "dashboard_feedback"
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 2):
        raise ValueError("unsupported_feedback_card")
    card = value["display"]
    if not isinstance(card, dict) or not isinstance(card.get("context"), dict):
        raise ValueError("invalid_feedback_shape")
    sid = card["recorded_session_id"]
    if not isinstance(sid, str) or not re.fullmatch(r"[0-7][0-9a-hjkmnp-tv-z]{25}", sid):
        raise ValueError("invalid_feedback_session_id")
    context = {key: _label(card["context"].get(key)) for key in ("car", "track", "session_type")}
    if any(value is None for value in context.values()):
        raise ValueError("invalid_feedback_context")
    rows = card["laps"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ValueError("feedback_lap_limit")
    laps, valid, reasons = [], [], {}
    allowed = {"sim_invalidated", "bad_lap_progress", "missing_tick_range",
               "non_running_vehicle_state", "teleport_or_reset", "metadata_boundary",
               "unknown_invalidity"}
    for row in rows:
        lap = integer(row["lap"], minimum=1)
        seconds = number(row["lap_time_s"], minimum=0, maximum=86400)
        source = row["lap_time_source"]
        if not isinstance(source, str) or source not in BASIS:
            raise ValueError("invalid_feedback_timing_source")
        if type(row["valid"]) is not bool:
            raise ValueError("invalid_feedback_validity")
        reason = row["invalid_reason"]
        if (row["valid"] and (reason is not None or seconds <= 0)
                or not row["valid"] and reason not in allowed):
            raise ValueError("invalid_feedback_exclusion")
        if row["valid"]:
            valid.append(seconds)
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
        laps.append(dict(lap=lap, lap_time_s=seconds, lap_time_source=source,
                         valid=row["valid"], invalid_reason=reason))
    if len({row["lap"] for row in laps}) != len(laps):
        raise ValueError("duplicate_feedback_lap")
    timing = summarize_timing(laps)
    objective = card.get("objective")
    if objective is not None:
        if len(valid) < 3 or timing["timing_basis"] not in BASIS.values():
            raise ValueError("insufficient_feedback_objective")
        objective = dict(kind="chief_selected_consistency_target",
            laps=integer(objective["laps"], minimum=3, maximum=20),
            spread_s=number(objective["spread_s"], minimum=.05, maximum=10),
            timing_basis=timing["timing_basis"])
    return dict(recorded_session_id=sid, context=context,
        ended_at=number(card["ended_at"]), **timing,
        scope="one_recorded_session", laps=laps, exclusions=reasons,
        objective=objective, causal_coaching_instruction=None)


def read_feedback_session(visit, session):
    """Bound a validated Recorded Session to the selected visit and exact bytes."""
    from simcoach.validation import read_validated_session_artifacts

    visit, session = _path(visit), _path(session)
    if not any(session.is_relative_to(root / "sessions") for root in worker_directories(visit)):
        raise ValueError("feedback_session_outside_worker")
    sources = [bound(session / name, visit)[1] for name in ("session.yaml", "laps.jsonl")]
    ticks = session / "ticks.parquet"
    before = _io(ticks).stat()
    if ticks.resolve() != ticks.absolute() or before.st_size > 64 * 1024 * 1024:
        raise ValueError("feedback_tick_limit_or_redirect")
    with _io(ticks).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata, _, laps = read_validated_session_artifacts(_io(session))
    after = _io(ticks).stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("feedback_source_changed")
    for binding in sources:
        if bound(binding["path"], visit)[1] != binding:
            raise ValueError("feedback_source_changed")
    sources.append(dict(path=str(ticks), sha256=digest, bytes=before.st_size))
    return metadata, laps, sources


def prepare_feedback(visit, session, *, target_laps=3, target_spread_s=.5):
    """Validate one finalized segment and prepare a card; explicit review is separate."""
    from datetime import datetime

    visit, session = _path(visit), _path(session)
    if not any(session.is_relative_to(root / "sessions") for root in worker_directories(visit)):
        raise ValueError("feedback_session_outside_worker")
    directory = visit / "output/dashboard" / uuid4().hex
    if directory.resolve() != directory.absolute():
        raise ValueError("feedback_output_redirected")
    _io(directory).mkdir(parents=True, exist_ok=False)
    _publish(directory / "attempt.json", dict(schema_version=1, session=str(session),
                                              at=time.time()))
    try:
        metadata, laps, sources = read_feedback_session(visit, session)
        ended = datetime.fromisoformat(metadata["ended_at"])
        if ended.tzinfo is None:
            raise ValueError("feedback_time_requires_zone")
        timing = summarize_timing(laps)
        value = dict(schema_version=2, kind="dashboard_feedback", prepared_at=time.time(),
            sources=sources, review="required", display=dict(
                recorded_session_id=metadata["recorded_session_id"],
                context=dict(car=metadata["car_raw"], track=metadata["track_raw"],
                             session_type=metadata["session_type"]), ended_at=ended.timestamp(),
                laps=[{key: row[key] for key in (
                    "lap", "lap_time_s", "lap_time_source", "valid", "invalid_reason")}
                      for row in laps],
                objective=dict(laps=target_laps, spread_s=target_spread_s)
                    if sum(row["valid"] for row in laps) >= 3
                    and timing["timing_basis"] in BASIS.values() else None))
        value["display"] = public_card(value)
        _publish(directory / "card.json", value)
        return directory / "card.json"
    except Exception as exc:
        _publish(directory / "failure.json", dict(schema_version=1, error_type=type(exc).__name__,
                                                 review_accepted=False))
        raise


def reviewed_feedback(visit, selection):
    """Read an explicitly selected immutable review; never select newest output."""
    if visit is None or selection is None:
        return dict(state="unavailable", reason="no_feedback_selected", card=None)
    try:
        visit = _path(visit)
        raw, _ = bound(selection, visit)
        chosen = strict_json(raw.decode("utf8"))
        if (not isinstance(chosen, dict) or type(chosen.get("schema_version")) is not int
                or chosen["schema_version"] != 1):
            raise ValueError("unsupported_feedback_selection")
        review_path = _path(chosen["review"])
        if (not review_path.is_relative_to(visit / "reviews")
                or review_path.name != "review.json"):
            raise ValueError("feedback_review_outside_visit")
        raw, _ = bound(review_path, visit)
        if hashlib.sha256(raw).hexdigest() != chosen.get("review_sha256"):
            raise ValueError("selected_review_changed")
        review = strict_json(raw.decode("utf8"))
        if (not isinstance(review, dict) or type(review.get("schema_version")) is not int
                or review["schema_version"] != 1
                or review.get("reviewer") != "chief"
                or review.get("visit_mode") not in {"live", "replay"}
                or review.get("review_id") != review_path.parent.name):
            raise ValueError("feedback_review_not_chief")
        if review.get("verdict") not in {"useful", "insufficient"}:
            return dict(state="withheld", reason="review_does_not_support_display", card=None)
        source = review["output"]
        if not _path(source["path"]).is_relative_to(visit / "output/dashboard"):
            raise ValueError("feedback_card_outside_display_outputs")
        raw, binding = bound(source["path"], visit)
        if binding != source:
            raise ValueError("feedback_card_changed_after_review")
        value = public_card(strict_json(raw.decode("utf8")))
        if review["verdict"] == "insufficient":
            value["objective"] = None
        return dict(state="reviewed", reason=None, review_id=review["review_id"],
            reviewed_at=number(review["reviewed_at"]), verdict=review["verdict"], card=value,
            evidence_mode=review["visit_mode"],
            delivery_verified=False, driver_endorsed=False)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return dict(state="unavailable", reason="feedback_evidence_unverified", card=None)


def select_feedback(visit, review):
    from simcoach.cli.telemetry_watch import write_status
    visit = _path(visit)
    root = visit / "output/dashboard-selections"
    if root.resolve() != root.absolute():
        raise ValueError("feedback_selection_redirected")
    _io(root).mkdir(parents=True, exist_ok=True)
    candidate = root / f"{uuid4().hex}.json"
    raw, _ = bound(review, visit)
    value = dict(schema_version=1, review=str(_path(review)),
                 review_sha256=hashlib.sha256(raw).hexdigest(), at=time.time())
    _publish(candidate, value)
    checked = reviewed_feedback(visit, candidate)
    if checked["state"] == "unavailable":
        raise ValueError("feedback_selection_unverified")
    destination = visit / "output/dashboard-selection.json"
    if destination.resolve() != destination.absolute():
        raise ValueError("feedback_selection_redirected")
    write_status(_io(destination), value, temporary=_io(root / f"{uuid4().hex}.partial"))
    return destination
