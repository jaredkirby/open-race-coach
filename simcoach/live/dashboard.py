"""Read-only, allowlisted dashboard evidence; no raw pages or operational authority."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from uuid import uuid4

from simcoach.cli.capture_health import continuity_generation
from simcoach.live.activity_evidence import ActivityEvidence
from simcoach.live.supervisor import integer, number, strict_json

MAX_SOURCE_BYTES = 256 * 1024


def _read(root, name):
    path = root / name
    if path.resolve() != path.absolute():
        raise ValueError("source_redirected")
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("source_too_large")
    return data


def read_source(archive):
    """Bounded identity/status/identity read of one explicitly selected archive."""
    root = Path(archive).absolute()
    if root.resolve() != root:
        raise ValueError("source_redirected")
    before = _read(root, "run.json")
    identity = strict_json(before.decode("utf8"))
    if (not isinstance(identity, dict) or type(identity.get("schema_version")) is not int
            or identity["schema_version"] != 1 or identity.get("expected_version") != 14
            or identity.get("struct_size_bytes") != 20700):
        raise ValueError("unsupported_source_identity")
    status = strict_json(_read(root, "status.json").decode("utf8"))
    if _read(root, "run.json") != before:
        raise ValueError("source_changed_during_read")
    return hashlib.sha256(before).hexdigest(), status


def _optional(latest, field, *, low=0, high=1e6, whole=False):
    try:
        check = integer if whole else number
        return check(latest.get(field), minimum=low, maximum=high)
    except (ValueError, TypeError):
        return None


def _label(value):
    return value if (type(value) is str and len(value) <= 256
                     and all(ord(c) >= 32 for c in value)) else None


def project(latest):
    """Copy explicit scalar display fields only. Unsupported readings stay null."""
    capacity = _optional(latest, "mFuelCapacity", low=.001, high=10000)
    fraction = _optional(latest, "mFuelLevel", high=1)
    view = latest.get("participant_observation")
    view = view if isinstance(view, dict) and view.get("schema_version") == 1 else {}
    return dict(
        context=dict(car=_label(latest.get("mCarName")), track=_label(latest.get("mTrackLocation")),
                     variation=_label(latest.get("mTrackVariation")),
                     session_state=_optional(latest, "mSessionState", high=6, whole=True)),
        view=dict(identity="viewed_car_unconfirmed",
                  index=_optional(view, "index", high=63, whole=True),
                  count=_optional(view, "count", high=64, whole=True)),
        speed_mps=_optional(latest, "mSpeed", high=200),
        rpm=_optional(latest, "mRpm", high=100000),
        gear=_optional(latest, "mGear", low=-1, high=20, whole=True),
        throttle_fraction=_optional(latest, "mThrottle", high=1),
        brake_fraction=_optional(latest, "mBrake", high=1),
        steering_fraction=_optional(latest, "mSteering", low=-1, high=1),
        fuel_litres=capacity * fraction if capacity is not None and fraction is not None else None,
        lap=_optional(latest, "mCurrentLap", whole=True),
        lap_distance_m=_optional(latest, "mCurrentLapDistance"),
        lap_invalidated=latest.get("mLapInvalidated")
            if type(latest.get("mLapInvalidated")) is bool else None,
        simulator_last_lap_s=_optional(latest, "mLastLapTime", low=.001, high=86400),
        simulator_best_lap_s=_optional(latest, "mBestLapTime", low=.001, high=86400),
        simulator_current_lap_s=_optional(latest, "mCurrentTime", high=86400),
    )


class DashboardFeed:
    """Sample independently of client requests; repeated data never renews expiry."""

    def __init__(self):
        self.publisher_id = uuid4().hex
        self.observer = ActivityEvidence()
        self.lock = threading.Lock()
        self.frame = 0
        self.source_epoch = 0
        self.source_epoch_at = None
        self.previous = None
        self.value = None
        self.observed_elapsed = None
        self.expires_elapsed = None
        self.feedback = dict(state="unavailable", reason="no_feedback_selected", card=None)
        self.operations = dict(state="not_reported", reason="no_service_selected", valid_for_ms=0)
        self.operations_deadline = 0

    def panels(self, feedback, operations, *, elapsed):
        with self.lock:
            self.feedback = feedback
            self.operations = operations
            self.operations_deadline = (elapsed + operations.get("valid_for_ms", 0) / 1000
                                        if self.value and self.value["state"] == "fresh" else 0)

    def sample(self, status, *, source_id, now, elapsed, read_error=None):
        with self.lock:
            projection = project(status["latest"]) if (
                isinstance(status, dict) and isinstance(status.get("latest"), dict)) else None
            key = (source_id, status.get("samples"), status.get("offset_s"),
                   status.get("updated_at")) if isinstance(status, dict) else None
            reset = None
            if self.previous and projection is not None:
                old_key, old = self.previous
                if key == old_key and projection != old:
                    status, reset = None, "same_sample_changed"
                elif projection["view"] != old["view"]:
                    self.observer = ActivityEvidence()
                    reset = "view_changed"
                elif (key and key[0] == old_key[0] and projection["lap"] is not None
                      and old["lap"] is not None and projection["lap"] < old["lap"]):
                    self.observer = ActivityEvidence()
                    reset = "lap_counter_reset"
            evidence = self.observer.observe(status, source_id=source_id, now=now, elapsed=elapsed)
            reason = read_error or reset or evidence["reason"]
            if reason and not re.fullmatch(r"[a-z0-9_]{1,80}", reason):
                reason = "source_unavailable"
            fresh = evidence["evidence"] in {"advancing", "held"} and not read_error and not reset
            if not fresh:
                if self.value and self.value["state"] == "fresh":
                    self.source_epoch += 1
                self.source_epoch_at = None
                self.operations_deadline = 0
            elif self.source_epoch_at is None:
                self.source_epoch_at = evidence["source_at"]
            waiting = reason in {"awaiting_advancing_observation", "source_changed",
                                 "context_changed", "source_continuity_changed",
                                 "view_changed", "lap_counter_reset"}
            self.frame += 1
            self.value = dict(schema_version=1, publisher_id=self.publisher_id,
                frame=self.frame, state="fresh" if fresh else "waiting" if waiting else "stale",
                reason=reason, source_id=evidence["source_id"], source_epoch=self.source_epoch,
                source_epoch_at=self.source_epoch_at,
                source_sequence=evidence["sequence"], source_samples=evidence["samples"],
                source_at=evidence["source_at"], expires_at=evidence["expires_at"],
                source_age_s=now - evidence["source_at"] if evidence["source_at"] else None,
                telemetry=projection if fresh else None,
                fuel_supervision="not_reported", presence="not_inferred")
            if fresh and "continuity_generation" in status:
                self.value["continuity_generation"] = continuity_generation(status)
            self.expires_elapsed = evidence["expires_elapsed"] if fresh else None
            self.observed_elapsed = elapsed
            self.previous = (key, projection) if key and projection and status is not None else None

    def response(self, nonce, *, elapsed):
        with self.lock:
            value = dict(self.value) if self.value else dict(schema_version=1,
                publisher_id=self.publisher_id, frame=0, state="waiting", reason="awaiting_source",
                source_id=None, source_epoch=self.source_epoch, source_epoch_at=None,
                source_sequence=None, source_samples=None, source_at=None,
                expires_at=None, source_age_s=None, telemetry=None,
                fuel_supervision="not_reported", presence="not_inferred")
            remaining = 0 if self.expires_elapsed is None else self.expires_elapsed - elapsed
            if (self.observed_elapsed is None or elapsed < self.observed_elapsed
                    or elapsed - self.observed_elapsed > 1):
                value.update(state="stale", reason="publisher_observation_gap", telemetry=None)
                remaining = 0
            elif value["state"] == "fresh" and remaining <= 0:
                value.update(state="stale", reason="source_evidence_expired", telemetry=None)
            if value["source_age_s"] is not None and self.observed_elapsed is not None:
                value["source_age_s"] += max(0, elapsed - self.observed_elapsed)
            value.update(request_id=nonce, valid_for_ms=max(0, min(2000, int(remaining * 1000))))
            operations = dict(self.operations)
            if operations["state"] in {"armed", "return_due", "unarmed", "suspended"}:
                left = min(self.operations_deadline - elapsed, remaining)
                if left <= 0 or value["state"] != "fresh":
                    operations = dict(state="unknown", reason="operations_display_expired",
                                      valid_for_ms=0)
                else:
                    operations["valid_for_ms"] = max(0, int(left * 1000))
            value.update(feedback=self.feedback, operations=operations,
                         fuel_supervision=operations["state"])
            # Deep copy so a caller cannot mutate the next response's nested data.
            return json.loads(json.dumps(value, allow_nan=False))
