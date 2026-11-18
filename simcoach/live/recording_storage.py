"""Local recorder free-space observations with a fixed admission deadline."""

from __future__ import annotations

import shutil
import time

from simcoach.live.supervisor import integer, number

MIN_FREE_BYTES = 1024 ** 3
OBSERVATION_INTERVAL_S = 1
STORAGE_MAX_AGE_S = 3


def _age(sample, *, now, elapsed):
    wall = number(now) - number(sample["at"])
    mono = number(elapsed) - number(sample["elapsed"])
    if wall < 0 or mono < 0 or abs(wall - mono) > .5:
        raise ValueError("storage_clock_discontinuous")
    return max(wall, mono)


def storage_deadline(sample, *, now, elapsed):
    """Validate the producer's original deadline after the consumer's reads."""
    if (not isinstance(sample, dict) or type(sample.get("schema_version")) is not int
            or sample["schema_version"] != 1 or sample.get("state") != "available"):
        return None
    try:
        integer(sample["free_bytes"], minimum=MIN_FREE_BYTES, maximum=2**64-1)
        deadline = number(sample["at"]) + STORAGE_MAX_AGE_S
        age = _age(sample, now=now, elapsed=elapsed)
        if sample["expires_at"] == deadline and age < STORAGE_MAX_AGE_S:
            return min(deadline, now + (STORAGE_MAX_AGE_S - age))
    except (KeyError, ValueError):
        pass
    return None


def free_bytes(path):
    return shutil.disk_usage(path).free


class RecordingStorage:
    """Rate-limit volume queries and keep failures separate from a measured low reserve."""

    def __init__(self, *, clock=time.time, elapsed=time.perf_counter):
        self.clock = clock
        self.elapsed = elapsed
        self.last = None

    def poll(self, path):
        at, elapsed = self.clock(), self.elapsed()
        if self.last and self.last["path"] == str(path):
            try:
                if _age(self.last, now=at, elapsed=elapsed) < OBSERVATION_INTERVAL_S:
                    return self.last
            except ValueError:
                pass
        sample = dict(schema_version=1, at=at, elapsed=elapsed, path=str(path),
                      minimum_free_bytes=MIN_FREE_BYTES, state="unknown", free_bytes=None,
                      expires_at=None, reason=None)
        try:
            remaining = integer(free_bytes(path), maximum=2**64-1)
            if _age(sample, now=self.clock(), elapsed=self.elapsed()) >= STORAGE_MAX_AGE_S:
                raise ValueError("storage_measurement_expired")
        except OSError as exc:
            sample.update(reason="storage_probe_failed", error_type=type(exc).__name__)
        except ValueError:
            sample["reason"] = "storage_observation_invalid_or_expired"
        else:
            available = remaining >= MIN_FREE_BYTES
            sample.update(state="available" if available else "low", free_bytes=remaining,
                          reason=None if available else "disk_reserve",
                          expires_at=at + STORAGE_MAX_AGE_S)
        self.last = sample
        return sample
