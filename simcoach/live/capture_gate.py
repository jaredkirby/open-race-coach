"""Nonblocking source admission for capture; fuel approval is independent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from simcoach.cli.capture_health import _status_fields
from simcoach.live.supervisor import Context, integer, number

MAX_CAPTURE_AGE_S = 3.0


def recorder_deadline(status, *, now, process_alive):
    if not process_alive or not isinstance(status, dict):
        return None
    if (type(status.get("schema_version")) is not int or status["schema_version"] != 1
            or status.get("status") != "recording" or status.get("intake_active") is not True
            or type(status.get("active_ticks")) is not int or status["active_ticks"] <= 0):
        return None
    try:
        at = number(status["updated_at"])
        age = number(status["tick_age_s"])
        expires = at + (MAX_CAPTURE_AGE_S - age)
        return expires if at <= number(now) < expires else None
    except (KeyError, ValueError):
        return None


def capture_deadline_current(at, expires, *, now):
    try:
        at = number(at)
        return at <= number(now) < number(expires) <= at + MAX_CAPTURE_AGE_S
    except ValueError:
        return False


def published_capture_ready(status, *, now):
    """Check the producer's deadline after reading a host status snapshot."""
    return (isinstance(status, dict) and status.get("capture_ready") is True
            and capture_deadline_current(status.get("updated_at"),
                                         status.get("capture_expires_at"), now=now))


@dataclass(frozen=True)
class _Sample:
    source_id: str
    at: float
    count: int
    offset: float
    sequence: int
    context: Context
    continuity_generation: int | None


class CaptureGate:
    """Require two fresh advancing observations before starting capture.

    The host supplies a verified archive identity and monotonic elapsed time.
    This class starts no process, owns no files, and never approves driving or fuel.
    """

    def __init__(self, expected_context: Context, *, max_age: float = MAX_CAPTURE_AGE_S):
        if not isinstance(expected_context, Context):
            raise ValueError("invalid_expected_context")
        self.expected_context = expected_context
        self.max_age = number(max_age, minimum=0.001, maximum=MAX_CAPTURE_AGE_S)
        self.previous: _Sample | None = None
        self.last_now: float | None = None
        self.last_elapsed: float | None = None
        self.clock_anchor: float | None = None
        self.source_clock_anchor: float | None = None
        self.ready = False

    def _read(self, status, source_id, now):
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("missing_source_identity")
        sample = _status_fields(status, datetime.fromtimestamp(now, UTC), self.max_age)
        if sample.health != "healthy":
            raise ValueError(sample.reason)
        latest = status["latest"]
        if type(latest.get("mVersion")) is not int or latest["mVersion"] != 14:
            raise ValueError("unsupported_layout")
        sequence = integer(latest.get("mSequenceNumber"))
        if sequence % 2:
            raise ValueError("unstable_sequence")
        context = Context(latest.get("mCarName"), latest.get("mTrackLocation"),
                          latest.get("mTrackVariation"), latest.get("mSessionState"))
        # Preserve supported non-driving states too. Fuel-plan matching and
        # race-active checks govern supervision, not whether evidence is kept.
        if (type(latest.get("mGameState")) is not int
                or latest["mGameState"] not in {1, 2, 3, 4, 6, 7}):
            raise ValueError("unsupported_game_state")
        return _Sample(source_id, sample.updated_at.timestamp(), sample.count,
                       sample.offset_s, sequence, context, sample.continuity_generation)

    def observe(self, status, *, source_id, now, elapsed):
        """Return transient readiness; expiry is based on source time, never poll time."""
        candidate = None
        reason = None
        valid_clock = False
        clock_reset = False
        previous = self.previous
        try:
            now = number(now)
            elapsed = number(elapsed)
            # Also bound conversion on platforms whose datetime range is narrower.
            datetime.fromtimestamp(now, UTC)
            valid_clock = True
            candidate = self._read(status, source_id, now)
            if self.last_now is not None:
                if now < self.last_now or elapsed < self.last_elapsed:
                    clock_reset = True
                    raise ValueError("observation_clock_regressed")
                if elapsed - self.last_elapsed >= self.max_age:
                    clock_reset = True
                    raise ValueError("observation_gap")
                if (abs((now - self.last_now) - (elapsed - self.last_elapsed)) > 0.5
                        or abs(now - elapsed - self.clock_anchor) > 0.5):
                    clock_reset = True
                    raise ValueError("observation_clock_changed")
            if previous is None:
                raise ValueError("awaiting_advancing_observation")
            if candidate.source_id != previous.source_id:
                raise ValueError("source_changed")
            if candidate.continuity_generation != previous.continuity_generation:
                raise ValueError("source_continuity_changed")
            if candidate.context != previous.context:
                raise ValueError("context_changed")
            if candidate == previous:
                if not self.ready:
                    raise ValueError("awaiting_advancing_observation")
            else:
                if candidate.at <= previous.at or candidate.offset <= previous.offset:
                    raise ValueError("source_clock_not_advancing")
                if candidate.count <= previous.count:
                    raise ValueError("sample_count_not_advancing")
                if candidate.sequence <= previous.sequence:
                    raise ValueError("sequence_not_advancing")
                if candidate.at - previous.at >= self.max_age:
                    raise ValueError("source_gap")
                if (self.source_clock_anchor is None
                        or abs(candidate.at - candidate.offset - self.source_clock_anchor) > 0.5
                        or abs((candidate.at - previous.at)
                               - (candidate.offset - previous.offset)) > 0.5):
                    raise ValueError("source_clock_changed")
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
            reason = str(exc) if type(exc) is ValueError else "malformed_observation"
        # A valid candidate at a reset is only a new baseline, not admission.
        # Invalid source data erases the baseline; malformed clock data erases both.
        self.previous = candidate
        self.last_now = now if valid_clock else None
        self.last_elapsed = elapsed if valid_clock else None
        if not valid_clock:
            self.clock_anchor = None
        elif self.clock_anchor is None or clock_reset:
            self.clock_anchor = now - elapsed
        if candidate is None:
            self.source_clock_anchor = None
        elif (previous is None or candidate.source_id != previous.source_id
              or reason in {"source_clock_not_advancing", "source_clock_changed",
                            "source_continuity_changed"}):
            # A replacement baseline cannot admit this observation. Capture may
            # reacquire a coherent pair; fuel approval remains a separate gate.
            self.source_clock_anchor = candidate.at - candidate.offset
        self.ready = reason is None
        return dict(health="healthy" if self.ready else "degraded", reason=reason,
                    ready=self.ready, source_id=candidate.source_id if candidate else None,
                    expires_at=candidate.at + self.max_age if self.ready else None,
                    observed_context=asdict(candidate.context) if candidate else None,
                    expected_context_match=(candidate.context == self.expected_context
                                            if candidate else None))
