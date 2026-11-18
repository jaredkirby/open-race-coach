"""Explicit Driver Visit boundaries and prospective reasons; no occupancy inference."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from simcoach.cli.telemetry_watch import write_status

ACTIVITIES = ("driving", "pit_moving", "not_driving", "transition", "unknown")
REASONS = ("intentional_rest", "setup", "assistant_wait", "other_wait", "unspecified")
REASON_POLICY = "until_driving"


def _stamp(at, elapsed):
    if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
        raise ValueError("visit_requires_utc")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("invalid_elapsed")
    return at.isoformat(), float(elapsed)


class VisitClock:
    """One new, explicitly present visit. Never reopen or resume a prior directory.

    The caller supplies UTC and time.perf_counter() observations. Reason changes
    apply only after their annotation timestamp. Activity must come from a fresh
    observation or an explicitly bounded, labeled held estimate. Missing evidence
    is unknown; a running simulator does not establish physical presence.
    """

    def __init__(self, run: Path, *, presence_confirmed, at: datetime, elapsed: float,
                 max_gap_s=1.0):
        stamp, elapsed = _stamp(at, elapsed)
        if presence_confirmed is not True:
            raise ValueError("explicit_presence_required")
        if (type(max_gap_s) not in (int, float) or not math.isfinite(max_gap_s)
                or not 0 < max_gap_s <= 3):
            raise ValueError("invalid_max_gap")
        self.run = Path(run)
        self.run.mkdir(parents=True, exist_ok=False)
        self.visit_id = uuid4().hex
        self.started_at = stamp
        self.at, self.elapsed = at, elapsed
        self.start_elapsed = elapsed
        self.max_gap_s = max_gap_s
        self.activity = "unknown"
        self.reason = "unspecified"
        self.status = "running"
        self.terminal_intent = None
        self.publication_complete = False
        self.publication_error = None
        self.activity_s = dict.fromkeys(ACTIVITIES, 0.0)
        self.not_driving_reason_s = dict.fromkeys(REASONS, 0.0)
        self.sequence = 0
        self.connection = sqlite3.connect(self.run / "events.sqlite3")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        self._append([dict(event="started", at=stamp, offset_s=0, presence_confirmed=True,
                           reason_policy=REASON_POLICY)])
        self._checkpoint()

    def _append(self, events):
        with self.connection:
            for event in events:
                payload = dict(schema_version=1, visit_id=self.visit_id, **event)
                self.connection.execute("INSERT INTO events(payload) VALUES (?)",
                                        (json.dumps(payload, allow_nan=False),))
        self.sequence += len(events)

    def snapshot(self):
        """Return a detached summary of recorded time; never extrapolate to now."""
        return dict(schema_version=1, visit_id=self.visit_id, status=self.status,
                    presence="declared_present" if self.status == "running" else
                    "declared_absent" if self.terminal_intent == "complete" else "unknown",
                    started_at=self.started_at, updated_at=self.at.isoformat(),
                    elapsed_s=self.elapsed-self.start_elapsed,
                    activity_s=dict(self.activity_s),
                    not_driving_reason_s=dict(self.not_driving_reason_s),
                    current_reason=self.reason, reason_policy=REASON_POLICY,
                    journal_sequence=self.sequence,
                    terminal_intent=self.terminal_intent,
                    publication_complete=self.publication_complete,
                    publication_error=self.publication_error)

    def _checkpoint(self):
        write_status(self.run / "status.json", self.snapshot())

    def _advance(self, activity, *, at, elapsed, annotation=None, terminal=None,
                 source_evidence=None):
        if self.status != "running":
            raise RuntimeError("visit_not_running")
        stamp, elapsed = _stamp(at, elapsed)
        if activity not in ACTIVITIES:
            raise ValueError("invalid_activity")
        if annotation is not None and annotation not in REASONS:
            raise ValueError("invalid_reason")
        if source_evidence is not None:
            allowed = {"schema_version", "activity", "evidence", "reason", "source_id",
                       "source_at", "sequence", "samples", "offset_s", "expires_at",
                       "expires_elapsed", "policy"}
            if (not isinstance(source_evidence, dict) or set(source_evidence) != allowed
                    or type(source_evidence.get("schema_version")) is not int
                    or source_evidence["schema_version"] != 1
                    or source_evidence.get("activity") != activity
                    or source_evidence.get("evidence") not in ("advancing", "held", "unknown")
                    or (source_evidence["evidence"] == "unknown") != (activity == "unknown")):
                raise ValueError("invalid_source_evidence")
            # Snapshot the diagnostic before appending. This is not a fresh source read.
            source_evidence = json.loads(json.dumps(source_evidence, allow_nan=False))
        seconds = elapsed - self.elapsed
        if seconds < 0:
            raise ValueError("elapsed_regressed_start_new_visit")
        clock_delta = (at-self.at).total_seconds()
        gap = seconds > self.max_gap_s
        clock_changed = clock_delta < 0 or abs(clock_delta-seconds) > 0.5
        bucket = "unknown" if gap or clock_changed or "unknown" in (self.activity, activity) else (
            activity if activity == self.activity else "transition")
        interval_reason = self.reason if bucket == "not_driving" else None
        events = [dict(event="interval", at=stamp, start_at=self.at.isoformat(),
                       start_offset_s=self.elapsed-self.start_elapsed,
                       end_offset_s=elapsed-self.start_elapsed, seconds=seconds,
                       activity=bucket, reason=interval_reason,
                       gap=gap, clock_changed=clock_changed, source_evidence=source_evidence)]
        next_reason = annotation
        reason_cause = "declared"
        # End the previous stop's declaration only after a positive, supported
        # driving interval. A single endpoint, pit automation or missing evidence
        # cannot establish that boundary. Never rewrite the interval just closed.
        if (annotation is None and bucket == "driving" and seconds > 0
                and self.reason != "unspecified"):
            next_reason = "unspecified"
            reason_cause = "driving_resumed"
        if next_reason is not None:
            events.append(dict(event="reason_changed", at=stamp,
                               offset_s=elapsed-self.start_elapsed, reason=next_reason,
                               cause=reason_cause))
        if terminal:
            events.append(dict(event="ended" if terminal == "complete" else "interrupted",
                               at=stamp, offset_s=elapsed-self.start_elapsed,
                               presence="declared_absent" if terminal == "complete" else "unknown"))
        self._append(events)
        self.activity_s[bucket] += seconds
        if interval_reason is not None:
            self.not_driving_reason_s[interval_reason] += seconds
        self.at, self.elapsed, self.activity = at, elapsed, activity
        if next_reason is not None:
            self.reason = next_reason
        if terminal:
            self.terminal_intent = terminal
            self.status = "finalizing"
            self._finish_summary(terminal)
        else:
            self._checkpoint()
        return self.snapshot()

    def update(self, activity, *, at: datetime, elapsed: float, source_evidence=None):
        return self._advance(activity, at=at, elapsed=elapsed, source_evidence=source_evidence)

    def annotate(self, reason, *, at: datetime, elapsed: float, activity="unknown",
                 source_evidence=None):
        """Apply a declared reason prospectively; activity needs fresh evidence."""
        return self._advance(activity, at=at, elapsed=elapsed, annotation=reason,
                             source_evidence=source_evidence)

    def end(self, *, at: datetime, elapsed: float):
        """Explicit departure. Unobserved tail is unknown; later time is excluded."""
        return self._advance("unknown", at=at, elapsed=elapsed, terminal="complete")

    def _finish_summary(self, terminal):
        # A fresh directory and single writer make this a once-only publication.
        try:
            if (self.run / "summary.json").exists():
                raise FileExistsError("visit_summary_already_exists")
            candidate = dict(self.snapshot(), status=terminal, publication_complete=True)
            write_status(self.run / "summary.json", candidate)
            write_status(self.run / "status.json", candidate)
        except Exception as exc:
            # A committed end is not proof of publication. Do not retry it from
            # close(), append another end, or silently promote this state later.
            self.status = "publication_failed"
            self.publication_error = type(exc).__name__
            raise
        else:
            self.status = terminal
            self.publication_complete = True
        finally:
            self.connection.close()

    def close(self):
        """Interrupted observer: retain only last measured interval, no absence claim."""
        if self.status == "running":
            return self._advance("unknown", at=self.at, elapsed=self.elapsed,
                                 terminal="interrupted")
        self.connection.close()
        return self.snapshot()
