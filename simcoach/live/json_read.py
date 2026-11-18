"""Bounded retries for transient failures opening atomically published files."""

from __future__ import annotations

import errno
import time


def read_with_retry(operation, *, role, diagnostic=None):
    """Retry only sharing/access errors, never stale content or invalid JSON.

    Three attempts, two 10 ms waits, and a 50 ms monotonic retry budget. OS I/O
    itself is not interruptible. Every attempt redoes the supplied transaction.
    No cached bytes or producer timestamps are retained or refreshed.
    """
    started = time.perf_counter()
    failures = []

    def report(recovered):
        if diagnostic is not None and failures:
            diagnostic(dict(schema_version=1, event="file_read", role=role,
                            at=time.time(), attempts=attempt, recovered=recovered,
                            failures=list(failures)))

    for attempt in range(1, 4):
        try:
            value = operation()
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            failures.append(dict(attempt=attempt, error_type=type(exc).__name__,
                                 errno=exc.errno, winerror=winerror))
            transient = winerror in (5, 32) or (winerror is None and exc.errno == errno.EACCES)
            if not transient or attempt == 3 or time.perf_counter() - started >= 0.04:
                report(False)
                raise
            time.sleep(0.01)
            if time.perf_counter() - started >= 0.05:
                report(False)
                raise
        except (ValueError, TypeError, KeyError):
            # A prior read error must remain visible even when the next read is
            # successful but has invalid content. It is never retried again.
            report(False)
            raise
        else:
            report(True)
            return value
