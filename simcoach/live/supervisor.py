"""Evidence-gated local fuel decisions. No model calls, simulator writes or audio."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from simcoach.cli.capture_health import _status_fields, continuity_generation


def number(value, *, minimum=0, maximum=math.inf):
    if type(value) not in (int, float):
        raise ValueError("invalid_number")
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("invalid_number")
    return float(value)


def integer(value, *, minimum=0, maximum=2**32 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_integer")
    return value


def strict_json(text):
    def reject(_):
        raise ValueError("invalid_json_constant")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(text, parse_constant=reject, object_pairs_hook=unique_pairs)


@dataclass(frozen=True)
class Context:
    car: str
    track: str
    variation: str
    session_state: int

    def __post_init__(self):
        for label in (self.car, self.track, self.variation):
            if not isinstance(label, str) or len(label) > 256 or "\0" in label:
                raise ValueError("invalid_context_label")
        if not self.car.strip() or not self.track.strip():
            raise ValueError("missing_context_label")
        integer(self.session_state, minimum=1, maximum=6)


@dataclass(frozen=True)
class FuelPlan:
    context: Context
    litres_per_lap: float
    timed_laps: int
    reserve_laps: int
    return_threshold_litres: float
    estimate_source: str
    out_laps: int = 1
    in_laps: int = 1

    def __post_init__(self):
        if not isinstance(self.context, Context):
            raise ValueError("invalid_plan_context")
        number(self.litres_per_lap, minimum=0.001, maximum=1000)
        integer(self.timed_laps, minimum=1, maximum=10000)
        integer(self.reserve_laps, minimum=2, maximum=10000)
        integer(self.out_laps, maximum=100)
        integer(self.in_laps, maximum=100)
        number(self.return_threshold_litres, minimum=self.return_budget, maximum=10000)
        if not isinstance(self.estimate_source, str) or not self.estimate_source.strip():
            raise ValueError("missing_estimate_source")

    @property
    def required_load(self):
        return (self.out_laps + self.timed_laps + self.in_laps
                + self.reserve_laps) * self.litres_per_lap

    @property
    def return_budget(self):
        return (1 + self.reserve_laps) * self.litres_per_lap

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
            raise ValueError("invalid_plan")
        if value["schema_version"] != 1:
            raise ValueError("unsupported_plan_version")
        fields = dict(value)
        del fields["schema_version"]
        fields["context"] = Context(**fields["context"])
        return cls(**fields)


class Journal:
    """Fresh, private SQLite journal; never silently reopen a previous run."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE metadata (schema_version INTEGER NOT NULL)")
        self.connection.execute("INSERT INTO metadata VALUES (1)")
        self.connection.execute(
            "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.connection.commit()

    def append(self, event):
        payload = json.dumps(dict(schema_version=1, **event), allow_nan=False, sort_keys=True)
        with self.connection:
            self.connection.execute("INSERT INTO events(payload) VALUES (?)", (payload,))

    def close(self):
        self.connection.close()


def source_identity(archive: Path):
    raw = (archive / "run.json").read_bytes()
    run = strict_json(raw.decode("utf-8"))
    if not isinstance(run, dict) or type(run.get("schema_version")) is not int:
        raise ValueError("invalid_archive_identity")
    if run["schema_version"] != 1 or run.get("expected_version") != 14:
        raise ValueError("unsupported_archive_identity")
    if run.get("struct_size_bytes") != 20700:
        raise ValueError("unsupported_archive_layout")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ParticipantView:
    """A checked viewed slot, not proof of local-player identity."""
    index: int
    count: int
    car: str


def participant_view(latest):
    if "participant_observation" not in latest:
        return None  # Legacy solo-context evidence; never upgraded to a player identity.
    view = latest["participant_observation"]
    if (not isinstance(view, dict) or type(view.get("schema_version")) is not int
            or view["schema_version"] != 1 or view.get("active") is not True):
        raise ValueError("participant_evidence_invalid")
    count = integer(view.get("count"), minimum=1, maximum=64)
    index = integer(view.get("index"), maximum=count - 1)
    if (view.get("car") != latest.get("mCarName")
            or type(view.get("race_state")) is not int
            or view["race_state"] != latest.get("mRaceState")
            or type(view.get("pit_mode")) is not int
            or view["pit_mode"] != latest.get("mPitMode")):
        raise ValueError("viewed_fields_disagree")
    return ParticipantView(index, count, view["car"])


@dataclass(frozen=True)
class Reading:
    source_id: str
    at: float
    count: int
    offset: float
    sequence: int
    context: Context
    litres: float
    capacity: float
    lap: int
    game: int
    pit: int
    race: int
    participant: ParticipantView | None = None
    speed: float | None = None
    continuity_generation: int | None = None

    @property
    def solo_practice_exit(self):
        return (self.context.session_state == 1 and self.game == 2 and self.race == 1
                and self.pit == 5 and self.participant is not None
                and self.participant.index == 0 and self.participant.count == 1)

    @property
    def prestart(self):
        grid = (self.context.session_state == 5 and self.participant
                and self.race == 1 and self.game in {2, 4} and self.pit == 0)
        if self.speed is None:
            return False
        return bool(grid and self.speed <= 1 or self.solo_practice_exit and self.speed <= 0.1)

    @property
    def evidence_id(self):
        evidence = asdict(self)
        if self.continuity_generation is None:
            del evidence["continuity_generation"]
        content = json.dumps(evidence, sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(content).hexdigest()


def reading(status, source_id, now, max_age):
    sample = _status_fields(status, datetime.fromtimestamp(now, UTC), max_age)
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
    capacity = number(latest.get("mFuelCapacity"), minimum=0.001, maximum=10000)
    fraction = number(latest.get("mFuelLevel"), maximum=1)
    return Reading(
        source_id, sample.updated_at.timestamp(), sample.count, sample.offset_s,
        sequence, context, fraction * capacity, capacity,
        integer(latest.get("mCurrentLap")), integer(latest.get("mGameState"), maximum=7),
        integer(latest.get("mPitMode"), maximum=5), integer(latest.get("mRaceState"), maximum=6),
        participant_view(latest),
        number(latest["mSpeed"]) if "mSpeed" in latest else None,
        sample.continuity_generation,
    )


class Supervisor:
    """Fresh approval per process; only bounded copy contention may recover."""

    def __init__(self, plan: FuelPlan, journal: Journal, *, max_age=3.0):
        self.plan = plan
        self.plan_revision = 0
        self.journal = journal
        self.max_age = number(max_age, minimum=0.1, maximum=3)
        self.previous = None
        self.current = None
        self.last_now = None
        self.last_elapsed = None
        self.clock_anchor = None
        self.source_clock_anchor = None
        self.health = "unknown"
        self.reason = "awaiting_observations"
        self.arm_id = None
        self.minimum_fuel = None
        self.return_notice = None
        self.last_result = None
        self.approval_floor = None
        self.approval_result = None
        self.suspension = None
        self.recovery_samples = 0
        self.supervision_loss = None
        self.preapproval_anchor = None
        self.preapproval_advanced = False
        self.healthy_since = None

    def _event(self, event_type, now, **values):
        # Commit before exposing a decision. Storage failure propagates, never silently passes.
        self.journal.append(dict(event=event_type, at=now, **values))

    def plan_status(self):
        value = dict(revision=self.plan_revision, plan=asdict(self.plan))
        encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        return dict(plan_id=hashlib.sha256(encoded).hexdigest(), **value,
                    required_load_litres=self.plan.required_load)

    def change_plan(self, *, plan, expected_plan_id, requested_at, now, note):
        """Explicit compare-and-replace; never carries fuel approval into a new plan."""
        before = self.plan_status()
        try:
            now, requested_at = number(now), number(requested_at)
            if not 0 <= now - requested_at < 3:
                raise ValueError("stale_plan_request")
            if self.last_now is not None and now < self.last_now:
                raise ValueError("plan_clock_regressed")
            if expected_plan_id != before["plan_id"]:
                raise ValueError("plan_binding_changed")
            if not isinstance(plan, FuelPlan):
                raise ValueError("invalid_plan")
            if plan == self.plan:
                raise ValueError("plan_unchanged")
            if not isinstance(note, str) or not note.strip() or len(note) > 2048:
                raise ValueError("invalid_plan_note")
        except (ValueError, TypeError) as exc:
            reason = str(exc) if type(exc) is ValueError else "invalid_plan_request"
            self._event("plan_change_rejected", now, reason=reason)
            return dict(accepted=False, reason=reason, plan_id=before["plan_id"])
        # Commit intent before effects. A storage failure propagates; the owner
        # must stop instead of continuing under an uncertain operational state.
        self._event("plan_change", now, before=before, plan=asdict(plan),
                    revision=self.plan_revision + 1, requested_at=requested_at, note=note)
        self._disarm("plan_changed", now)
        self.plan = plan
        self.plan_revision += 1
        self.approval_floor = max(now, self.approval_floor or now)
        self.previous = self.current = None
        self.source_clock_anchor = None
        self.health, self.reason = "degraded", "awaiting_plan_observations"
        self.approval_result = dict(accepted=False, reason="plan_changed", at=now)
        self.last_result = dict(
            self.last_result or {}, at=now, supervision_health=self.health, reason=self.reason,
            fuel_state="unarmed", fuel_litres=None, healthy_since=None, expires_at=now,
            evidence_id=None,
            notice=None, approval=self.approval_result, recovery=None,
            supervision_loss=self.supervision_loss, participant=None, phase="unknown",
            **self.plan_status())
        return dict(accepted=True, reason=None, plan_id=self.plan_status()["plan_id"],
                    fuel_approval_required=True)

    def _disarm(self, reason, now):
        self.healthy_since = None
        self.preapproval_anchor = None
        self.preapproval_advanced = False
        if self.arm_id is not None:
            self._event("fuel_disarmed", now, arm_id=self.arm_id, reason=reason)
            self.approval_result = dict(accepted=False, reason=reason, at=now)
            self._loss(reason, now, resolution="requires_approval")
        self.arm_id = self.minimum_fuel = self.return_notice = None
        self.suspension = None
        self.recovery_samples = 0

    def _loss(self, reason, now, *, resolution):
        loss = (dict(self.supervision_loss) if self.suspension and self.supervision_loss
                else dict(id=uuid4().hex, started_at=now, arm_id=self.arm_id,
                          initial_reason=reason))
        loss.update(reason=reason, resolution=resolution, updated_at=now)
        self._event("supervision_loss", now, **loss)
        self.supervision_loss = loss

    @property
    def fuel_state(self):
        if self.suspension:
            return "suspended"
        return "return_due" if self.return_notice else "armed" if self.arm_id else "unarmed"

    def _suspend(self, now, elapsed):
        # The last source timestamp bounds the episode even if publication was
        # already old. Repeated bad reads cannot extend either deadline.
        deadline = min(now + 3, self.previous.at + 5)
        state = dict(started_at=now, expires_at=deadline,
                     expires_elapsed=elapsed + deadline - now)
        self.current = None
        self.health, self.reason = "degraded", "coverage_unstable_copy"
        try:
            self._event("fuel_suspended", now, arm_id=self.arm_id, **state)
            self._loss("coverage_unstable_copy", now, resolution="recovering")
        except Exception:
            # Partial journal evidence cannot retain operational authorization.
            # Propagate the failure; do not silently treat storage as recovered.
            self.arm_id = self.minimum_fuel = self.return_notice = None
            self.approval_result = dict(accepted=False, reason="suspension_journal_failed", at=now)
            raise
        self.suspension = state
        self.recovery_samples = 0

    def source_clock_matches(self, candidate, prior):
        """Read-only clock continuity check, also used immediately before radio output."""
        return bool(
            self.source_clock_anchor is not None
            and abs(candidate.at - candidate.offset - self.source_clock_anchor) <= .5
            and abs((candidate.at - prior.at) - (candidate.offset - prior.offset)) <= .5
        )

    def observe(self, status, *, source_id, now, elapsed):
        number(now)
        number(elapsed)
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("missing_source_identity")
        reason = None
        candidate = None
        prior = self.previous
        clock_reset = False
        try:
            if self.last_now is not None:
                if now < self.last_now or elapsed < self.last_elapsed:
                    clock_reset = True
                    raise ValueError("observation_clock_regressed")
                if elapsed - self.last_elapsed >= self.max_age:
                    clock_reset = True
                    raise ValueError("observation_gap")
                if (abs((now - self.last_now) - (elapsed - self.last_elapsed)) > .5
                        or abs(now - elapsed - self.clock_anchor) > .5):
                    clock_reset = True
                    raise ValueError("observation_clock_changed")
            if self.suspension:
                if (now >= self.suspension["expires_at"]
                        or elapsed >= self.suspension["expires_elapsed"]):
                    raise ValueError("recovery_expired")
                if source_id != self.previous.source_id:
                    raise ValueError("source_changed")
            if self.preapproval_anchor:
                if now - self.preapproval_anchor.at >= self.max_age:
                    raise ValueError("preapproval_copy_expired")
                if source_id != self.preapproval_anchor.source_id:
                    raise ValueError("source_changed")
            candidate = reading(status, source_id, now, self.max_age)
            if prior is None:
                raise ValueError("awaiting_advancing_observation")
            if candidate.source_id != prior.source_id:
                raise ValueError("source_changed")
            if candidate.continuity_generation != prior.continuity_generation:
                raise ValueError("source_continuity_changed")
            if candidate.context != prior.context or candidate.context != self.plan.context:
                raise ValueError("context_changed")
            if candidate.participant != prior.participant:
                raise ValueError("participant_view_changed")
            if candidate.game != 2 and not candidate.prestart:
                raise ValueError("cockpit_inactive")
            continuing_exit = (self.arm_id and candidate.solo_practice_exit
                               and prior.solo_practice_exit)
            if candidate.race != 2 and not (candidate.prestart or continuing_exit):
                raise ValueError("race_not_active")
            if prior.race == 2 and candidate.race == 1:
                raise ValueError("race_reset")
            if abs(candidate.capacity - prior.capacity) > 0.001:
                raise ValueError("capacity_changed")
            if candidate == prior:
                if self.health != "healthy":
                    raise ValueError("awaiting_advancing_observation")
            else:
                if candidate.at <= prior.at or candidate.offset <= prior.offset:
                    raise ValueError("source_clock_not_advancing")
                if candidate.count <= prior.count:
                    raise ValueError("sample_count_not_advancing")
                if candidate.sequence <= prior.sequence:
                    raise ValueError("sequence_not_advancing")
                if candidate.at - prior.at >= self.max_age:
                    raise ValueError("source_gap")
                if not self.source_clock_matches(candidate, prior):
                    raise ValueError("source_clock_changed")
                if not prior.lap <= candidate.lap <= prior.lap + 1:
                    raise ValueError("lap_counter_changed")
                if candidate.litres > prior.litres + 0.05:
                    raise ValueError("refuel_detected")
            if self.minimum_fuel is not None and candidate.litres > self.minimum_fuel + 0.05:
                raise ValueError("refuel_detected")
            if self.preapproval_anchor:
                anchor = self.preapproval_anchor
                if (candidate.lap != anchor.lap or candidate.race != anchor.race
                        or candidate.pit != anchor.pit or candidate.game != anchor.game
                        or candidate.speed is None or candidate.speed > 1
                        or abs(candidate.litres - anchor.litres) > 0.05):
                    raise ValueError("preapproval_basis_changed")
                if not self.preapproval_advanced:
                    self.preapproval_advanced = True
                    raise ValueError("awaiting_preapproval_observation")
                self.preapproval_anchor = None
                self.preapproval_advanced = False
            if self.suspension:
                self.recovery_samples += 1
                self.minimum_fuel = min(self.minimum_fuel, candidate.litres)
                if self.recovery_samples < 2:
                    raise ValueError("awaiting_recovery_observation")
                self._event("fuel_recovered", now, arm_id=self.arm_id,
                            loss_id=self.supervision_loss["id"],
                            suspended_at=self.suspension["started_at"])
                self._loss("fresh_pair_verified", now, resolution="recovered")
                self.suspension = None
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            # Reading validation emits codes only; malformed type errors are not retained verbatim.
            reason = str(exc) if type(exc) is ValueError else "malformed_status"
            if (reason == "coverage_unstable_copy" and prior is not None
                    and continuity_generation(status) != prior.continuity_generation):
                reason = "source_continuity_changed"
        self.last_now, self.last_elapsed = now, elapsed
        if self.clock_anchor is None or clock_reset:
            self.clock_anchor = now - elapsed
        recoverable = reason in {"coverage_unstable_copy", "awaiting_recovery_observation",
                                 "awaiting_advancing_observation"}
        if (reason == "coverage_unstable_copy" and not self.arm_id and self.current
                and self.current.source_id == source_id and self.current.participant
                and self.current.continuity_generation is not None
                and self.current.speed is not None and self.current.speed <= 1
                and 0 <= now - self.current.at < self.max_age):
            self.preapproval_anchor = self.current
        if reason == "coverage_unstable_copy":
            self.preapproval_advanced = False
        checking_copy = self.preapproval_anchor is not None and reason in {
            "coverage_unstable_copy", "awaiting_preapproval_observation",
            "awaiting_advancing_observation",
        }
        if (reason == "coverage_unstable_copy" and self.arm_id and not self.suspension
                and self.return_notice is None and self.current is not None
                and self.previous.source_id == source_id
                and 0 <= now - self.previous.at < self.max_age):
            self._suspend(now, elapsed)
        if self.suspension and reason == "coverage_unstable_copy":
            self.recovery_samples = 0
        if candidate is not None or not (checking_copy or self.suspension and recoverable):
            self.previous = candidate
            if candidate is None:
                self.source_clock_anchor = None
            elif (prior is None or candidate.source_id != prior.source_id
                  or reason in {"source_clock_changed", "source_continuity_changed"}):
                # The fault below revokes approval. This new baseline cannot
                # restore that approval or replay an old notice.
                self.source_clock_anchor = candidate.at - candidate.offset
        self.current = candidate if reason is None else None
        self.reason = reason
        self.health = "healthy" if reason is None else "degraded"
        if reason:
            self.healthy_since = None
        elif self.healthy_since is None:
            self.healthy_since = now
        if reason and not (checking_copy or self.suspension and recoverable):
            if reason != "awaiting_advancing_observation":
                self.approval_floor = now
            self._disarm(reason, now)
        self._event("observation", now, source_id=source_id, health=self.health,
                    reason=reason, reading=asdict(candidate) if candidate else None)
        notice = None
        if self.arm_id and self.current:
            self.minimum_fuel = min(self.minimum_fuel, self.current.litres)
            if (self.current.litres <= self.plan.return_threshold_litres
                    and self.return_notice is None and self.current.pit == 0
                    and self.current.game == 2 and self.current.race == 2):
                notice = dict(
                    id=uuid4().hex, kind="fuel_return", arm_id=self.arm_id,
                    evidence_id=self.evidence_id, created_at=now,
                    source_at=self.current.at,
                    expires_at=self.current.at + self.max_age,
                    litres=self.current.litres, threshold=self.plan.return_threshold_litres,
                    text="Pit this lap.",
                )
                self._event("notice", now, **notice)
                self.return_notice = notice
        result = dict(
            schema_version=1, at=now, supervision_health=self.health, reason=reason,
            fuel_state=self.fuel_state,
            healthy_since=self.healthy_since,
            fuel_litres=self.current.litres if self.current else None,
            expires_at=self.current.at + self.max_age if self.current else now,
            evidence_id=self.evidence_id, notice=notice,
            approval=self.approval_result,
            recovery=dict(self.suspension) if self.suspension else None,
            supervision_loss=dict(self.supervision_loss) if self.supervision_loss else None,
            participant=asdict(self.current.participant)
            if self.current and self.current.participant else None,
            **self.plan_status(),
            phase="prestart" if self.current and self.current.race == 1 else
            "active" if self.current else "unknown",
        )
        self.last_result = result
        return result

    @property
    def evidence_id(self):
        return self.current.evidence_id if self.current else None

    def arm(self, *, cockpit_litres, verified_at, now, source_id, solo_player_confirmed=False,
            note, player_view_confirmed=False, participant_index=None):
        reason = None
        try:
            number(now)
            number(verified_at)
            cockpit = number(cockpit_litres)
            if not self.current or self.health != "healthy":
                raise ValueError("capture_not_healthy")
            if source_id != self.current.source_id:
                raise ValueError("approval_source_changed")
            if self.approval_floor is not None and verified_at <= self.approval_floor:
                raise ValueError("approval_predates_invalidation")
            if not 0 <= now - verified_at <= 30 or not 0 <= now - self.current.at < self.max_age:
                raise ValueError("approval_not_fresh")
            if player_view_confirmed is True:
                index = integer(participant_index, maximum=63)
                if not self.current.participant or index != self.current.participant.index:
                    raise ValueError("confirmed_view_not_observed")
                if solo_player_confirmed is not False:
                    raise ValueError("conflicting_player_confirmation")
            elif (player_view_confirmed is not False or participant_index is not None
                  or solo_player_confirmed is not True):
                raise ValueError("player_context_not_confirmed")
            elif (self.current.context.session_state not in {1, 2}
                  or self.current.participant and self.current.participant.count != 1):
                raise ValueError("explicit_player_view_required")
            if (self.current.context != self.plan.context
                    or self.current.game != 2 and not self.current.prestart):
                raise ValueError("context_not_expected")
            if abs(cockpit - self.current.litres) > 0.2:
                raise ValueError("cockpit_fuel_mismatch")
            if self.plan.required_load > self.current.capacity:
                raise ValueError("plan_exceeds_capacity")
            if self.current.litres < self.plan.required_load:
                raise ValueError("insufficient_loaded_fuel")
            if self.current.litres <= self.plan.return_threshold_litres:
                raise ValueError("already_at_return_threshold")
            if not isinstance(note, str) or not note.strip():
                raise ValueError("missing_approval_note")
            if self.arm_id:
                raise ValueError("already_armed")
        except (ValueError, TypeError) as exc:
            reason = str(exc) if type(exc) is ValueError else "invalid_approval"
        if reason:
            self._event("arm_rejected", now, reason=reason)
            self.approval_result = dict(accepted=False, reason=reason, at=now)
            return False
        arm_id = uuid4().hex
        self._event("fuel_armed", now, arm_id=arm_id, evidence_id=self.evidence_id,
                    cockpit_litres=cockpit, verified_at=verified_at,
                    source_id=source_id, solo_player_confirmed=solo_player_confirmed, note=note,
                    player_view_confirmed=player_view_confirmed,
                    participant_index=participant_index,
                    participant=asdict(self.current.participant)
                    if self.current.participant else None,
                    plan=asdict(self.plan))
        if self.supervision_loss:
            loss = dict(self.supervision_loss, resolution="reapproved", updated_at=now,
                        replacement_arm_id=arm_id)
            self._event("supervision_loss", now, **loss)
            self.supervision_loss = loss
        self.arm_id = arm_id
        self.minimum_fuel = self.current.litres
        self.return_notice = None
        self.approval_result = dict(accepted=True, reason=None, at=now)
        return True

    def notice_valid(self, notice, *, now):
        """Recheck just before dispatch; caller must also keep observation polling alive."""
        return bool(
            self.arm_id and self.current and self.health == "healthy"
            and notice == self.return_notice and notice["arm_id"] == self.arm_id
            and notice["created_at"] <= now < notice["expires_at"]
            and 0 <= now - self.current.at < self.max_age
            and self.current.litres <= self.plan.return_threshold_litres
            and self.current.game == 2 and self.current.race == 2 and self.current.pit == 0
        )
