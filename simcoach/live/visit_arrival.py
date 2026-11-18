"""Private seated declarations before a Driver Visit host is available."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from uuid import uuid4

from simcoach.cli.race_watch import SourceLock
from simcoach.live.supervisor import number, strict_json
from simcoach.live.visit_clock import REASONS, _stamp
from simcoach.live.visit_review import _io, _path, _publish, _read_bound

MAX_PREHOST_SECONDS = 7200
MAX_ARRIVAL_BYTES = 4096
MAX_PREHOST_NOTES = 64


class ArrivalError(ValueError):
    """A bounded reason the Chief can use to inspect the arrival."""


def _now():
    return datetime.now(UTC), time.perf_counter()


def declare_arrival(root, *, present):
    if present is not True:
        raise ArrivalError("explicit_presence_required")
    root = _path(root)
    lock = SourceLock(root / "visit.lock")
    try:
        at, elapsed = _now()
        stamp, elapsed = _stamp(at, elapsed)
        pending = pending_arrival(root)
        if pending is not None:
            original_at, original_elapsed, _ = _declared(root, pending)
            if _duration(original_at, original_elapsed, at, elapsed) is None:
                raise ArrivalError(f"arrival_clock_discontinuous_or_stale:{pending}")
            return dict(arrival_id=pending,
                        arrival_path=str(_directory(root, pending) / "declaration.json"),
                        at=original_at.isoformat(), reused=True)
        identity = uuid4().hex
        directory = _directory(root, identity)
        _io(directory).mkdir(parents=True, exist_ok=False)
        path = directory / "declaration.json"
        _publish(path, dict(schema_version=1, arrival_id=identity, declaration="driver_arrived",
                            visit_root=str(root), at=stamp, elapsed=elapsed))
        return dict(arrival_id=identity, arrival_path=str(path), at=stamp, reused=False)
    finally:
        lock.close()


def _directory(root, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ArrivalError("invalid_arrival_id")
    directory = root / "arrivals" / identity
    if _path(directory) != directory:
        raise ArrivalError("arrival_directory_redirected")
    return directory


def _read(root, identity, name):
    directory = _directory(root, identity)
    path = directory / name
    if _path(path) != path or _io(path).stat().st_size > MAX_ARRIVAL_BYTES:
        raise ArrivalError("arrival_file_invalid")
    raw, binding = _read_bound(path, directory)
    return strict_json(raw.decode("utf-8")), binding["sha256"]


def _declared(root, identity):
    value, digest = _read(root, identity, "declaration.json")
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "arrival_id", "declaration", "visit_root",
                              "at", "elapsed"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["arrival_id"] != identity or value["declaration"] != "driver_arrived"
            or value["visit_root"] != str(root)):
        raise ArrivalError("arrival_declaration_invalid_or_unbound")
    try:
        at = datetime.fromisoformat(value["at"])
        _, elapsed = _stamp(at, number(value["elapsed"]))
    except (TypeError, ValueError):
        raise ArrivalError("arrival_clock_invalid") from None
    return at, elapsed, digest


def _abandoned(directory):
    return any(re.fullmatch(r"abandonment-[a-f0-9]{32}\.json", item.name)
               for item in _io(directory).glob("abandonment-*.json"))


def pending_arrival(root):
    """Resolve the one available arrival under the caller's visit-root lock."""
    root = _path(root)
    arrivals = root / "arrivals"
    if _path(arrivals) != arrivals:
        raise ArrivalError("arrival_directory_redirected")
    if not _io(arrivals).exists():
        return None
    pending = []
    for item in _io(arrivals).iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}", item.name):
            continue
        directory = _directory(root, item.name)
        if (_io(directory / "declaration.json").exists()
                and not _io(directory / "resolution.json").exists()
                and not _abandoned(directory)):
            pending.append(directory)
    if len(pending) > 1:
        identities = ",".join(sorted(path.name for path in pending)[:8])
        raise ArrivalError(f"multiple_pending_arrivals:{identities}")
    if not pending:
        return None
    directory = pending[0]
    if _io(directory / "resolution.partial").exists():
        raise ArrivalError(f"arrival_resolution_uncertain:{directory.name}")
    return directory.name


def abandon_before_host(root, identity, *, confirmed):
    """Retire an unclaimed declaration with no claim about time or departure."""
    if confirmed is not True:
        raise ArrivalError("explicit_abandonment_required")
    root = _path(root)
    lock = SourceLock(root / "visit.lock")
    try:
        directory = _directory(root, identity)
        if _io(directory / "resolution.json").exists() or _abandoned(directory):
            raise ArrivalError("arrival_already_resolved_or_uncertain")
        declaration = directory / "declaration.json"
        if _path(declaration) != declaration or _io(declaration).stat().st_size > MAX_ARRIVAL_BYTES:
            raise ArrivalError("arrival_file_invalid")
        _, source = _read_bound(declaration, directory)
        stamp, _ = _stamp(*_now())
        path = directory / f"abandonment-{uuid4().hex}.json"
        receipt = dict(schema_version=1, arrival_id=identity, arrival_sha256=source["sha256"],
                       disposition="abandoned", at=stamp, presence="unknown", elapsed_s=None)
        _publish(path, receipt)
        return dict(receipt, abandonment_path=str(path))
    finally:
        lock.close()


def _assert_unresolved(directory):
    resolution = directory / "resolution.json"
    if (_io(resolution).exists() or _io(resolution.with_suffix(".partial")).exists()
            or _abandoned(directory)):
        raise ArrivalError("arrival_already_resolved_or_uncertain")
    return resolution


def _duration(start_at, start_elapsed, at, elapsed):
    counter = elapsed - start_elapsed
    wall = (at - start_at).total_seconds()
    if (0 <= counter <= MAX_PREHOST_SECONDS and 0 <= wall <= MAX_PREHOST_SECONDS
            and abs(wall - counter) <= .5):
        return counter
    return None


def _note_names(directory):
    # A final file can coexist with its partial link after an interrupted POSIX cleanup.
    files = {item.name for item in _io(directory).glob("reason-*")}
    notes = sorted(name for name in files if not name.endswith(".partial"))
    for name in files:
        if name.endswith(".partial") and f"{name[:-8]}.json" not in files:
            raise ArrivalError("prehost_reason_publication_uncertain")
    if (len(notes) > MAX_PREHOST_NOTES
            or notes != [f"reason-{i:04d}.json" for i in range(1, len(notes) + 1)]):
        raise ArrivalError("prehost_reason_sequence_invalid")
    return notes


def _reason_prefix(root, identity, start_at, start_elapsed, declaration_digest):
    totals = dict.fromkeys(REASONS, 0.0)
    at, elapsed, reason, digest = start_at, start_elapsed, "unspecified", declaration_digest
    names = _note_names(_directory(root, identity))
    for index, name in enumerate(names, start=1):
        note, next_digest = _read(root, identity, name)
        if (not isinstance(note, dict)
                or set(note) != {"schema_version", "arrival_id", "sequence", "reason", "at",
                                 "elapsed", "previous_sha256"}
                or type(note["schema_version"]) is not int or note["schema_version"] != 1
                or note["arrival_id"] != identity or type(note["sequence"]) is not int
                or note["sequence"] != index or note["reason"] not in REASONS
                or note["previous_sha256"] != digest):
            raise ArrivalError("prehost_reason_binding_invalid")
        next_at = datetime.fromisoformat(note["at"])
        _, next_elapsed = _stamp(next_at, number(note["elapsed"]))
        seconds = _duration(at, elapsed, next_at, next_elapsed)
        if seconds is None or _duration(start_at, start_elapsed, next_at, next_elapsed) is None:
            raise ArrivalError("prehost_reason_clock_discontinuous")
        totals[reason] += seconds
        at, elapsed, reason, digest = next_at, next_elapsed, note["reason"], next_digest
    return at, elapsed, reason, digest, len(names), totals


def note_before_host(root, identity, *, reason):
    """Record a prospective declared purpose without classifying driving activity."""
    if reason not in REASONS:
        raise ArrivalError("invalid_reason")
    root = _path(root)
    lock = SourceLock(root / "visit.lock")
    try:
        directory = _directory(root, identity)
        _assert_unresolved(directory)
        pending_arrival(root)  # Refuse attribution while legacy arrivals overlap.
        start_at, start_elapsed, digest = _declared(root, identity)
        at, elapsed = _now()
        stamp, elapsed = _stamp(at, elapsed)
        prior_at, prior_elapsed, _, last_digest, count, _ = _reason_prefix(
            root, identity, start_at, start_elapsed, digest)
        if (_duration(start_at, start_elapsed, at, elapsed) is None
                or _duration(prior_at, prior_elapsed, at, elapsed) is None):
            raise ArrivalError("prehost_reason_clock_discontinuous")
        if count >= MAX_PREHOST_NOTES:
            raise ArrivalError("prehost_reason_limit")
        sequence = count + 1
        path = directory / f"reason-{sequence:04d}.json"
        _publish(path, dict(schema_version=1, arrival_id=identity, sequence=sequence,
                            reason=reason, at=stamp, elapsed=elapsed, previous_sha256=last_digest))
        return dict(arrival_id=identity, note_path=str(path), sequence=sequence,
                    reason=reason, at=stamp)
    finally:
        lock.close()


def _declared_intent(root, identity, *, start_at, start_elapsed, digest, at, elapsed, prehost):
    try:
        previous, counter, reason, head, count, totals = _reason_prefix(
            root, identity, start_at, start_elapsed, digest)
        if not count:
            return None
        tail = _duration(previous, counter, at, elapsed)
        if prehost is None or tail is None:
            raise ArrivalError("prehost_reason_clock_discontinuous")
    except (OSError, TypeError, ValueError, RecursionError, OverflowError):
        # Optional intent evidence never blocks a host or overwrites a failed note.
        return dict(state="unavailable")
    totals[reason] += tail
    return dict(state="verified", note_count=count, head_sha256=head, reason_s=totals)


def _resolution(root, identity, *, at, elapsed, run):
    start_at, start_elapsed, digest = _declared(root, identity)
    stamp, elapsed = _stamp(at, number(elapsed))
    prehost = _duration(start_at, start_elapsed, at, elapsed)
    if run is not None and prehost is None:
        raise ArrivalError(f"arrival_clock_discontinuous_or_stale:{identity}")
    result = dict(schema_version=1, arrival_id=identity, arrival_sha256=digest,
                  arrived_at=start_at.isoformat(), at=stamp,
                  elapsed=elapsed, elapsed_s=prehost, unknown_s=prehost,
                  resolution="claimed" if run is not None else "driver_left_before_host",
                  run=run.name if run is not None else None)
    intent = _declared_intent(root, identity, start_at=start_at, start_elapsed=start_elapsed,
                              digest=digest, at=at, elapsed=elapsed, prehost=prehost)
    if intent is not None:
        result["declared_intent"] = intent
    return start_at, start_elapsed, result


def _resolve(root, identity, *, at, elapsed, run):
    path = _assert_unresolved(_directory(root, identity))
    pending_arrival(root)
    resolution = _resolution(root, identity, at=at, elapsed=elapsed, run=run)
    _publish(path, resolution[2])
    return resolution


def claim_arrival(root, identity, *, at, elapsed, run):
    """Claim once under the visit lock, before host/recorder startup."""
    return _resolve(_path(root), identity, at=at, elapsed=elapsed, run=run)


def depart_before_host(root, identity, *, left):
    if left is not True:
        raise ArrivalError("explicit_departure_required")
    root = _path(root)
    lock = SourceLock(root / "visit.lock")
    try:
        at, elapsed = _now()
        _, _, result = _resolve(root, identity, at=at, elapsed=elapsed, run=None)
        return result
    finally:
        lock.close()


def verify_arrival_claim(run, binding):
    """Check the terminal host's claimed arrival against its private receipts."""
    if not isinstance(binding, dict):
        raise ArrivalError("arrival_claim_invalid")
    root = run.parent
    identity = binding.get("arrival_id")
    recorded, digest = _read(root, identity, "resolution.json")
    if recorded != binding or not isinstance(recorded, dict):
        raise ArrivalError("arrival_claim_changed")
    try:
        at = datetime.fromisoformat(recorded["at"])
        _, _, expected = _resolution(root, identity, at=at, elapsed=recorded["elapsed"], run=run)
    except (KeyError, TypeError, ValueError):
        raise ArrivalError("arrival_claim_invalid") from None
    claims = {key: value for key, value in recorded.items() if key != "declared_intent"}
    expected_intent = expected.pop("declared_intent", None)
    if claims != expected or type(recorded.get("schema_version")) is not int:
        raise ArrivalError("arrival_claim_invalid")
    verified = dict(recorded, resolution_sha256=digest)
    if "declared_intent" in recorded and recorded["declared_intent"] != expected_intent:
        verified["declared_intent"] = dict(state="unavailable")
    return verified
