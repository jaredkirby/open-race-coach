"""Transient, source-progress-bounded activity evidence; never detects presence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from simcoach.cli.capture_health import _status_fields
from simcoach.live.supervisor import integer, number


@dataclass(frozen=True)
class _Observation:
    source_id: str
    at: float
    count: int
    offset: float
    sequence: int
    context: tuple[str, str, str, int]
    payload: tuple[int, float, int, float]
    activity: str
    continuity_generation: int | None

    @property
    def progress(self):
        return self.at, self.count, self.offset, self.sequence


class ActivityEvidence:
    """Admit two advancing source observations; hold only to original expiry.

    The caller must verify the archive whose SHA-256 identity it supplies. This
    observer reads no files and proves neither participant identity nor presence.
    Returned dictionaries contain detached scalar evidence, never source labels.
    """

    def __init__(self, *, max_age=2.0, max_poll_gap=1.0, clock_tolerance=0.5):
        self.max_age = number(max_age, minimum=0.001, maximum=2)
        self.max_poll_gap = number(max_poll_gap, minimum=0.001, maximum=2)
        self.clock_tolerance = number(clock_tolerance, minimum=0, maximum=0.5)
        self.previous: _Observation | None = None
        self.last_now: float | None = None
        self.last_elapsed: float | None = None
        self.clock_anchor: float | None = None
        self.source_anchor: float | None = None
        self.admitted = False
        self.expires_elapsed: float | None = None

    def _read(self, status, source_id, now):
        if not isinstance(source_id, str) or re.fullmatch(r"[0-9a-f]{64}", source_id) is None:
            raise ValueError("missing_verified_source_identity")
        sample = _status_fields(status, datetime.fromtimestamp(now, UTC), self.max_age)
        if sample.health != "healthy":
            raise ValueError(sample.reason)
        latest = status["latest"]
        if type(latest.get("mVersion")) is not int or latest["mVersion"] != 14:
            raise ValueError("unsupported_layout")
        sequence = integer(latest.get("mSequenceNumber"))
        if sequence % 2:
            raise ValueError("unstable_sequence")
        game = integer(latest.get("mGameState"), maximum=8)
        session = integer(latest.get("mSessionState"), maximum=6)
        pit = integer(latest.get("mPitMode"), maximum=5)
        speed = number(latest.get("mSpeed"))
        labels = tuple(latest.get(key) for key in
                       ("mCarName", "mTrackLocation", "mTrackVariation"))
        if any(type(label) is not str or len(label) > 256 or "\0" in label for label in labels):
            raise ValueError("invalid_context")
        if game in (1, 3, 4, 6, 7):
            activity = "not_driving"
        elif game == 2 and labels[0].strip() and labels[1].strip() and session != 0:
            activity = "not_driving" if speed <= 1 else "pit_moving" if pit else "driving"
        else:
            raise ValueError("activity_unavailable")
        return _Observation(source_id, sample.updated_at.timestamp(), sample.count,
                            sample.offset_s, sequence, (*labels, session),
                            (game, speed, pit, status["target_hz"]), activity,
                            sample.continuity_generation)

    def observe(self, status, *, source_id, now, elapsed):
        """Return advancing, bounded held, or unknown evidence for this poll."""
        candidate = None
        reason = None
        mode = "unknown"
        valid_clock = False
        clock_reset = False
        expires_elapsed = None
        try:
            now, elapsed = number(now), number(elapsed)
            datetime.fromtimestamp(now, UTC)
            valid_clock = True
            candidate = self._read(status, source_id, now)
            if self.last_now is not None:
                if now < self.last_now or elapsed < self.last_elapsed:
                    clock_reset = True
                    raise ValueError("observation_clock_regressed")
                if elapsed - self.last_elapsed > self.max_poll_gap:
                    clock_reset = True
                    raise ValueError("observation_gap")
                if abs(now - elapsed - self.clock_anchor) > self.clock_tolerance:
                    clock_reset = True
                    raise ValueError("observation_clock_changed")
            previous = self.previous
            if previous is None:
                raise ValueError("awaiting_advancing_observation")
            if candidate.source_id != previous.source_id:
                raise ValueError("source_changed")
            if candidate.continuity_generation != previous.continuity_generation:
                raise ValueError("source_continuity_changed")
            if candidate.context != previous.context:
                raise ValueError("context_changed")
            if candidate.progress == previous.progress:
                if candidate.payload != previous.payload:
                    candidate = None
                    raise ValueError("same_progress_payload_changed")
                if not self.admitted:
                    raise ValueError("awaiting_advancing_observation")
                if elapsed >= self.expires_elapsed:
                    candidate = None
                    raise ValueError("source_evidence_expired")
                expires_elapsed = self.expires_elapsed
                mode = "held"
            else:
                if candidate.at <= previous.at or candidate.offset <= previous.offset:
                    raise ValueError("source_clock_not_advancing")
                if candidate.count <= previous.count:
                    raise ValueError("sample_count_not_advancing")
                if candidate.sequence <= previous.sequence:
                    raise ValueError("sequence_not_advancing")
                source_delta = candidate.at - previous.at
                if source_delta >= self.max_age:
                    raise ValueError("source_gap")
                if abs(source_delta - (candidate.offset - previous.offset)) > self.clock_tolerance:
                    raise ValueError("source_clock_changed")
                if abs(candidate.at - candidate.offset - self.source_anchor) > self.clock_tolerance:
                    raise ValueError("source_clock_changed")
                expires_elapsed = elapsed + (candidate.at + self.max_age - now)
                mode = "advancing"
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
            # Never expose arbitrary labels or exception payloads in evidence.
            reason = str(exc) if type(exc) is ValueError else "malformed_observation"
        self.previous = candidate
        self.admitted = mode != "unknown"
        self.expires_elapsed = expires_elapsed
        if mode == "unknown":
            self.source_anchor = candidate.at - candidate.offset if candidate else None
        if valid_clock:
            if self.clock_anchor is None or clock_reset:
                self.clock_anchor = now - elapsed
            self.last_now, self.last_elapsed = now, elapsed
        else:
            self.last_now = self.last_elapsed = self.clock_anchor = None
        return dict(
            schema_version=1, activity=candidate.activity if self.admitted else "unknown",
            evidence=mode, reason=reason, source_id=candidate.source_id if candidate else None,
            source_at=candidate.at if candidate else None,
            sequence=candidate.sequence if candidate else None,
            samples=candidate.count if candidate else None,
            offset_s=candidate.offset if candidate else None,
            expires_at=candidate.at + self.max_age if candidate else None,
            expires_elapsed=expires_elapsed,
            policy=dict(max_age_s=self.max_age, max_poll_gap_s=self.max_poll_gap,
                        clock_tolerance_s=self.clock_tolerance),
        )
