"""Best-effort worker observations, separate from canonical capture writes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4


class WorkerStatusPublisher:
    """Keep status I/O errors from aborting recording or canonical closure.

    Each call publishes a new observation, never refreshes an old timestamp, and
    retains failed temporaries. A failed terminal publication must return failure
    to the worker. Existing reader freshness rules remain authoritative.
    """

    def __init__(self, run: Path, writer):
        self.run, self.writer = Path(run), writer
        self.failures = 0
        self.last_failure = None

    def publish(self, value):
        temporary = self.run / f"worker-status-{uuid4().hex}.tmp"
        value = dict(value, publication_diagnostic=dict(
            failures=self.failures, last_failure=self.last_failure))
        try:
            self.writer(self.run / "status.json", value, temporary=temporary)
        except OSError as exc:
            self.failures += 1
            self.last_failure = dict(
                at=time.time(), error_type=type(exc).__name__, errno=exc.errno,
                winerror=getattr(exc, "winerror", None), temporary=temporary.name,
                observation_at=value["updated_at"], status=value["status"],
            )
            try:
                with (self.run / "status-publication-errors.jsonl").open(
                    "a", encoding="utf-8"
                ) as handle:
                    handle.write(json.dumps(self.last_failure, allow_nan=False) + "\n")
            except OSError:
                # Keep failure in memory for the next successful observation.
                pass
            return False
        return True
