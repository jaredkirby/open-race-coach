"""Report health from two status snapshots of one explicit capture archive."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

COVERAGE_GAPS = {
    "unavailable", "unstable", "stale", "unsupported", "low-disk", "stopped",
    "disk_reserve", "disk_probe_unavailable", "mapping_unavailable", "unstable_copy",
    "unsupported_size",
    "unsupported_version", "stale_mapping",
}
UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)"
)


@dataclass
class Sample:
    observed_at: datetime
    health: str
    reason: str | None = None
    updated_at: datetime | None = None
    age_s: float | None = None
    count: int | None = None
    offset_s: float | None = None
    continuity_generation: int | None = None


def _reject_json_constant(_value):
    raise ValueError("Nonstandard JSON numeric constant")


def _read_status(path):
    return json.loads(
        path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
    )


def _utc_now():
    return datetime.now(UTC)


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def continuity_generation(data):
    """Legacy status omits the field. A malformed present generation is unknown."""
    if "continuity_generation" not in data:
        return None
    value = data["continuity_generation"]
    if type(value) is not int or not 0 <= value < 2**53:
        raise ValueError("invalid_continuity_generation")
    return value


def _status_fields(data, now, max_age):
    if not isinstance(data, dict):
        return Sample(now, "unknown", "status_not_object")

    version = data.get("schema_version")
    if type(version) is not int:
        return Sample(now, "unknown", "invalid_schema_version")
    if version != 1:
        return Sample(now, "unknown", "unsupported_schema_version")

    state = data.get("status")
    if state == "failed":
        return Sample(now, "degraded", "capture_failed")
    if state in ("complete", "stopped"):
        return Sample(now, "degraded", "capture_stopped")
    if state != "running":
        return Sample(now, "unknown", "invalid_capture_status")

    stamp = data.get("updated_at")
    if not isinstance(stamp, str) or UTC_TIMESTAMP.fullmatch(stamp) is None:
        return Sample(now, "unknown", "invalid_updated_at")
    try:
        updated_at = datetime.fromisoformat(stamp)
    except ValueError:
        return Sample(now, "unknown", "invalid_updated_at")

    sample = Sample(now, "unknown", updated_at=updated_at,
                    age_s=(now - updated_at).total_seconds())
    try:
        sample.continuity_generation = continuity_generation(data)
    except ValueError as exc:
        sample.reason = str(exc)
        return sample
    coverage = data.get("coverage")
    count = data.get("samples")
    offset = data.get("offset_s")
    current = data.get("latest_is_current")
    target_hz = data.get("target_hz")
    if type(count) is not int or count < 0:
        sample.reason = "invalid_sample_count"
    elif not _finite_number(offset) or offset < 0:
        sample.reason = "invalid_offset"
    elif type(current) is not bool:
        sample.reason = "invalid_latest_is_current"
    elif "coverage" not in data or (coverage is not None and not isinstance(coverage, str)):
        sample.reason = "invalid_coverage"
    elif not _finite_number(target_hz) or target_hz <= 0:
        sample.reason = "invalid_target_hz"
    elif "error" not in data or data["error"] is not None:
        sample.reason = "invalid_running_error"
    elif "latest" not in data or (
        data["latest"] is not None and not isinstance(data["latest"], dict)
    ):
        sample.reason = "invalid_latest"
    elif coverage == "available" and (count == 0 or data["latest"] is None):
        sample.reason = "missing_sample_evidence"
    else:
        sample.count = count
        sample.offset_s = offset
        sample.health = "degraded"
        if sample.age_s < 0:
            sample.reason = "future_heartbeat"
        elif sample.age_s >= max_age:
            sample.reason = "stale_heartbeat"
        elif coverage in COVERAGE_GAPS:
            sample.reason = f"coverage_{coverage}"
        elif coverage is None:
            sample.reason = "coverage_pending"
        elif coverage != "available":
            sample.reason = "coverage_unrecognized"
        elif not current:
            sample.reason = "latest_not_current"
        else:
            sample.health = "healthy"
    return sample


def _take_sample(path, max_age, read, clock):
    try:
        data = read(path)
    except FileNotFoundError:
        reason = "status_missing"
    except OSError:
        reason = "status_unreadable"
    except (ValueError, RecursionError):
        reason = "status_malformed"
    else:
        return _status_fields(data, clock(), max_age)
    return Sample(clock(), "unknown", reason)


def _expiry(second, health, max_age):
    if health != "healthy":
        return second.observed_at
    latest_datetime = datetime.max.replace(tzinfo=UTC)
    if max_age >= (latest_datetime - second.updated_at).total_seconds():
        return latest_datetime
    return second.updated_at + timedelta(seconds=max_age)


def observe(run, *, interval=1.2, max_age=3.0, read=_read_status, clock=_utc_now,
            sleep=time.sleep):
    """Read and compare two status files without retaining any prior observation."""
    path = run / "status.json"
    first = _take_sample(path, max_age, read, clock)
    sleep(interval)
    second = _take_sample(path, max_age, read, clock)

    reasons = [
        {"sample": index, "code": sample.reason}
        for index, sample in enumerate((first, second), start=1)
        if sample.reason is not None
    ]
    health = "healthy"
    if "unknown" in (first.health, second.health):
        health = "unknown"
    elif reasons:
        health = "degraded"

    count_advanced = offset_advanced = None
    if first.count is not None and second.count is not None:
        count_advanced = second.count > first.count
        offset_advanced = second.offset_s > first.offset_s
        if first.continuity_generation != second.continuity_generation:
            reasons.append({"sample": 2, "code": "source_continuity_changed"})
        if second.updated_at < first.updated_at:
            reasons.append({"sample": 2, "code": "heartbeat_regressed"})
        if second.observed_at < first.observed_at:
            reasons.append({"sample": 2, "code": "observation_clock_regressed"})
        if not count_advanced:
            code = (
                "sample_count_regressed" if second.count < first.count else "sample_count_stalled"
            )
            reasons.append({"sample": 2, "code": code})
        if not offset_advanced:
            code = "offset_regressed" if second.offset_s < first.offset_s else "offset_stalled"
            reasons.append({"sample": 2, "code": code})
        if reasons and health == "healthy":
            health = "degraded"

    return {
        "health": health,
        "reasons": reasons,
        "observed_at": second.observed_at.isoformat(),
        "expires_at": _expiry(second, health, max_age).isoformat(),
        "sample_advance": {"count": count_advanced, "offset": offset_advanced},
        "freshness": {
            "max_age_s": max_age,
            "first_age_s": first.age_s,
            "second_age_s": second.age_s,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--interval", type=float, default=1.2)
    parser.add_argument("--max-age", type=float, default=3.0)
    args = parser.parse_args(argv)
    if len(args.run) != 1:
        parser.error("--run must specify exactly one archive directory")
    if not math.isfinite(args.interval) or not 0 < args.interval <= 10:
        parser.error("--interval must be finite and greater than 0, up to 10 seconds")
    if not math.isfinite(args.max_age) or args.max_age <= 0:
        parser.error("--max-age must be finite and greater than 0")
    report = observe(args.run[0], interval=args.interval, max_age=args.max_age)
    print(json.dumps(report))
    return 0 if report["health"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
