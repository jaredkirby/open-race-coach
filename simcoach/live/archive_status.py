"""One latest-observation status publisher, isolated from raw archive writes."""

from __future__ import annotations

import copy
import threading
import time
from datetime import UTC, datetime
from pathlib import Path


class ArchiveStatusPublisher:
    """Only this thread writes status; it never owns raw samples or event files.

    The mailbox holds at most one pending snapshot plus the in-flight snapshot.
    Observation timestamps never change on retry. A blocked OS write cannot be
    cancelled; the daemon and bounded close keep it off the raw capture path.
    """

    def __init__(self, path: Path, *, writer=None, retry_min_s=1.0, retry_max_s=30.0):
        from simcoach.cli.telemetry_watch import write_status

        if not 0 < retry_min_s <= retry_max_s <= 30:
            raise ValueError("invalid_status_retry_interval")
        self.path = Path(path)
        self.writer = writer or write_status
        self.retry_min_s, self.retry_max_s = retry_min_s, retry_max_s
        self.condition = threading.Condition()
        self.pending = None
        self.generation = self.confirmed = self.attempts = self.failures = 0
        self.consecutive_failures = self.superseded = 0
        self.last_failure = None
        self.closing = self.finished = self.fatal = False
        self.thread = threading.Thread(target=self._work, name="archive-status", daemon=True)
        self.thread.start()

    def submit(self, value):
        # Detach before taking the mailbox lock. No filesystem operations or raw
        # state access occur while either side owns the lock.
        snapshot = copy.deepcopy(value)
        with self.condition:
            if self.closing:
                raise RuntimeError("status_publisher_closing")
            self.generation += 1
            if self.pending is not None:
                self.superseded += 1
            self.pending = (self.generation, snapshot)
            self.condition.notify_all()
            return self.generation

    def _snapshot(self):
        return dict(schema_version=1, submitted_sequence=self.generation,
                    confirmed_sequence=self.confirmed, attempts=self.attempts,
                    failures=self.failures, consecutive_failures=self.consecutive_failures,
                    superseded_snapshots=self.superseded,
                    last_failure=copy.deepcopy(self.last_failure),
                    closing=self.closing, finished=self.finished, fatal=self.fatal)

    def snapshot(self):
        with self.condition:
            return self._snapshot()

    def _work(self):
        retry_at = 0.0
        delay = self.retry_min_s
        try:
            while True:
                with self.condition:
                    while True:
                        if self.closing and self.pending is None:
                            return
                        remaining = retry_at - time.perf_counter()
                        if self.pending is not None and (self.closing or remaining <= 0):
                            break
                        self.condition.wait(remaining if self.pending is not None else None)
                    generation, value = self.pending
                    self.pending = None
                    self.attempts += 1
                    attempt = self.attempts
                    # This describes state before the write; only the outer
                    # observation_sequence identifies the candidate being written.
                    diagnostic = self._snapshot()
                value = dict(value, status_publication=dict(
                    observation_sequence=generation, prior_state=diagnostic))
                temporary = self.path.parent / f"status-publication-{attempt:08d}.tmp"
                try:
                    self.writer(self.path, value, temporary=temporary)
                except Exception as exc:
                    with self.condition:
                        self.failures += 1
                        self.consecutive_failures += 1
                        self.last_failure = dict(
                            attempt=attempt, observation_sequence=generation,
                            at=datetime.now(UTC).isoformat(), error_type=type(exc).__name__,
                            errno=getattr(exc, "errno", None),
                            winerror=getattr(exc, "winerror", None),
                            temporary=temporary.name,
                        )
                        if not isinstance(exc, OSError):
                            self.fatal = True
                            return
                        if self.closing and self.pending is None:
                            return  # Terminal attempt failed; never acknowledge an older write.
                        if self.pending is None:
                            # The original observation remains stale on retries.
                            self.pending = (generation, value)
                    retry_at = time.perf_counter() + delay
                    delay = min(self.retry_max_s, delay * 2)
                else:
                    with self.condition:
                        self.confirmed = generation
                        self.consecutive_failures = 0
                    retry_at = 0.0
                    delay = self.retry_min_s
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    def close(self, *, timeout_s=2.0):
        """Seal submissions and wait boundedly for the final submitted generation."""
        if not 0 <= timeout_s <= 5:
            raise ValueError("invalid_status_close_timeout")
        with self.condition:
            self.closing = True
            self.condition.notify_all()
        self.thread.join(timeout_s)
        with self.condition:
            return (self.finished and not self.fatal and self.generation > 0
                    and self.confirmed == self.generation)
