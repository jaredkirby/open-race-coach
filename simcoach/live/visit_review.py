"""Explicit private output assessments; never claims delivery or driver opinion by inference."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from simcoach.live.json_read import read_with_retry
from simcoach.live.supervisor import strict_json
from simcoach.live.visit_outcome import worker_directories

VERDICTS = ("useful", "insufficient", "misleading", "failed")
REVIEWERS = ("chief", "driver")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _path(value):
    raw = str(value)
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return Path(raw).resolve()


def _io(path):
    raw = str(path)
    if os.name == "nt":
        raw = "\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw
    return Path(raw)


def _inside(path, root):
    resolved = _path(path)
    if not resolved.is_relative_to(root):
        raise ValueError("outside_owned_visit")
    return resolved


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _read_bound(path, root, *, snapshot=False):
    """Check path and open-handle identity around an exact-byte read."""
    resolved = _inside(path, root)
    before = _io(resolved).stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("output_not_regular_file")
    with _io(resolved).open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("output_not_regular_file")
        if not snapshot and _identity(before) != _identity(opened):
            raise ValueError("source_changed_before_read")
        raw = handle.read()
        after_handle = os.fstat(handle.fileno())
    if _inside(path, root) != resolved:
        raise ValueError("source_path_changed")
    if (_identity(opened) != _identity(after_handle) or len(raw) != opened.st_size
            or not snapshot and _identity(opened) != _identity(_io(resolved).stat())):
        raise ValueError("source_changed_during_read")
    return raw, dict(path=str(resolved), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                     device=opened.st_dev, file_id=str(opened.st_ino),
                     modified_ns=str(opened.st_mtime_ns))


def _visit_status(run):
    raw, source = read_with_retry(lambda: _read_bound(run/"status.json", run, snapshot=True),
                                  role="review_visit_status")
    status = strict_json(raw.decode("utf-8-sig"))
    if (not isinstance(status, dict) or type(status.get("schema_version")) is not int
            or status["schema_version"] != 1 or status.get("mode") not in ("live", "replay")):
        raise ValueError("visit_mode_unavailable")
    return status, source


def _visit_id(status):
    clock = status.get("clock")
    identity = clock.get("visit_id") if isinstance(clock, dict) else None
    return identity if isinstance(identity, str) and identity else None


def _live_visit_identity(run, status):
    visit_id, pid = _visit_id(status), status.get("pid")
    if status["mode"] != "live" or visit_id is None or type(pid) is not int or pid <= 0:
        return None
    try:
        host_raw, host_source = _read_bound(run/"host-process.json", run)
    except FileNotFoundError:
        return None  # Legacy visits still have the exact status comparison.
    host = strict_json(host_raw.decode("utf-8-sig"))
    process = host.get("process") if isinstance(host, dict) else None
    if (not isinstance(process, dict) or type(host.get("schema_version")) is not int
            or host["schema_version"] != 1
            or type(process.get("pid")) is not int or process["pid"] != pid):
        raise ValueError("visit_host_binding_invalid")
    return dict(visit_id=visit_id, pid=pid, host_source=host_source)


def _publish(path, value):
    """Publish without overwrite; a failed temporary remains inspectable."""
    temporary = path.with_suffix(".partial")
    with _io(temporary).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    if os.name == "nt":
        # Windows rename refuses an existing destination. Unlike replace(), it
        # publishes without overwrite and needs no hard-link privilege.
        os.rename(_io(temporary), _io(path))
    else:
        # POSIX rename may overwrite; preserve exclusive publication via link.
        os.link(_io(temporary), _io(path))
        # Once published, cleanup failure must not contradict acceptance.
        with suppress(OSError):
            _io(temporary).unlink()


def _epoch(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.timestamp() if stamp.utcoffset() == timedelta(0) else None
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _departure(run, at, mode, visit_id):
    sources = []
    if mode != "live":
        return None, sources
    try:
        values = []
        for name in ("stop", "clock/status.json", "clock/summary.json"):
            raw, binding = _read_bound(run/name, run)
            sources.append(binding)
            values.append(strict_json(raw.decode("utf-8-sig")))
        declaration, status, summary = values
        if not isinstance(declaration, dict) or not isinstance(summary, dict) or status != summary:
            return None, sources
        departure = _epoch(summary.get("updated_at"))
        started = _epoch(summary.get("started_at"))
        if (type(summary.get("schema_version")) is int and summary["schema_version"] == 1
                and summary.get("status") == "complete"
                and summary.get("terminal_intent") == "complete"
                and summary.get("publication_complete") is True
                and summary.get("publication_error") is None
                and summary.get("presence") == "declared_absent"
                and isinstance(summary.get("visit_id"), str) and summary["visit_id"]
                and (visit_id is None or summary["visit_id"] == visit_id)
                and _number(summary.get("elapsed_s"))
                and _number(started) and _number(departure) and started <= departure <= at
                and declaration.get("declaration") == "driver_left"
                and _number(declaration.get("elapsed")) and _number(declaration.get("at"))
                and math.isclose(declaration["at"], departure, rel_tol=0, abs_tol=0.000001)
                and all(_read_bound(Path(source["path"]), run)[1] == source for source in sources)):
            return departure, sources
    except (OSError, ValueError, RecursionError):
        pass
    return None, sources


def record_review(run, *, output: Path, verdict, note, reviewer="chief",
                  driver_feedback_confirmed=False, clock=time.time):
    """Append an explicit assessment to a new private attempt directory.

    Invalid attempts in an existing visit retain attempt/failure artifacts and raise.
    The clock is sampled here; the API accepts no caller-supplied review timestamp.
    """
    run = _path(run)
    if not _io(run).is_dir():
        raise ValueError("visit_directory_missing")
    reviews = _inside(run/"reviews", run)
    _io(reviews).mkdir(exist_ok=True)
    reviews = _inside(run/"reviews", run)
    attempt = reviews/uuid4().hex
    _io(attempt).mkdir(exist_ok=False)
    # Keep rejected input types recognizable without serializing arbitrary user objects.
    request = dict(schema_version=1, output=str(output),
                   verdict=verdict if isinstance(verdict, str) else None,
                   note=note if isinstance(note, str) else None,
                   reviewer=reviewer if isinstance(reviewer, str) else None,
                   driver_feedback_confirmed=driver_feedback_confirmed
                   if type(driver_feedback_confirmed) is bool else None)
    try:
        _publish(attempt/"attempt.json", request)
        at = clock()
        if not _number(at):
            raise ValueError("invalid_review_clock")
        if reviewer not in REVIEWERS or verdict not in VERDICTS:
            raise ValueError("invalid_review_role_or_verdict")
        if not isinstance(note, str) or not note.strip() or len(note) > 4000:
            raise ValueError("review_note_must_have_1_to_4000_characters")
        if type(driver_feedback_confirmed) is not bool:
            raise ValueError("driver_confirmation_requires_boolean")
        if reviewer == "driver" and not driver_feedback_confirmed:
            raise ValueError("explicit_driver_feedback_required")
        status, visit_source = _visit_status(run)
        visit_identity = _live_visit_identity(run, status)
        target = _inside(output, run)
        allowed = [run/name for name in ("output", "outcomes", "coverage", "clock")]
        allowed.extend(worker_directories(run))
        if not any(target.is_relative_to(parent) for parent in allowed):
            raise ValueError("not_a_visit_output")
        _, binding = _read_bound(output, run)
        if not any(Path(binding["path"]).is_relative_to(parent) for parent in allowed):
            raise ValueError("not_a_visit_output")
        latest, latest_source = _visit_status(run)
        if visit_identity is None:
            same_visit = latest_source == visit_source
        else:
            same_visit = (
                latest["mode"] == "live"
                and _visit_id(latest) == visit_identity["visit_id"]
                and type(latest.get("pid")) is int and latest["pid"] == visit_identity["pid"]
                and _read_bound(run/"host-process.json", run)[1] == visit_identity["host_source"]
            )
        if not same_visit:
            raise ValueError("source_changed_during_review")
        departure_at, departure_sources = _departure(run, at, status["mode"], _visit_id(status))
        if _read_bound(output, run)[1] != binding:
            raise ValueError("source_changed_during_review")
        review = dict(
            schema_version=1, review_id=attempt.name, reviewed_at=at, visit_mode=status["mode"],
            output=binding, visit_source=visit_source, verdict=verdict, note=note.strip(),
            reviewer=reviewer, driver_feedback_confirmed=reviewer == "driver",
            reviewer_identity_authenticated=False, delivery_verified=False, audio_heard=None,
            declared_departure_at=departure_at, departure_sources=departure_sources,
            review_latency_utc_s=at-departure_at if departure_at is not None else None,
            latency_kind="explicit_review_after_declared_departure",
        )
        if visit_identity is not None:
            review.update(visit_identity=visit_identity,
                          visit_source_before_publication=latest_source)
        _publish(attempt/"review.json", review)
        return dict(path=str(attempt/"review.json"), review=review)
    except Exception as exc:
        failure = dict(schema_version=1, status="failed", error_type=type(exc).__name__,
                       reason=str(exc), review_accepted=False)
        # Preserve the original failure plus any partial failure-publication evidence.
        with suppress(Exception):
            _publish(attempt/"failure.json", failure)
        raise
