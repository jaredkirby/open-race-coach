"""Replay-first lap-flag edge proposals. No simulator, radio, models or causal claims."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from simcoach.cli.capture_health import _status_fields
from simcoach.cli.race_watch import SourceLock
from simcoach.live.supervisor import Context, ParticipantView, integer, number, participant_view


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Observation:
    source_id: str
    at: float
    offset_s: float
    count: int
    sequence: int
    context: Context
    raw_lap: int
    invalidated: bool
    view: ParticipantView | None
    continuity_generation: int | None


def _observation(status, source_id, now, max_age, expected, *, require_view):
    if (not isinstance(source_id, str) or len(source_id) != 64
            or any(c not in "0123456789abcdef" for c in source_id)):
        raise ValueError("invalid_source_identity")
    try:
        observed_at = datetime.fromtimestamp(now, UTC)
    except (OSError, OverflowError, ValueError):
        raise ValueError("invalid_observation_time") from None
    sample = _status_fields(status, observed_at, max_age)
    if sample.health != "healthy":
        raise ValueError(sample.reason)
    latest = status["latest"]
    if type(latest.get("mVersion")) is not int or latest["mVersion"] != 14:
        raise ValueError("unsupported_layout")
    flag = latest.get("mLapInvalidated")
    if type(flag) is not bool:
        raise ValueError("invalid_lap_flag")
    sequence = integer(latest.get("mSequenceNumber"))
    if sequence % 2:
        raise ValueError("unstable_sequence")
    context = Context(latest.get("mCarName"), latest.get("mTrackLocation"),
                      latest.get("mTrackVariation"), latest.get("mSessionState"))
    if context != expected:
        raise ValueError("unexpected_context")
    if (integer(latest.get("mGameState"), maximum=7) != 2
            or integer(latest.get("mRaceState"), maximum=6) != 2
            or integer(latest.get("mPitMode"), maximum=5) != 0):
        raise ValueError("inactive_driving_context")
    view = participant_view(latest)
    if require_view and view is None:
        raise ValueError("participant_evidence_unavailable")
    return Observation(source_id, sample.updated_at.timestamp(), sample.offset_s,
                       sample.count, sequence, context, integer(latest.get("mCurrentLap")), flag,
                       view, sample.continuity_generation)


def assess_optional_output(proposal, *, now, muted, urgent, workload_clear):
    """Pure shadow assessment, never permission to bypass an actual radio owner."""
    number(now)
    if any(type(value) is not bool for value in (muted, urgent, workload_clear)):
        raise ValueError("explicit_output_guards_required")
    if not proposal["detected_at"] <= now < proposal["expires_at"]:
        return "expired_or_future"
    if muted:
        return "muted"
    if urgent:
        return "urgent_output_has_priority"
    if not workload_clear:
        return "workload_unknown"
    return "eligible_for_shadow_review"


class LapInvalidationDetector:
    """One locked journal, explicit source/context, consumed-before-return proposals.

    Reopening the same journal retains deduplication but never restores a false
    baseline or replays proposals. All outputs remain shadow-only, in both modes.
    """

    def __init__(self, expected: Context, journal_path: Path, *, mode="replay",
                 timestamp_basis="archive_sample", max_age_s=2, max_gap_s=2, ttl_s=2):
        if not isinstance(expected, Context):
            raise ValueError("expected_context_required")
        if mode not in ("replay", "shadow"):
            raise ValueError("invalid_reaction_mode")
        if timestamp_basis not in ("archive_sample", "collector_heartbeat"):
            raise ValueError("invalid_timestamp_basis")
        self.expected, self.mode, self.timestamp_basis = expected, mode, timestamp_basis
        self.max_age = number(max_age_s, minimum=.1, maximum=3)
        self.max_gap = number(max_gap_s, minimum=.1, maximum=3)
        self.ttl = number(ttl_s, minimum=.1, maximum=2)
        self.previous = None
        self.last_now = self.last_elapsed = None
        self.closed = False
        self.epoch = 0
        self.instance_id = uuid4().hex
        path = Path(journal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = SourceLock(path.with_suffix(path.suffix+".lock"))
        self.connection = None
        config = json.dumps(dict(schema_version=1, expected=asdict(expected), mode=mode,
                                 timestamp_basis=timestamp_basis, max_age_s=self.max_age,
                                 max_gap_s=self.max_gap, ttl_s=self.ttl), sort_keys=True)
        try:
            exists = path.exists()
            self.connection = sqlite3.connect(path)
            self.connection.execute("PRAGMA synchronous=FULL")
            if not exists:
                with self.connection:
                    self.connection.execute("CREATE TABLE metadata (config TEXT NOT NULL)")
                    self.connection.execute("INSERT INTO metadata VALUES (?)", (config,))
                    self.connection.execute(
                        "CREATE TABLE decisions "
                        "(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                    self.connection.execute(
                        "CREATE TABLE consumed (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            elif self.connection.execute("SELECT config FROM metadata").fetchall() != [(config,)]:
                raise ValueError("incompatible_reaction_journal")
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            self.lock.close()
            raise

    def _save(self, decision, proposal):
        with self.connection:
            if proposal is not None:
                inserted = self.connection.execute(
                    "INSERT OR IGNORE INTO consumed VALUES (?, ?)",
                    (proposal["id"], json.dumps(proposal, allow_nan=False)),
                ).rowcount
                if not inserted:
                    proposal = None
                    decision["reason"] = "already_consumed"
                elif decision["reason"] == "edge_expired":
                    proposal = None
            decision["proposal"] = proposal
            self.connection.execute("INSERT INTO decisions(payload) VALUES (?)",
                                    (json.dumps(decision, allow_nan=False),))
        return decision

    def observe(self, status, *, source_id, now, elapsed):
        if self.closed:
            raise RuntimeError("reaction_detector_closed")
        try:
            number(now)
            number(elapsed)
        except ValueError:
            self.previous = None
            raise
        candidate = None
        reason = "no_edge"
        proposal = None
        previous = self.previous
        try:
            candidate = _observation(status, source_id, now, self.max_age, self.expected,
                                     require_view=self.mode == "shadow")
            if self.last_now is not None and (
                now < self.last_now or elapsed < self.last_elapsed
                or abs((now-self.last_now)-(elapsed-self.last_elapsed)) > .5
            ):
                raise ValueError("observation_clock_changed")
            if self.last_elapsed is not None and elapsed-self.last_elapsed >= self.max_gap:
                raise ValueError("observation_gap")
            if previous is None:
                reason = ("initial_true_without_baseline" if candidate.invalidated
                          else "false_baseline")
            elif candidate == previous:
                reason = "duplicate_observation"
            elif candidate.source_id != previous.source_id:
                raise ValueError("source_changed")
            elif candidate.continuity_generation != previous.continuity_generation:
                raise ValueError("source_continuity_changed")
            elif candidate.view != previous.view:
                raise ValueError("viewed_slot_changed")
            elif (candidate.at <= previous.at or candidate.offset_s <= previous.offset_s
                  or candidate.count <= previous.count or candidate.sequence <= previous.sequence):
                raise ValueError("source_progress_invalid")
            elif candidate.at-previous.at >= self.max_gap:
                raise ValueError("source_gap")
            elif abs((candidate.at - previous.at) - (candidate.offset_s - previous.offset_s)) > .5:
                raise ValueError("source_clock_changed")
            elif candidate.raw_lap != previous.raw_lap:
                raise ValueError("raw_lap_changed")
            elif not previous.invalidated and candidate.invalidated:
                expires = candidate.at + self.ttl
                evidence = asdict(candidate)
                identity = {key: value for key, value in evidence.items()
                            if key not in {"count", "view"}
                            and (key != "continuity_generation" or value is not None)}
                proposal = dict(
                    id=_digest(dict(kind="lap_invalidated", edge=identity)),
                    kind="lap_invalidated", text="Lap marked invalid.",
                    mode=self.mode, timestamp_basis=self.timestamp_basis,
                    before=asdict(previous), edge=evidence,
                    observation_epoch=f"{self.instance_id}:{self.epoch}",
                    detected_at=now, decision_at=now, expires_at=expires,
                    delivery="NOT RUN", causal_claim=None,
                )
                reason = "edge_expired" if now >= expires else "flag_false_to_true"
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reason = str(exc) if type(exc) is ValueError else "malformed_observation"
            candidate = None
            self.epoch += 1
        decision = dict(schema_version=1, at=now, elapsed=elapsed, mode=self.mode,
                        timestamp_basis=self.timestamp_basis, reason=reason,
                        observation=asdict(candidate) if candidate else None)
        try:
            result = self._save(decision, proposal)
        except BaseException:
            self.previous = None
            self.closed = True
            self.connection.close()
            self.lock.close()
            raise
        self.previous = candidate
        self.last_now, self.last_elapsed = now, elapsed
        return result

    def close(self):
        if not self.closed:
            self.connection.close()
            self.lock.close()
            self.closed = True
