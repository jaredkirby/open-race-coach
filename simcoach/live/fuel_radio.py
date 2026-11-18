"""Connect local fuel decisions to one evidence-gated radio owner.

This adapter never starts capture, changes a simulator, or selects an audio
device. The host supplies a transport and continues polling the Supervisor.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from simcoach.live.json_read import read_with_retry
from simcoach.live.radio import Dispatcher, Transmission, Transport, read_pcm
from simcoach.live.supervisor import Supervisor, reading, source_identity, strict_json

FUEL_PHRASE = "Pit this lap."
LOSS_PHRASE = "Fuel monitoring unavailable."
RECOVERY_PHRASE = "Fuel monitoring restored."
CHIEF_LOSS_PHRASE = "Race chief connection lost."


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class FuelPhrase:
    """A reviewed render package, not arbitrary text supplied by a live model."""

    manifest_path: Path
    wav_path: Path
    manifest_sha256: str
    wav_sha256: str
    duration_s: float
    text: str = FUEL_PHRASE

    @classmethod
    def load(cls, directory: Path, *, maximum_duration_s: float = 3.0,
             expected_text: str = FUEL_PHRASE) -> FuelPhrase:
        if expected_text not in {FUEL_PHRASE, LOSS_PHRASE, RECOVERY_PHRASE, CHIEF_LOSS_PHRASE}:
            raise ValueError("unsupported_radio_phrase")
        manifest = directory / "render.json"
        audio = directory / "radio.wav"
        metadata = strict_json(manifest.read_text(encoding="utf-8"))
        if (type(metadata.get("schema_version")) is not int
                or metadata["schema_version"] != 1 or metadata.get("status") != "generated"
                or metadata.get("text") != expected_text
                or metadata.get("audio_output_attempted") is not False):
            raise ValueError("unsupported_fuel_phrase_manifest")
        rate, samples = read_pcm(audio)
        duration = len(samples) / rate
        if (not 0 < duration < maximum_duration_s
                or metadata.get("sample_rate_hz") != rate
                or metadata.get("frames") != len(samples)
                or type(metadata.get("duration_s")) not in (float, int)
                or not math.isfinite(metadata["duration_s"])
                or abs(metadata["duration_s"] - duration) > 1e-9):
            raise ValueError("invalid_fuel_phrase_duration")
        audio_hash = digest(audio)
        if metadata.get("radio_sha256") != audio_hash:
            raise ValueError("fuel_phrase_hash_mismatch")
        return cls(manifest.resolve(), audio.resolve(), digest(manifest), audio_hash, duration,
                   expected_text)

    def unchanged(self) -> bool:
        return (digest(self.manifest_path) == self.manifest_sha256
                and digest(self.wav_path) == self.wav_sha256)


class ArchiveStatusSource:
    """Read one explicit archive; no newest-directory selection or capture control."""

    def __init__(self, archive: Path, *, diagnostic=None):
        self.archive = archive
        self.diagnostic = diagnostic

    def __call__(self) -> tuple[str, dict]:
        def snapshot():
            identity = source_identity(self.archive)
            status = strict_json((self.archive / "status.json").read_text(encoding="utf-8"))
            if source_identity(self.archive) != identity:
                raise ValueError("archive_changed_during_read")
            return identity, status

        return read_with_retry(snapshot, role="archive_snapshot", diagnostic=self.diagnostic)


class _StartGuard:
    def __init__(self, transport: Transport, fits: Callable[[], bool], started: Callable[[], None]):
        self.transport, self.fits, self.started = transport, fits, started

    def start(self, path: Path, *, can_start: Callable[[], bool]) -> bool:
        result = self.transport.start(path, can_start=lambda: can_start() and self.fits())
        if result is True:
            self.started()
        return result

    def poll(self) -> str:
        return self.transport.poll()

    def stop(self) -> None:
        self.transport.stop()

    def close(self) -> None:
        close_transport = getattr(self.transport, "close", None)
        if close_transport is not None:
            close_transport()

    def prepare_owner(self) -> None:
        prepare_owner = getattr(self.transport, "prepare_owner", None)
        if prepare_owner is not None:
            prepare_owner()

    @property
    def ownership(self):
        return getattr(self.transport, "ownership", None)


class FuelRadio:
    """Reusable host adapter. Call tick after observe, and frequently while playing.

    Radio failures become degraded snapshots and do not change the Supervisor or
    stop capture. Construction errors still require host-level isolation. Close
    returns closed=False on unconfirmed stop and retains journal ownership.
    """

    def __init__(self, supervisor: Supervisor, journal_path: Path, phrase: FuelPhrase,
                 source_reader: Callable[[], tuple[str, dict]], transport: Transport, *,
                 clock: Callable[[], float] = time.time, start_margin_s: float = 0.1,
                 loss_phrase: FuelPhrase | None = None,
                 recovery_phrase: FuelPhrase | None = None,
                 chief_phrase: FuelPhrase | None = None,
                 chief_loss_valid: Callable[[], bool] | None = None,
                 output_permitted: Callable[[], bool] | None = None):
        if (type(start_margin_s) not in (float, int) or not math.isfinite(start_margin_s)
                or not 0 <= start_margin_s <= 1):
            raise ValueError("invalid_start_margin")
        if phrase.duration_s + start_margin_s >= supervisor.max_age:
            raise ValueError("fuel_phrase_exceeds_evidence_lifetime")
        if not phrase.unchanged():
            raise ValueError("fuel_phrase_changed")
        if phrase.text != FUEL_PHRASE:
            raise ValueError("unexpected_fuel_phrase")
        if loss_phrase is not None and (loss_phrase.text != LOSS_PHRASE
                                       or not loss_phrase.unchanged()
                                       or loss_phrase.duration_s + start_margin_s >= 4.5):
            raise ValueError("invalid_loss_phrase")
        if recovery_phrase is not None and (
                loss_phrase is None or recovery_phrase.text != RECOVERY_PHRASE
                or not recovery_phrase.unchanged()
                or recovery_phrase.duration_s + start_margin_s >= 5):
            raise ValueError("invalid_recovery_phrase")
        self.supervisor = supervisor
        self.output_permitted = output_permitted or (lambda: True)
        if chief_phrase is not None and (
                chief_phrase.text != CHIEF_LOSS_PHRASE or not chief_phrase.unchanged()
                or chief_phrase.duration_s + start_margin_s >= 4.5
                or not callable(chief_loss_valid)):
            raise ValueError("invalid_chief_loss_phrase_or_guard")
        self.chief_phrase, self.chief_loss_valid = chief_phrase, chief_loss_valid
        self.chief_link = None
        self.chief_notices = {}
        self.last_chief_transmission_id = None
        self.phrase = phrase
        self.loss_phrase = loss_phrase
        self.loss_notices: dict[str, dict] = {}
        self.last_loss_transmission_id: str | None = None
        self.recovery_phrase = recovery_phrase
        self.recovery_notices: dict[str, dict] = {}
        self.last_recovery_transmission_id: str | None = None
        self.confirmed_started: set[str] = set()
        self.source_reader = source_reader
        self.clock = clock
        self.start_margin_s = start_margin_s
        self.notices: dict[str, dict] = {}
        self.last_transmission_id: str | None = None
        self.guard_reason: str | None = None
        self._evidence_window: tuple[str, float, float] | None = None
        self.failure_type: str | None = None
        self.failure_reason: str | None = None
        self.muted = False
        self.closed = False
        self.dispatcher = Dispatcher(journal_path, _StartGuard(transport, self._fits,
                                                             self._output_started),
                                     clock=clock, evidence_valid=self._evidence_valid)

    def _output_started(self):
        if self.dispatcher.active:
            # Transport confirmation is not proof of sound/hearing. It is enough
            # to justify clarifying a loss phrase that may have been cut short.
            self.confirmed_started.add(self.dispatcher.active.transmission_id)

    def _reject(self, reason: str) -> bool:
        self.guard_reason = reason
        return False

    def _accept(self, message: Transmission, checked_at: float, fresh_until: float) -> bool:
        self._evidence_window = (message.transmission_id, checked_at, fresh_until)
        self.guard_reason = None
        return True

    def _evidence_valid(self, message: Transmission) -> bool:
        """Read-only: do not approve, observe, disarm, or journal into Supervisor."""
        self._evidence_window = None
        if self.output_permitted() is not True:
            return self._reject("service_output_not_permitted")
        if message.transmission_id in self.chief_notices:
            return self._chief_valid(message)
        if message.transmission_id in self.loss_notices:
            return self._loss_valid(message)
        if message.transmission_id in self.recovery_notices:
            return self._recovery_valid(message)
        notice = self.notices.get(message.transmission_id)
        now = self.clock()
        if notice is None or message.evidence_id != notice["evidence_id"]:
            return self._reject("notice_binding_mismatch")
        if not self.supervisor.notice_valid(notice, now=now):
            return self._reject("notice_no_longer_valid")
        try:
            if not self.phrase.unchanged():
                return self._reject("fuel_phrase_changed")
            source_id, status = self.source_reader()
            read_at = self.clock()
            if read_at < now or not self.supervisor.notice_valid(notice, now=read_at):
                return self._reject("fresh_source_expired_during_read")
            candidate = reading(status, source_id, read_at, self.supervisor.max_age)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            return self._reject("fresh_source_unavailable_or_invalid")
        current = self.supervisor.current
        # notice_valid established current/arm. Recheck the arm/context against a
        # separate fresh read so a queued notice cannot survive a pit/reset/refuel.
        if (current is None or candidate.source_id != current.source_id
                or candidate.context != current.context
                or candidate.participant != current.participant
                or candidate.context != self.supervisor.plan.context):
            return self._reject("fresh_source_context_changed")
        if candidate.continuity_generation != current.continuity_generation:
            return self._reject("fresh_source_continuity_changed")
        if candidate.game != 2 or candidate.race != 2 or candidate.pit != 0:
            return self._reject("fresh_source_not_driving")
        if abs(candidate.capacity - current.capacity) > 0.001:
            return self._reject("fresh_capacity_changed")
        if (self.supervisor.minimum_fuel is None
                or candidate.litres > self.supervisor.minimum_fuel + 0.05):
            return self._reject("fresh_refuel_detected")
        if candidate.litres > self.supervisor.plan.return_threshold_litres:
            return self._reject("fresh_fuel_above_return_threshold")
        if not self.supervisor.source_clock_matches(candidate, current):
            return self._reject("fresh_source_clock_changed")
        if candidate != current and (
            candidate.at <= current.at or candidate.offset <= current.offset
            or candidate.count <= current.count or candidate.sequence <= current.sequence
            or candidate.at - current.at >= self.supervisor.max_age
            or not current.lap <= candidate.lap <= current.lap + 1
        ):
            return self._reject("fresh_source_progress_invalid")
        # Source reads can take time. Retain the original notice deadline and
        # check candidate age again after I/O; never refresh validity from wall time.
        after = self.clock()
        if (after < read_at or not self.supervisor.notice_valid(notice, now=after)
                or not 0 <= after - candidate.at < self.supervisor.max_age):
            return self._reject("fresh_source_expired_during_read")
        return self._accept(message, after, min(current.at, candidate.at) + self.supervisor.max_age)

    def _loss_valid(self, message: Transmission) -> bool:
        # The evidence is a committed local monitoring failure, not a guessed
        # fuel amount from the unavailable source. No fresh telemetry is needed
        # to state that monitoring is unavailable, but the supervisor must be live.
        notice = self.loss_notices[message.transmission_id]
        loss = self.supervisor.supervision_loss
        now = self.clock()
        result = self.supervisor.last_result
        if (loss is None or loss["id"] != notice["loss_id"]
                or message.evidence_id != notice["evidence_id"]
                or loss["resolution"] != "requires_approval"
                or loss["updated_at"] != message.created_at
                or self.supervisor.fuel_state != "unarmed"
                or result is None or not 0 <= now - result["at"] < self.supervisor.max_age
                or not message.created_at + .5 <= now < message.expires_at):
            return self._reject("loss_no_longer_actionable")
        if self.loss_phrase is None or not self.loss_phrase.unchanged():
            return self._reject("loss_phrase_changed")
        after = self.clock()
        if not now <= after < min(message.expires_at, result["at"] + self.supervisor.max_age):
            return self._reject("loss_expired_during_check")
        return self._accept(message, after, result["at"] + self.supervisor.max_age)

    def _submit_loss(self):
        loss = self.supervisor.supervision_loss
        if self.loss_phrase is None or loss is None:
            return
        identity = f"fuel_loss:{loss['id']}"
        if identity in self.loss_notices or self.clock() < loss["updated_at"] + .5:
            return
        # Recovered-before-dispatch episodes remain in the supervisor journal;
        # they never produce a delayed loss warning.
        if loss["resolution"] != "requires_approval":
            return
        evidence = {key: loss[key] for key in ("id", "arm_id", "started_at", "initial_reason",
                                               "updated_at", "resolution")}
        evidence_id = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        self.loss_notices[identity] = dict(loss_id=loss["id"], evidence_id=evidence_id)
        self.last_loss_transmission_id = identity
        self.dispatcher.submit(Transmission(
            identity, identity, evidence_id, self.loss_phrase.wav_path, 90,
            loss["updated_at"], loss["updated_at"] + 5,
        ))

    def _fits(self) -> bool:
        if self.output_permitted() is not True:
            return self._reject("service_output_not_permitted")
        message = self.dispatcher.active
        if message is None:
            return self._reject("no_active_output_intent")
        phrase = (self.chief_phrase if message.transmission_id in self.chief_notices
                  else self.loss_phrase if message.transmission_id in self.loss_notices
                  else self.recovery_phrase if message.transmission_id in self.recovery_notices
                  else self.phrase)
        now = self.clock()
        if now + phrase.duration_s + self.start_margin_s >= message.expires_at:
            return self._reject("insufficient_playback_lifetime")
        # The final permission read may block after the source guard succeeds.
        # Keep its time bound tied to this message and recheck it without I/O.
        window = self._evidence_window
        if (window is None or window[0] != message.transmission_id
                or not window[1] <= now < window[2]):
            return self._reject("evidence_expired_during_output_check")
        return True

    def update_chief_link(self, value):
        """Service-local evidence only; never changes fuel approval or presence."""
        self.chief_link = dict(value)

    def _chief_valid(self, message):
        notice = self.chief_notices[message.transmission_id]
        def current(now):
            link = self.chief_link
            return bool(link and link.get("state") == "lost"
                        and link.get("loss_id") == notice["loss_id"]
                        and link.get("created_at") == notice["created_at"]
                        and 0 <= now - link["at"] < 3
                        and notice["created_at"] <= now < notice["created_at"] + 5)
        now = self.clock()
        try:
            valid = (message.evidence_id == notice["evidence_id"] and current(now)
                     and self.chief_phrase.unchanged()
                     and self.chief_loss_valid() is True)
            after = self.clock()
            if not (valid and after >= now and current(after)):
                return self._reject("chief_loss_unverified_or_expired")
            return self._accept(message, after, self.chief_link["at"] + 3)
        except (OSError, ValueError, TypeError, KeyError):
            return self._reject("chief_loss_evidence_unavailable")

    def _submit_chief_loss(self):
        link = self.chief_link
        if self.chief_phrase is None or not link or link.get("state") != "lost":
            return
        identity = f"chief_loss:{link['loss_id']}"
        if identity in self.chief_notices or self.clock() - link["created_at"] < .5:
            return
        notice = {key: link[key] for key in ("loss_id", "created_at", "pinned_host")}
        encoded = json.dumps(notice, sort_keys=True).encode()
        notice["evidence_id"] = hashlib.sha256(encoded).hexdigest()
        self.chief_notices[identity] = notice
        self.last_chief_transmission_id = identity
        self.dispatcher.submit(Transmission(identity, identity, notice["evidence_id"],
                                           self.chief_phrase.wav_path, 90,
                                           link["created_at"], link["created_at"] + 5))

    def _recovery_current(self, notice, now):
        loss, current = self.supervisor.supervision_loss, self.supervisor.current
        result = self.supervisor.last_result
        return bool(
            loss and current and result and loss["id"] == notice["loss_id"]
            and loss["resolution"] in {"recovered", "reapproved"}
            and loss["updated_at"] == notice["created_at"]
            and self.supervisor.arm_id == notice["arm_id"]
            and self.supervisor.health == "healthy"
            and self.supervisor.fuel_state in {"armed", "return_due"}
            and notice["created_at"] <= now < notice["expires_at"]
            and 0 <= now - result["at"] < self.supervisor.max_age
            and 0 <= now - current.at < self.supervisor.max_age
        )

    def _recovery_valid(self, message):
        notice = self.recovery_notices[message.transmission_id]
        now = self.clock()
        if (message.evidence_id != notice["evidence_id"]
                or not self._recovery_current(notice, now)):
            return self._reject("recovery_no_longer_current")
        current = self.supervisor.current
        try:
            if self.recovery_phrase is None or not self.recovery_phrase.unchanged():
                return self._reject("recovery_phrase_changed")
            source_id, status = self.source_reader()
            read_at = self.clock()
            if read_at < now or not self._recovery_current(notice, read_at):
                return self._reject("recovery_expired_during_check")
            candidate = reading(status, source_id, read_at, self.supervisor.max_age)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            return self._reject("recovery_source_unavailable")
        if (candidate.source_id != current.source_id or candidate.context != current.context
                or candidate.participant != current.participant
                or candidate.context != self.supervisor.plan.context
                or candidate.game != 2 or candidate.race != 2
                or abs(candidate.capacity - current.capacity) > .001
                or self.supervisor.minimum_fuel is None
                or candidate.litres > self.supervisor.minimum_fuel + .05):
            return self._reject("recovery_source_changed")
        if candidate.continuity_generation != current.continuity_generation:
            return self._reject("recovery_source_continuity_changed")
        if not self.supervisor.source_clock_matches(candidate, current):
            return self._reject("recovery_source_clock_changed")
        if candidate != current and (
                candidate.at <= current.at or candidate.offset <= current.offset
                or candidate.count <= current.count or candidate.sequence <= current.sequence
                or candidate.at - current.at >= self.supervisor.max_age
                or not current.lap <= candidate.lap <= current.lap + 1):
            return self._reject("recovery_source_not_advancing")
        after = self.clock()
        if (after < read_at or not self._recovery_current(notice, after)
                or not 0 <= after - candidate.at < self.supervisor.max_age):
            return self._reject("recovery_expired_during_check")
        oldest_at = min(current.at, candidate.at, self.supervisor.last_result["at"])
        return self._accept(message, after, oldest_at + self.supervisor.max_age)

    def _submit_recovery(self):
        loss = self.supervisor.supervision_loss
        if self.recovery_phrase is None or not loss:
            return
        loss_identity = f"fuel_loss:{loss['id']}"
        prior_completed = self.dispatcher.states.get(loss_identity) in {"played", "acknowledged"}
        if (loss["resolution"] not in {"recovered", "reapproved"}
                or not (loss_identity in self.confirmed_started or prior_completed)):
            return
        arm_id = loss.get("replacement_arm_id", loss["arm_id"])
        identity = f"fuel_recovery:{loss['id']}:{arm_id}"
        if identity in self.recovery_notices:
            return
        notice = dict(loss_id=loss["id"], arm_id=arm_id,
                      created_at=loss["updated_at"], expires_at=loss["updated_at"] + 5)
        payload = json.dumps(notice, sort_keys=True).encode()
        notice["evidence_id"] = hashlib.sha256(payload).hexdigest()
        self.recovery_notices[identity] = notice
        self.last_recovery_transmission_id = identity
        self.dispatcher.submit(Transmission(
            identity, identity, notice["evidence_id"], self.recovery_phrase.wav_path, 80,
            notice["created_at"], notice["expires_at"],
        ))

    def _failure(self, error: Exception) -> None:
        self.failure_type = type(error).__name__
        self.failure_reason = "radio_operation_failed"
        try:
            self.dispatcher.close()
            self.closed = True
        except Exception:
            # Keep owner/reference alive, so another process cannot overlap an
            # output that did not confirm stopping. No capture control is called.
            self.failure_reason = "radio_close_unconfirmed"

    def tick(self) -> dict:
        if self.failure_reason or self.closed:
            return self.snapshot()
        notice = self.supervisor.return_notice
        try:
            if notice is not None:
                identity = f"fuel:{notice['arm_id']}:{notice['id']}"
                self.last_transmission_id = identity
                if identity not in self.notices:
                    bound = dict(notice)
                    self.notices[identity] = bound
                    if bound.get("text") != FUEL_PHRASE or bound.get("kind") != "fuel_return":
                        raise ValueError("unsupported_fuel_notice")
                    self.dispatcher.submit(Transmission(
                        identity, f"fuel_return:{bound['arm_id']}", bound["evidence_id"],
                        self.phrase.wav_path, 100, bound["created_at"], bound["expires_at"],
                    ), source_at=bound.get("source_at"))
            self._submit_loss()
            self._submit_recovery()
            self._submit_chief_loss()
            self.dispatcher.tick()
        except Exception as error:
            self._failure(error)
        return self.snapshot()

    def set_muted(self, muted: bool) -> dict:
        if type(muted) is not bool:
            raise ValueError("mute_requires_boolean")
        self.muted = muted
        if not self.closed and not self.failure_reason:
            try:
                self.dispatcher.set_muted(muted)
            except Exception as error:
                self._failure(error)
        return self.snapshot()

    def snapshot(self) -> dict:
        current_notice = self.supervisor.return_notice
        current_identity = (f"fuel:{current_notice['arm_id']}:{current_notice['id']}"
                            if current_notice else None)
        state = self.dispatcher.states.get(self.last_transmission_id, "none")
        matches = current_identity is not None and current_identity == self.last_transmission_id
        unsuccessful = matches and state in {"failed", "expired", "interrupted", "suppressed"}
        loss_state = self.dispatcher.states.get(self.last_loss_transmission_id, "none")
        chief_state = self.dispatcher.states.get(self.last_chief_transmission_id, "none")
        chief_unsuccessful = bool(
            self.chief_link and self.chief_link.get("state") == "lost"
            and self.last_chief_transmission_id == f"chief_loss:{self.chief_link['loss_id']}"
            and chief_state in {"failed", "expired", "interrupted", "suppressed"})
        recovery_state = self.dispatcher.states.get(self.last_recovery_transmission_id, "none")
        recovery_notice = self.recovery_notices.get(self.last_recovery_transmission_id)
        recovery_unsuccessful = (recovery_notice is not None
                                 and self.supervisor.arm_id == recovery_notice["arm_id"]
                                 and recovery_state in {"failed", "expired", "interrupted",
                                                        "suppressed"})
        loss = self.supervisor.supervision_loss
        loss_unsuccessful = (loss is not None
                             and f"fuel_loss:{loss['id']}" == self.last_loss_transmission_id
                             and loss["resolution"] in {"recovering", "requires_approval"}
                             and loss_state in {"failed", "expired", "interrupted", "suppressed"})
        health = ("degraded" if self.failure_reason
                  or (unsuccessful or loss_unsuccessful or recovery_unsuccessful
                      or chief_unsuccessful) and not self.muted
                  else "closed" if self.closed else "muted" if self.muted else "available")
        reason = (self.failure_reason or self.supervisor.reason or self.guard_reason
                  or (f"transmission_{state}" if unsuccessful else None))
        return {
            "fuel_state": self.supervisor.fuel_state, "supervision_health": self.supervisor.health,
            "supervision_loss": (dict(self.supervisor.supervision_loss)
                                 if self.supervisor.supervision_loss else None),
            "loss_warning_configured": self.loss_phrase is not None,
            "loss_transmission_id": self.last_loss_transmission_id,
            "loss_delivery_state": loss_state,
            "recovery_warning_configured": self.recovery_phrase is not None,
            "recovery_transmission_id": self.last_recovery_transmission_id,
            "recovery_delivery_state": recovery_state,
            "delivery_health": health, "delivery_state": state,
            "fuel_notice_id": current_notice["id"] if current_notice else None,
            "transmission_id": self.last_transmission_id,
            "delivery_matches_current_notice": matches,
            "reason": reason,
            "failure_type": self.failure_type, "muted": self.muted, "closed": self.closed,
            "audible_delivery_verified": False,
            "output_ownership": self.dispatcher.transport.ownership,
            "chief_loss_warning_configured": self.chief_phrase is not None,
            "chief_loss_transmission_id": self.last_chief_transmission_id,
            "chief_loss_delivery_state": chief_state,
        }

    def close(self) -> dict:
        if not self.closed:
            try:
                self.dispatcher.close()
                self.closed = True
            except Exception as error:
                self.failure_type = type(error).__name__
                self.failure_reason = "radio_close_unconfirmed"
        return self.snapshot()
