"""Read-only visit learning summary. Never recapture, analyze, play or repair inputs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from uuid import uuid4

import yaml

from simcoach.live.lap_timing import BASIS, summarize_timing
from simcoach.live.radio import Transmission
from simcoach.live.supervisor import strict_json
from simcoach.live.visit_clock import ACTIVITIES, REASONS

MAX_LAP_BYTES = 128 * 1024
MAX_LAP_RECORDS = 512
MAX_SUPERVISION_BYTES = 64 * 1024 * 1024
MAX_FUEL_NOTICES = 512
TIMING_LABELS = {"derived_from_ticks": "Captured time", "sim": "Simulator time"}

class _UniqueYaml(yaml.SafeLoader):
    pass


def _yaml_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError("duplicate_yaml_key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueYaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _normal_path(value):
    value = str(value)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value).resolve()


def _io_path(path):
    value = str(_normal_path(path))
    if os.name == "nt":
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def worker_directories(run):
    """Inventory owned worker roots, including receipt-only launch attempts.

    Directory names are locators, never proof of launch, ownership or success.
    Resolved siblings outside the exact visit parent are not followed.
    """
    run = _normal_path(run)
    names = {"worker"}
    for child in _io_path(run).iterdir():
        if re.fullmatch(r"worker-[0-9]{4}", child.name):
            names.add(child.name)
    receipts = run / "capture-attempts"
    if _normal_path(receipts).parent == run and _io_path(receipts).is_dir():
        for child in _io_path(receipts).iterdir():
            if re.fullmatch(r"[0-9]{4}", child.name) and int(child.name) >= 1:
                number = int(child.name)
                names.add("worker" if number == 1 else f"worker-{number:04d}")
    return [run / name for name in sorted(names)
            if _normal_path(run / name).parent == run
            and _normal_path(run / name).name == name]


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _epoch(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class _Reader:
    def __init__(self, root):
        self.root = _normal_path(root)
        self.sources = {}

    def read(self, path, *, format="json"):
        path = _normal_path(path)
        entry = dict(
            path=str(path), sha256=None, bytes=None, state="unavailable", existed_initially=None
        )
        self.sources[str(path)] = entry
        if not path.is_relative_to(self.root):
            entry["state"] = "outside_owned_visit_not_read"
            return None
        entry["existed_initially"] = _io_path(path).exists()
        if format == "presence":
            entry["state"] = "present" if entry["existed_initially"] else "absent"
            return entry["existed_initially"]
        try:
            limit = {"laps": MAX_LAP_BYTES, "sqlite": MAX_SUPERVISION_BYTES}.get(format)
            if limit is not None:
                with _io_path(path).open("rb") as handle:
                    raw = handle.read(limit + 1)
                if len(raw) > limit:
                    entry["state"] = "over_limit"
                    return None
            else:
                raw = _io_path(path).read_bytes()
            entry.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), state="read")
            if format == "bytes":
                return dict(sha256=entry["sha256"], bytes=len(raw))
            if format == "sqlite":
                return raw
            text = raw.decode("utf-8-sig")
            if format == "text":
                return text
            if format == "laps":
                lines = text.splitlines()
                if not 1 <= len(lines) <= MAX_LAP_RECORDS:
                    raise ValueError("outcome_lap_record_limit")
                return [strict_json(line) for line in lines]
            value = strict_json(text) if format == "json" else yaml.load(text, Loader=_UniqueYaml)
            if not isinstance(value, dict):
                raise ValueError("not_object")
            return value
        except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError) as exc:
            entry.update(
                state="malformed" if entry["sha256"] else "unavailable",
                error_type=type(exc).__name__,
            )
            return None

    def verify(self):
        changed = []
        for path, entry in self.sources.items():
            if entry["state"] == "outside_owned_visit_not_read":
                continue
            if entry["sha256"] is None:
                if not entry["existed_initially"] and _io_path(path).exists():
                    entry["state"] = "appeared_during_summary"
                    changed.append(path)
                continue
            try:
                with _io_path(path).open("rb") as handle:
                    raw = handle.read(entry["bytes"] + 1)
                unchanged = (len(raw) == entry["bytes"]
                             and hashlib.sha256(raw).hexdigest() == entry["sha256"])
            except OSError:
                unchanged = False
            if not unchanged:
                entry["state"] = "changed_during_summary"
                changed.append(path)
        return changed


def _clock(reader, run, parent):
    status = reader.read(run / "clock/status.json")
    summary = reader.read(run / "clock/summary.json")
    unknown = dict(
        known=False,
        reason="terminal_clock_missing_or_invalid",
        elapsed_s=None,
        activity_s=None,
        not_driving_reason_s=None,
        reason_policy=None,
        presence="unknown",
        declared_departure_at=None,
        prehost=None,
    )
    if not isinstance(summary, dict) or status != summary:
        return unknown
    if (
        type(summary.get("schema_version")) is not int
        or summary["schema_version"] != 1
        or summary.get("status") not in ("complete", "interrupted")
        or summary.get("publication_complete") is not True
        or summary.get("publication_error") is not None
        or summary.get("presence")
        != ("declared_absent" if summary["status"] == "complete" else "unknown")
        or not isinstance(summary.get("visit_id"), str)
        or not summary["visit_id"]
        or _epoch(summary.get("started_at")) is None
        or _epoch(summary.get("updated_at")) is None
    ):
        return unknown
    totals, reasons = summary.get("activity_s"), summary.get("not_driving_reason_s")
    if (
        not isinstance(totals, dict)
        or set(totals) != set(ACTIVITIES)
        or not isinstance(reasons, dict)
        or set(reasons) != set(REASONS)
        or not all(_number(v) for v in [*totals.values(), *reasons.values()])
        or not _number(summary.get("elapsed_s"))
        or not math.isclose(sum(totals.values()), summary["elapsed_s"], abs_tol=1e-6)
        or not math.isclose(sum(reasons.values()), totals["not_driving"], abs_tol=1e-6)
    ):
        return dict(unknown, reason="clock_partition_invalid")
    departure = (
        summary.get("updated_at")
        if (summary["status"] == "complete" and summary.get("presence") == "declared_absent")
        else None
    )
    if departure is not None and _epoch(departure) is None:
        return unknown
    prehost = None
    if parent and parent.get("mode") == "live" and parent.get("clock") == summary:
        arrival = parent.get("arrival")
        if isinstance(arrival, dict):
            elapsed = arrival.get("elapsed_s")
            host = _epoch(arrival.get("at"))
            if (arrival.get("resolution") == "claimed" and arrival.get("run") == run.name
                    and arrival.get("arrived_at") == summary["started_at"]
                    and host is not None
                    and _epoch(summary["started_at"]) <= host <= _epoch(summary["updated_at"])
                    and _number(elapsed) and elapsed == arrival.get("unknown_s")
                    and elapsed <= totals["unknown"]):
                prehost = dict(scope="reported_host_binding", arrived_at=arrival["arrived_at"],
                               host_started_at=arrival["at"], unknown_s=elapsed)
                if "declared_intent" in arrival:
                    # Import lazily: arrival reads use helpers in visit_review.
                    from simcoach.live.visit_arrival import verify_arrival_claim

                    try:
                        verified = verify_arrival_claim(run, arrival)
                        intent = verified["declared_intent"]
                        prehost["declared_intent"] = (
                            dict(intent, resolution_sha256=verified["resolution_sha256"])
                            if intent.get("state") == "verified" else dict(state="unavailable"))
                    except (OSError, ValueError, RecursionError):
                        prehost["declared_intent"] = dict(state="unavailable")
    return dict(
        known=True,
        reason=None,
        status=summary["status"],
        elapsed_s=summary["elapsed_s"],
        activity_s=totals,
        not_driving_reason_s=reasons,
        reason_policy=summary.get("reason_policy"),
        presence=summary.get("presence"),
        declared_departure_at=departure,
        prehost=prehost,
    )


def _owned_path(value, root):
    if not isinstance(value, str) or not value:
        return None
    path = _normal_path(value)
    return path if path.is_relative_to(root.resolve()) else None


def _lap_review(reader, path, session, attempt):
    """Use finalized lap records and the worker's reported validation receipt."""
    summary = attempt["validation_summary"]
    if (attempt["inventory"] != "closure" or attempt["reported_validation"] != "PASS"
            or attempt["capture_complete"] is not True or not isinstance(summary, dict)):
        return None, "validated_closure_unavailable"
    identity = attempt["recorded_session_id"]
    context = {key: session.get(key) for key in ("car", "track", "session_type")}
    if (type(session.get("lap_schema_version")) is not int or session["lap_schema_version"] != 1
            or not isinstance(identity, str) or summary.get("recorded_session_id") != identity
            or not all(isinstance(value, str) and 0 < len(value.strip()) <= 256
                       and value.isprintable() for value in context.values())):
        return None, "lap_schema_identity_or_context_unverified"
    source = path / "laps.jsonl"
    metadata_source = path / "session.yaml"
    if _normal_path(source) != source or _normal_path(metadata_source) != metadata_source:
        return None, "lap_source_redirected"
    records = reader.read(source, format="laps")
    if records is None:
        return None, "lap_records_missing_malformed_or_over_limit"
    laps = []
    seen = set()
    exclusions = Counter()
    keys = ("lap_id", "lap", "lap_time_s", "lap_time_source", "valid", "invalid_reason")
    for row in records:
        if not isinstance(row, dict) or any(key not in row for key in keys):
            return None, "invalid_lap_record"
        lap_id, number, seconds, timing, valid, reason = (row[key] for key in keys)
        if (type(number) is not int or number < 1 or number in seen
                or lap_id != f"{identity}:lap:{number}"
                or not isinstance(timing, str) or timing not in BASIS
                or not _number(seconds) or seconds > 86400 or type(valid) is not bool
                or (valid and (seconds <= 0 or reason is not None))
                or (not valid and (not isinstance(reason, str)
                                   or not re.fullmatch(r"[a-z_]{1,40}", reason)))):
            return None, "invalid_lap_record"
        if not valid:
            exclusions[reason] += 1
        seen.add(number)
        laps.append({key: row[key] for key in keys})
    if (type(summary.get("lap_count")) is not int or summary["lap_count"] != len(laps)
            or type(summary.get("valid_lap_count")) is not int
            or summary["valid_lap_count"] != len(laps) - sum(exclusions.values())):
        return None, "lap_counts_disagree_with_validation"
    return dict(scope="reported_validated_recorded_session", context=context,
                laps=laps, exclusions=dict(exclusions), **summarize_timing(laps)), None


def _recordings(reader, worker_root, closure):
    root = worker_root / "sessions"
    worker = reader.read(worker_root / "status.json")
    if worker and (type(worker.get("schema_version")) is not int or worker["schema_version"] != 1):
        worker = None
    if _normal_path(root).parent != _normal_path(worker_root):
        return [], worker
    progress = reader.read(worker_root / "closure-progress.json")
    if progress and (type(progress.get("schema_version")) is not int
                     or progress["schema_version"] != 1):
        progress = None
    attempts = []
    known = {}
    candidates = []
    for source, inventory in ((closure, "closure"), (progress, "closure_progress")):
        rows = source.get("recordings", []) if isinstance(source, dict) else []
        if isinstance(rows, list):
            candidates.extend((row, inventory) for row in rows if isinstance(row, dict))
    extra = worker.get("sessions", []) if isinstance(worker, dict) else []
    if isinstance(extra, list):
        candidates.extend((dict(path=path), "worker_inventory") for path in extra)
    if _io_path(root).is_dir():
        candidates.extend(
            (dict(path=str(path)), "owned_directory_inventory")
            for path in sorted(_io_path(root).iterdir())
        )
    for row, inventory in candidates:
        locator = row.get("path")
        path = _owned_path(locator, root)
        key = str(path) if path is not None else str(locator)
        report = dict(source=inventory, reported=dict(row))
        if key in known:
            attempt = known[key]
            attempt["inventory_reports"].append(report)
            if inventory == "closure":
                attempt.update(lap_review=None, lap_review_reason="duplicate_closure_record")
            continue
        attempt = dict(
            path=locator,
            inventory=inventory,
            inventory_reports=[report],
            reported_validation=row.get("validation", "UNKNOWN"),
            reported_analysis=row.get("analysis", "UNKNOWN"),
            reported_error_type=row.get("error_type"),
            validation_summary=row.get("summary"),
            recorded_session_id=None,
            capture_complete=None,
            failure_reason=None,
            lap_review=None,
            lap_review_reason="session_evidence_unavailable",
            analyses=[],
            issues=[],
        )
        known[key] = attempt
        attempts.append(attempt)
        if path is None:
            attempt["issues"].append("locator_outside_owned_sessions_not_read")
            continue
        if not _io_path(path).is_dir():
            attempt["issues"].append("unregistered_file_or_missing_session")
            continue
        session = reader.read(path / "session.yaml", format="yaml")
        if (
            session
            and type(session.get("session_schema_version")) is int
            and session["session_schema_version"] == 1
        ):
            identity = session.get("recorded_session_id")
            failure = session.get("failure_reason")
            attempt.update(
                recorded_session_id=identity if isinstance(identity, str) else None,
                capture_complete=session.get("complete")
                if type(session.get("complete")) is bool
                else None,
                failure_reason=failure if isinstance(failure, str) else None,
            )
        else:
            attempt["issues"].append("session_metadata_unknown_or_unsupported")
        attempt["lap_review"], attempt["lap_review_reason"] = _lap_review(
            reader, path, session, attempt)
        analysis_root = path / "analysis"
        analysis_paths = (
            sorted(_io_path(analysis_root).iterdir()) if _io_path(analysis_root).is_dir() else []
        )
        for analysis_path in analysis_paths:
            analysis_path = _normal_path(analysis_path)
            if not _io_path(analysis_path).is_dir() or not analysis_path.is_relative_to(path):
                attempt["issues"].append("unregistered_analysis_artifact")
                continue
            analysis = reader.read(analysis_path / "analysis.yaml", format="yaml")
            result = dict(
                path=str(analysis_path),
                run_status=None,
                analysis_status=None,
                run_error=None,
                identity_matches=None,
                report_path=None,
                evidence="unknown",
            )
            if (
                analysis
                and type(analysis.get("analysis_schema_version")) is int
                and analysis["analysis_schema_version"] == 1
            ):
                run_status, status = analysis.get("run_status"), analysis.get("analysis_status")
                statuses = (
                    "reportable",
                    "consistent",
                    "inconsistent",
                    "insufficient_data",
                    "no_single_dominant_issue",
                )
                result.update(
                    run_status=run_status if run_status in ("complete", "failed") else None,
                    analysis_status=status if status in statuses else None,
                    run_error=analysis.get("run_error")
                    if isinstance(analysis.get("run_error"), str)
                    else None,
                    evidence="reported_metadata",
                    identity_matches=analysis.get("recorded_session_id")
                    == attempt["recorded_session_id"]
                    if attempt["recorded_session_id"]
                    else None,
                )
            report_path = analysis_path / "coach_report.md"
            if (_io_path(report_path).exists()
                    and reader.read(report_path, format="text") is not None):
                result["report_path"] = str(report_path)
            if result["identity_matches"] is False:
                result["evidence"] = "identity_mismatch"
            elif result["run_status"] == "complete" and result["report_path"] is None:
                result["evidence"] = "complete_report_missing"
            attempt["analyses"].append(result)
    return attempts, worker


def _radio_interval(start, end):
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    epoch = start.get("timing_epoch")
    if not isinstance(epoch, str) or not epoch or epoch != end.get("timing_epoch"):
        return None
    if not all(_number(point.get(key)) for point in (start, end) for key in ("at", "monotonic_s")):
        return None
    wall = end["at"] - start["at"]
    elapsed = end["monotonic_s"] - start["monotonic_s"]
    return elapsed if wall >= 0 and elapsed >= 0 and abs(elapsed - wall) <= .5 else None


def _radio_timing(events):
    by_state = {row["state"]: row for row in events}
    generated = by_state["generated"]
    spec = generated["spec"]
    source, decision = generated.get("source_at"), spec["created_at"]
    intent = by_state.get("playing")
    terminal = next((event for event in events if "transport_start" in event
                     and event["state"] in {"played", "failed", "interrupted", "expired"}), None)
    call = terminal.get("transport_start") if terminal else None
    requested, confirmed = ((call.get("requested"), call.get("confirmed"))
                            if isinstance(call, dict) else (None, None))
    queue = _radio_interval(by_state.get("queued"), intent)
    intent_to_call = _radio_interval(intent, requested)
    call_s = _radio_interval(requested, confirmed)
    confirmed_to_terminal = _radio_interval(confirmed, terminal)
    coherent_start = all(
        value is not None for value in (intent_to_call, call_s, confirmed_to_terminal))
    at = confirmed["at"] if coherent_start else None
    source_known = _number(source) and source <= decision
    return dict(
        source_observation_to_decision_utc_s=decision-source if source_known else None,
        dispatcher_queue_s=queue, output_intent_to_transport_call_s=intent_to_call,
        transport_start_call_s=call_s if coherent_start else None,
        confirmed_to_terminal_observed_s=confirmed_to_terminal if coherent_start else None,
        source_observation_to_transport_confirmed_utc_s=at-source if source_known and at is not None
        and at >= decision else None,
        transport_confirmed_before_deadline=at < spec["expires_at"] if at is not None else None,
        audible_latency_s=None,
    )


def _radio(reader, run):
    content = reader.read(run / "radio.jsonl", format="text")
    output = dict(
        known=False,
        reason="radio_journal_unavailable",
        transmissions=[],
        final_state_counts=None,
        observed_transition_counts=None,
        transport_completed_count=None,
        explicit_acknowledgment_count=None,
        driver_heard_count=None,
        driver_not_heard_count=None,
    )
    if content is None:
        return output, {}
    histories, states, transitions, dedup_keys = {}, {}, Counter(), set()
    allowed = {
        None: {"generated"},
        "generated": {"queued", "suppressed", "interrupted"},
        "queued": {"playing", "expired", "suppressed", "interrupted", "failed"},
        "playing": {"played", "failed", "interrupted", "expired"},
        "interrupted": {"failed"},
        "failed": {"interrupted"},
        "played": {"acknowledged"},
    }
    valid, previous_at = True, None
    lines = content.splitlines()
    for line_number, line in enumerate(lines, 1):
        try:
            if line_number == len(lines) and not content.endswith("\n"):
                raise ValueError("partial_radio_line")
            event = strict_json(line)
            if (
                not isinstance(event, dict)
                or type(event.get("schema_version")) is not int
                or event["schema_version"] != 1
                or not _number(event.get("at"))
                or not isinstance(event.get("transmission_id"), str)
                or not event["transmission_id"]
                or not isinstance(event.get("reason"), str)
                or not event["reason"]
            ):
                raise ValueError("invalid_radio_event")
            if previous_at is not None and event["at"] < previous_at:
                raise ValueError("radio_clock_regressed")
            identity, state = event["transmission_id"], event.get("state")
            if state not in allowed.get(states.get(identity), set()):
                raise ValueError("invalid_radio_transition")
            if state == "generated":
                spec = dict(event["spec"])
                digest = spec.pop("wav_sha256")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or "optional" not in spec
                    or spec.get("transmission_id") != identity
                    or not isinstance(spec.get("wav_path"), str)
                    or not Path(spec["wav_path"]).is_absolute()
                ):
                    raise ValueError("invalid_generated_specification")
                Transmission(**spec)._validate_fields()  # Metadata only; never spec()/audio.
                if spec["dedup_key"] in dedup_keys:
                    raise ValueError("duplicate_radio_event")
                dedup_keys.add(spec["dedup_key"])
            if state == "acknowledged" and event["reason"] != "explicit_driver_confirmation":
                raise ValueError("acknowledgment_not_explicit")
            histories.setdefault(identity, []).append(event)
            states[identity] = state
            transitions[state] += 1
            previous_at = event["at"]
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            valid = False
            output.update(reason="radio_journal_invalid_prefix_only", invalid_line=line_number)
            break
    output["transmissions"] = [
        dict(transmission_id=key, final_observed_state=states[key],
             history=[{key: row[key] for key in ("state", "at", "reason")} for row in rows],
             software_timing=_radio_timing(rows))
        for key, rows in histories.items()
    ]
    output["observed_transition_counts"] = dict(transitions)
    if valid:
        output.update(
            known=True,
            reason=None,
            final_state_counts=dict(Counter(states.values())),
            transport_completed_count=sum(
                any(e["state"] == "played" for e in rows) for rows in histories.values()
            ),
            explicit_acknowledgment_count=sum(v == "acknowledged" for v in states.values()),
        )
    return output, histories


def _fuel_notices(raw):
    """Query the verified byte snapshot in memory, without opening the source database."""
    if not raw or not raw.startswith(b"SQLite format 3\0") or raw[18:20] != b"\x01\x01":
        return None
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.deserialize(raw)
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            if db.execute("SELECT schema_version FROM metadata LIMIT 2").fetchall() != [(1,)]:
                return None
            rows = db.execute("""
                SELECT payload FROM events
                WHERE json_type(payload, '$.schema_version') IS NOT 'integer'
                    OR json_extract(payload, '$.schema_version') != 1
                    OR json_type(payload, '$.event') IS NOT 'text'
                    OR json_extract(payload, '$.event') = 'notice'
                ORDER BY sequence LIMIT ?
            """, (MAX_FUEL_NOTICES + 1,)).fetchall()
        if len(rows) > MAX_FUEL_NOTICES:
            return None
        notices, arms, ids = [], set(), set()
        for (payload,) in rows:
            row = strict_json(payload)
            if (type(row.get("schema_version")) is not int or row["schema_version"] != 1
                    or row.get("event") != "notice" or row.get("kind") != "fuel_return"
                    or row.get("text") != "Pit this lap."):
                return None
            identity, arm, evidence = (row.get(key) for key in ("id", "arm_id", "evidence_id"))
            if not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value)
                       for value in (identity, arm)):
                return None
            if (not isinstance(evidence, str) or not re.fullmatch(r"[a-f0-9]{64}", evidence)
                    or identity in ids or arm in arms):
                return None
            start, end, source = (row.get(key) for key in ("created_at", "expires_at", "source_at"))
            if (not all(_number(point) for point in (row.get("at"), start, end))
                    or row["at"] != start or start >= end
                    or source is not None and (not _number(source) or source > start)):
                return None
            ids.add(identity)
            arms.add(arm)
            notices.append(row)
        return notices
    except (AttributeError, sqlite3.Error, ValueError, TypeError, RecursionError):
        return None


def _fuel_dispatch_result(notice, rows, audio_enabled, radio_known):
    if not audio_enabled:
        return "audio_disabled", None
    if not radio_known:
        return "dispatch_evidence_unavailable", None
    if not rows:
        return "no_dispatch_record", None
    generated, final = rows[0], rows[-1]
    spec = generated["spec"]
    if (spec["dedup_key"] != f"fuel_return:{notice['arm_id']}"
            or any(spec[key] != notice[key] for key in ("evidence_id", "created_at", "expires_at"))
            or generated.get("source_at") != notice.get("source_at")
            or spec["priority"] != 100 or spec["optional"] is not False):
        return "binding_mismatch", None
    timing = _radio_timing(rows)["transport_confirmed_before_deadline"]
    state = final["state"]
    if timing is True:
        return "confirmed_before_deadline", state
    if timing is False:
        return "confirmation_at_or_after_deadline", state
    if state == "suppressed" and final["reason"] in {"muted", "submitted_while_muted"}:
        return "muted", state
    if state in {"expired", "suppressed", "interrupted", "failed"}:
        return state, state
    return "start_unverified", state


def _fuel_return_audit(notices, histories, *, audio_enabled, radio_known, reason):
    if notices is None or reason:
        return dict(reason=reason or "supervision_journal_unavailable", decision_count=None,
                    outcome_counts=None, unmatched_transmission_count=None, decisions=[])
    decisions, matched = [], set()
    for notice in notices:
        identity = f"fuel:{notice['arm_id']}:{notice['id']}"
        result, state = _fuel_dispatch_result(
            notice, histories.get(identity), audio_enabled, radio_known)
        if state is not None:
            matched.add(identity)
        decisions.append(dict(decision_id=notice["id"], arm_id=notice["arm_id"],
                              created_at=notice["created_at"], expires_at=notice["expires_at"],
                              transmission_id=identity, outcome=result, transmission_state=state))
    unmatched = sum(identity.startswith("fuel:") and identity not in matched
                    for identity in histories) if radio_known and audio_enabled else None
    return dict(reason=None, decision_count=len(decisions),
                outcome_counts=dict(Counter(row["outcome"] for row in decisions)),
                unmatched_transmission_count=unmatched, decisions=decisions)


def _supplemental(reader, run, name, filenames):
    """Inventory later evidence without selecting favorable reviews or widening its scope."""
    root = run / name
    if not _normal_path(root).is_relative_to(run):
        return [dict(directory=str(root), issue="outside_owned_visit_not_read", files={})]
    if not _io_path(root).exists():
        return []
    rows = []
    try:
        children = sorted(_io_path(root).iterdir())
    except OSError as exc:
        return [dict(directory=str(root), issue=type(exc).__name__, files={})]
    for child in children:
        path = _normal_path(child)
        row = dict(directory=str(path), issue=None, files={})
        rows.append(row)
        if not path.is_relative_to(_normal_path(root)):
            row["issue"] = "outside_evidence_directory_not_read"
            continue
        if not _io_path(path).is_dir():
            row["issue"] = "unregistered_evidence_file"
            continue
        for filename in filenames:
            value = reader.read(path / filename)
            version = value.get("schema_version") if isinstance(value, dict) else None
            versions = {1, 2} if name == "checkpoints" and filename == "consistency.json" else {1}
            supported = type(version) is int and version in versions
            row["files"][filename] = dict(
                state=f"reported_version_{version}" if supported else "unknown_or_unsupported",
                reported=value,
            )
    return rows


def _closure_timing(intent, closure, clock):
    start = intent.get("at") if isinstance(intent, dict) else None
    end = closure.get("closed_at") if isinstance(closure, dict) else None
    departure = _epoch(clock["declared_departure_at"]) if clock["declared_departure_at"] else None
    duration = end - start if _number(start) and _number(end) and end >= start else None
    relative = end - departure if _number(end) and departure is not None else None
    return dict(
        closure_elapsed_utc_s=duration, closure_vs_declared_departure_utc_s=relative,
        basis="UTC artifact timestamps; not monotonic latency or driver waiting time",
        closure_reason=None if duration is not None else
        "closure_timestamps_unavailable_or_reversed",
        departure_reason=None if relative is not None else
        "declared_departure_or_closure_timestamp_unknown",
    )


def _worker_attempt(reader, run, worker_root, clock):
    number = 1 if worker_root.name == "worker" else int(worker_root.name[7:])
    receipt_root = run / "capture-attempts" / f"{number:04d}"
    receipts = {name: reader.read(receipt_root / f"{name}.json")
                for name in ("intent", "spawn", "exit", "capture-ready")}
    # Legacy first-launch receipts stay separate and unchanged.
    if worker_root.name == "worker":
        receipts["legacy_intent"] = reader.read(run / "worker-intent.json")
        receipts["legacy_spawn"] = reader.read(run / "worker-spawn.json")
    process_receipt = reader.read(worker_root / "worker-process.json")
    closure = reader.read(worker_root / "closure.json")
    if closure and (type(closure.get("schema_version")) is not int
                    or closure["schema_version"] != 1):
        closure = None
    intent = reader.read(worker_root / "closure-intent.json")
    if intent and (type(intent.get("schema_version")) is not int or intent["schema_version"] != 1):
        intent = None
    recordings, worker = _recordings(reader, worker_root, closure)
    for recording in recordings:
        recording.update(worker_directory=worker_root.name, worker_attempt=number)
    issues = []
    launch = receipts["intent"]
    if launch is not None and (launch.get("worker_directory") != worker_root.name
                               or type(launch.get("attempt")) is not int
                               or launch["attempt"] != number):
        issues.append("launch_receipt_identity_mismatch")
    if intent is not None and closure is None:
        issues.append("closure_intent_without_result")
    if worker is None:
        issues.append("worker_status_unknown")
    if _normal_path(worker_root / "sessions").parent != _normal_path(worker_root):
        issues.append("sessions_root_outside_worker_not_read")
    exited = receipts["exit"]
    exit_code = exited.get("exit_code") if isinstance(exited, dict) else None
    return dict(
        attempt=number, worker_directory=worker_root.name, path=str(_normal_path(worker_root)),
        worker_status=worker.get("status") if worker else "unknown",
        worker_error=worker.get("error_type") if worker else None,
        worker_stop_reason=worker.get("stop_reason") if worker else None,
        worker_exit=exit_code if type(exit_code) is int else None,
        worker_capture={key: worker.get(key) for key in (
            "continuous_source_gaps", "capture_phase", "source_gap_reason", "source_gap_count",
            "recording_storage",
        )} if worker else None,
        launch_receipts=receipts, worker_process=process_receipt,
        closure=closure, closure_intent=intent,
        checkpoints=_supplemental(reader, _normal_path(worker_root), "checkpoints", (
            "intent.json", "handoff.json", "status.json", "closure-intent.json", "closure.json",
            "consistency.json")),
        closure_timing=_closure_timing(intent, closure, clock),
        recording_count=len(recordings), issues=issues,
    ), recordings, worker


def _flat_receipts(reader, run, name):
    root = run / name
    if not _normal_path(root).is_relative_to(run):
        return [dict(issue="outside_owned_visit_not_read")]
    try:
        return [dict(path=path.name, receipt=reader.read(path))
                for path in sorted(_io_path(root).glob("*.json"))]
    except OSError as error:
        return [dict(issue=type(error).__name__)]


def build_visit_outcome(run: Path):
    """Snapshot existing evidence only; unknown metrics stay null with reasons."""
    run = Path(run).resolve(strict=True)
    reader = _Reader(run)
    parent = reader.read(run / "status.json")
    if parent and (type(parent.get("schema_version")) is not int or parent["schema_version"] != 1):
        parent = None
    clock = _clock(reader, run, parent)
    current = parent.get("current_worker", "worker") if parent else "worker"
    worker_attempts, recordings, worker = [], [], None
    for worker_root in worker_directories(run):
        attempt, rows, worker_state = _worker_attempt(reader, run, worker_root, clock)
        worker_attempts.append(attempt)
        recordings.extend(rows)
        if worker_root.name == current:
            worker = worker_state
    selected = next((a for a in worker_attempts if a["worker_directory"] == current), None)
    operations = None
    radio_root = run
    if parent and parent.get("operations_mode") == "service":
        radio_root = run / "operations"
        service_run = reader.read(radio_root / "run.json")
        service_status = reader.read(radio_root / "status.json")
        reported = parent.get("operations") or {}
        bound = bool(isinstance(service_run, dict) and isinstance(service_status, dict)
                     and service_run.get("schema_version") == 1
                     and service_status.get("schema_version") == 1
                     and service_run.get("visit") == str(run)
                     and service_run.get("service_id") is not None
                     and service_run.get("service_id") == service_status.get("service_id")
                     == reported.get("service_id") and reported.get("binding_valid") is True)
        operations = dict(binding_reported=bound, run=service_run, status=service_status,
                          intent=reader.read(run / "operations-intent.json"),
                          launcher=reader.read(run / "operations-spawn.json"),
                          forwarding=_flat_receipts(reader, run, "operations-forwarded"),
                          receipts=_flat_receipts(reader, run, "operations/receipts"))
    radio, histories = _radio(reader, radio_root)
    if operations and not operations["binding_reported"]:
        radio.update(known=False, reason="operations_binding_unverified",
                     final_state_counts=None, transport_completed_count=None,
                     explicit_acknowledgment_count=None)
    reviews = _supplemental(reader, run, "reviews", ("attempt.json", "review.json", "failure.json"))
    coverage = _supplemental(reader, run, "coverage", ("report.json",))
    host_radio = parent.get("radio") if isinstance(parent, dict) else None
    radio["reported_host"] = (
        {
            key: host_radio.get(key)
            for key in (
                "delivery_health",
                "delivery_state",
                "reason",
                "failure_type",
                "muted",
                "closed",
            )
        }
        if isinstance(host_radio, dict)
        else None
    )
    timing = dict(selected["closure_timing"]) if selected else _closure_timing(None, None, clock)
    terminal = (
        isinstance(parent, dict)
        and parent.get("status") in ("complete", "incomplete")
        and selected is not None
        and (operations is None or operations["binding_reported"]
             and operations["status"].get("status") in {"complete", "incomplete"})
        and all(a["worker_status"] in ("complete", "failed") for a in worker_attempts)
    )
    if not terminal:
        radio.update(
            known=False,
            reason="nonterminal_or_unknown_visit_snapshot",
            final_state_counts=None,
            transport_completed_count=None,
            explicit_acknowledgment_count=None,
        )
    audio_enabled = None
    if operations and operations["binding_reported"]:
        configured = operations["run"].get("audio_enabled")
        audio_enabled = configured if type(configured) is bool else None
    elif operations is None and parent and parent.get("operations_mode") in ("embedded", None):
        configured = parent.get("audio")
        if configured in ("enabled_muted", "disabled"):
            audio_enabled = configured == "enabled_muted"
    supervision_path = radio_root / "supervision.sqlite3"
    raw = None
    if operations or (terminal and audio_enabled is not None):
        raw = reader.read(supervision_path, format="sqlite")
    if operations:
        entry = reader.sources[str(_normal_path(supervision_path))]
        operations["supervision_journal"] = (
            dict(sha256=entry["sha256"], bytes=entry["bytes"]) if raw is not None else None)
    notices, audit_reason = None, None
    if not terminal:
        audit_reason = "nonterminal_or_unbound_visit"
    elif audio_enabled is None:
        audit_reason = "radio_configuration_unknown"
    else:
        sidecars = [reader.read(supervision_path.with_name(supervision_path.name + suffix),
                                format="presence") for suffix in ("-wal", "-journal")]
        if all(present is False for present in sidecars):
            notices = _fuel_notices(raw)
        else:
            audit_reason = "supervision_sidecar_present_or_unknown"
    changed = reader.verify()
    if changed:
        for recording in recordings:
            if recording["lap_review"] is not None:
                recording.update(lap_review=None, lap_review_reason="source_changed_during_summary")
        clock = dict(
            clock,
            known=False,
            reason="source_changed_during_summary",
            elapsed_s=None,
            activity_s=None,
            not_driving_reason_s=None,
            reason_policy=None,
            declared_departure_at=None,
            prehost=None,
            presence="unknown",
        )
        timing.update(
            closure_elapsed_utc_s=None,
            closure_vs_declared_departure_utc_s=None,
            closure_reason="source_changed_during_summary",
            departure_reason="source_changed_during_summary",
        )
        for attempt in worker_attempts:
            attempt["closure_timing"].update(timing)
        radio.update(
            known=False,
            reason="source_changed_during_summary",
            final_state_counts=None,
            transport_completed_count=None,
            explicit_acknowledgment_count=None,
        )
    if changed or (operations and not operations["binding_reported"]):
        for transmission in radio["transmissions"]:
            transmission["software_timing"] = None
        radio["software_timing_scope"] = "unverified"
    else:
        radio["software_timing_scope"] = "reported_terminal" if radio["known"] else "parsed_prefix"
    radio["fuel_return_audit"] = _fuel_return_audit(
        notices, histories, audio_enabled=audio_enabled, radio_known=radio["known"],
        reason="source_changed_during_summary" if changed else audit_reason)
    return dict(
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        visit_path=str(run),
        source_snapshot="changed" if changed else "stable_at_read",
        snapshot_scope="reported_terminal" if terminal else "nonterminal_or_unknown",
        visit_status=parent.get("status") if isinstance(parent, dict) else "unknown",
        mode=parent.get("mode") if isinstance(parent, dict) else "unknown",
        visit_problem=parent.get("problem") if isinstance(parent, dict) else None,
        worker_status=worker.get("status") if isinstance(worker, dict) else "unknown",
        worker_error=worker.get("error_type") if isinstance(worker, dict) else None,
        worker_stop_reason=worker.get("stop_reason") if isinstance(worker, dict) else None,
        worker_exit=parent.get("worker_exit") if isinstance(parent, dict) else None,
        current_worker=current if isinstance(current, str) else None,
        worker_attempts=worker_attempts,
        worker_summary_scope="selected current worker only; all attempts retained separately",
        worker_capture={key: worker.get(key) for key in (
            "continuous_source_gaps", "capture_phase", "source_gap_reason", "source_gap_count",
            "recording_storage",
        )} if isinstance(worker, dict) else None,
        recording_storage=parent.get("recording_storage") if parent else None,
        clock=clock,
        recordings=recordings,
        closure_timing=timing,
        radio=radio,
        operations=operations,
        output_reviews=reviews,
        archive_coverage_audits=coverage,
        supplemental_evidence_scope=(
            "Reported attachments only; output bindings and archive scope are not re-audited. "
            "Chief opinion is not driver feedback; neither proves delivery. "
            "A selected archive window is not automatically whole-visit coverage."
        ),
        unmeasured=dict(
            capture_coverage=None,
            false_call_count=None,
            missed_call_count=None,
            useful_debrief_latency_s=None,
            reason="not_established_by_these_artifacts",
        ),
        sources=list(reader.sources.values()),
        limits=[
            "Read-only reported evidence; no validation or analysis rerun.",
            "All inventoried attempts retained; no best-lap selection or coaching inference.",
            "Reason totals subdivide non-driving time; rest is not wasted time.",
            "Transport completion and explicit acknowledgment do not establish acoustic delivery.",
            "Replay/test presence is not real seat time; no inferred departure or occupancy.",
        ],
    )


def _write_new(path, text):
    path = _io_path(path)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists():
        raise FileExistsError(path)
    partial.rename(path)


def _lap_review_lines(recordings):
    if not recordings:
        return ["Lap timing is unavailable because no recording was inventoried."]
    lines = ["## Lap timing", ""]
    for index, recording in enumerate(recordings, start=1):
        review = recording["lap_review"]
        if review is None:
            reason = recording["lap_review_reason"].replace("_", " ")
            lines.append(f"- Recording {index}. Lap timing unavailable ({reason}).")
            continue
        context = review["context"]
        label = escape(f"{context['car']} at {context['track']} ({context['session_type']})")
        counts = review["statistics"]
        lines.append(f"- Recording {index}. {label}. {counts['valid_laps']} of "
                     f"{counts['total_lap_records']} lap records were valid.")
        for source, stats in review["timing_groups"].items():
            if stats["best_s"] is None:
                continue
            line = (f"  - {TIMING_LABELS[source]} ({stats['valid_laps']} valid). "
                    f"Best {stats['best_s']:.3f} s, "
                    f"median {stats['median_s']:.3f} s")
            if stats["spread_s"] is not None:
                line += f", full valid lap spread {stats['spread_s']:.3f} s"
            lines.append(line + ".")
        if review["exclusions"]:
            detail = ", ".join(f"{count} {reason.replace('_', ' ')}"
                               for reason, count in sorted(review["exclusions"].items()))
            lines.append(f"  - Exclusions include {detail}.")
        if recording["reported_analysis"] == "FAIL":
            lines.append("  - The Analysis Run failed.")
    return lines


def write_visit_outcome(run: Path, output_dir: Path | None = None):
    destination = (
        Path(output_dir) if output_dir is not None else Path(run) / "outcomes" / uuid4().hex[:10]
    )
    if any(_normal_path(destination).is_relative_to(_normal_path(worker / "sessions"))
           for worker in worker_directories(run)):
        raise ValueError("outcome_destination_inside_recordings")
    report = build_visit_outcome(run)
    _io_path(destination).mkdir(parents=True, exist_ok=False)
    path = destination / "report.json"
    _write_new(path, json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = [
        "# Driver Visit outcome",
        "",
        f"Reported visit: {report['visit_status']} ({report['mode']}).",
        f"Clock totals known: {report['clock']['known']}. "
        f"Recording attempts: {len(report['recordings'])}.",
    ]
    if prehost := report["clock"]["prehost"]:
        lines.append(f"The visit includes {prehost['unknown_s']:.1f} seconds of unknown activity "
                     "between declared arrival and host startup.")
        intent = prehost.get("declared_intent")
        if intent and intent["state"] == "verified":
            reasons = intent["reason_s"]
            lines.append(f"Before host startup, {reasons['assistant_wait']:.1f} seconds were "
                         "declared as waiting for the Chief and "
                         f"{reasons['intentional_rest']:.1f} seconds as intentional rest.")
        elif intent:
            lines.append("Pre-start waiting reasons could not be verified.")
    if report["clock"]["known"]:
        assigned_wait = report["clock"]["not_driving_reason_s"]["assistant_wait"]
        lines.append(f"During the host run, {assigned_wait:.1f} seconds of non-driving time "
                     "were tagged as waiting for the Chief.")
    lines.extend(["", *_lap_review_lines(report["recordings"])])
    audit = report["radio"]["fuel_return_audit"]
    if audit["decision_count"] is not None:
        count = audit["decision_count"]
        lines.extend(["", "## Fuel radio", "", f"The supervisor recorded {count} committed "
                      f"fuel-return {'decision' if count == 1 else 'decisions'}."])
        labels = {
            "confirmed_before_deadline": "Transport confirmed before the deadline",
            "confirmation_at_or_after_deadline": "Transport confirmed at or after the deadline",
            "audio_disabled": "Audio disabled",
            "no_dispatch_record": "No record in the complete dispatcher journal",
            "dispatch_evidence_unavailable": "Dispatcher evidence unavailable",
            "binding_mismatch": "Decision and transmission identity mismatch",
            "start_unverified": "Transport start unverified",
        }
        lines.extend(["", *(f"- {labels.get(outcome, outcome.capitalize())} ({total})."
                           for outcome, total in sorted(audit["outcome_counts"].items()))])
        unmatched = audit["unmatched_transmission_count"]
        if unmatched:
            lines.extend(["", f"The radio journal has {unmatched} fuel transmissions without a "
                          "matching committed decision."])
    lines.extend([
        "",
        "All source locators, hashes, attempts and unknowns are retained in report.json.",
        "Transport completion is not proof of hearing. No analysis or coaching was rerun.",
    ])
    _write_new(destination / "report.md", "\n".join(lines) + "\n")
    return dict(
        directory=str(destination.resolve()), report_path=str(path.resolve()), report=report
    )
