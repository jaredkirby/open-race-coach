"""Visit-local radio binding and priority mute commands. No output on import."""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.fuel_radio import (
    CHIEF_LOSS_PHRASE,
    LOSS_PHRASE,
    RECOVERY_PHRASE,
    ArchiveStatusSource,
    FuelPhrase,
    FuelRadio,
)
from simcoach.live.supervisor import strict_json

MAX_CONTROL_BYTES = 65536


def _timestamp(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


class VisitAudio:
    """Host-owned, initially muted. Errors are exposed without controlling capture."""

    def __init__(self, run: Path, supervisor, *, archive: Path, phrase: Path,
                 selection: Path, clock=time.time, factory=None, loss_phrase: Path | None = None,
                 recovery_phrase: Path | None = None, chief_phrase: Path | None = None,
                 chief_loss_valid=None, source_reader=None, output_permitted=None,
                 controls_root=None):
        self.run, self.clock = run, clock
        self.controls = Path(controls_root) if controls_root is not None else run
        self.radio = None
        self.seen = {}
        self.rejections = set()
        self.accepted_unmute = None
        self.service_output_permitted = output_permitted
        self.error = None
        self.closing = False
        self.unmute_after = 0.0
        self.last = dict(delivery_health="degraded", muted=True, closed=True,
                         audible_delivery_verified=False)
        self.configuration = dict(schema_version=1,
            transport="injected_factory" if factory is not None else "unconfigured",
            selection_sha256=None)
        try:
            self.unmute_after = clock()
            if not _timestamp(self.unmute_after):
                raise ValueError("invalid_radio_host_clock")
            if factory is None:
                from simcoach.live.radio import OutputSelection, WindowsWaveTransport
                with selection.open("rb") as stream:
                    selection_bytes = stream.read(65537)
                if len(selection_bytes) > 65536:
                    raise ValueError("audio_selection_too_large")
                chosen = OutputSelection.from_dict(strict_json(selection_bytes.decode("utf-8")))
                self.configuration = dict(schema_version=1, transport="selected_windows_wave",
                    selection_sha256=hashlib.sha256(selection_bytes).hexdigest())

                def factory():
                    return WindowsWaveTransport(enable_audio=True, selection=chosen)
            loss_package = (FuelPhrase.load(loss_phrase, expected_text=LOSS_PHRASE)
                            if loss_phrase else None)
            recovery_package = (FuelPhrase.load(recovery_phrase, expected_text=RECOVERY_PHRASE)
                                if recovery_phrase else None)
            chief_package = (FuelPhrase.load(chief_phrase, expected_text=CHIEF_LOSS_PHRASE)
                             if chief_phrase else None)
            self.radio = FuelRadio(supervisor, run / "radio.jsonl", FuelPhrase.load(phrase),
                                   source_reader or ArchiveStatusSource(archive), factory(),
                                   clock=clock,
                                   loss_phrase=loss_package, recovery_phrase=recovery_package,
                                   chief_phrase=chief_package, chief_loss_valid=chief_loss_valid,
                                   output_permitted=self._output_permitted)
            self.last = self.radio.set_muted(True)
        except Exception as exc:
            self.error = type(exc).__name__
            self.last.update(reason="radio_initialization_failed", failure_type=self.error)
            self.closing = True
            self._safe_close()

    def _safe_close(self):
        self.accepted_unmute = None
        if self.radio:
            try:
                self.last = self.radio.close()
            except Exception as exc:
                # Retain the owner reference and do not let an audio exception
                # skip the coordinator's clock/capture shutdown responsibilities.
                self.error = "radio_close_unconfirmed"
                self.last.update(delivery_health="degraded", closed=False,
                                 failure_type=type(exc).__name__, reason=self.error)

    def _receipt(self, identity, fingerprint, *, applied, reason, rejected=False):
        # Match normal receipt filename length on Windows; full digest is in the receipt.
        name = f"rejected-{fingerprint[:24]}.json" if rejected else f"{identity}.json"
        path = self.controls / "receipts" / name
        if path.exists():
            return  # A historical receipt is immutable, including rejected commands.
        write_status(path, dict(
            schema_version=1, at=self.clock(), command_id=identity,
            command_fingerprint=fingerprint, accepted=applied, applied=applied,
            muted=self.last["muted"], reason=reason, audio_heard=None,
        ))

    def _reject(self, reason, fingerprint, identity=None):
        self.accepted_unmute = None
        self.error = reason
        now = self.clock()
        if not _timestamp(now):
            raise ValueError("invalid_radio_host_clock")
        self.unmute_after = max(self.unmute_after, now)
        if self.radio:
            self.last = self.radio.set_muted(True)
        if fingerprint not in self.rejections:
            self._receipt(identity, fingerprint, applied=False, reason=reason, rejected=True)
            self.rejections.add(fingerprint)

    def _read_control(self):
        with (self.controls / "mute.json").open("rb") as stream:
            return stream.read(MAX_CONTROL_BYTES + 1)

    def _output_permitted(self):
        service_permits = self.service_output_permitted
        if (self.closing or self.accepted_unmute is None
                or service_permits is not None and service_permits() is not True):
            return False
        try:
            # The transport also calls this after preparing its native buffer.
            # A later control is evaluated by the owner loop before it can grant output.
            return self._read_control() == self.accepted_unmute[1]
        except OSError:
            return False

    def _unavailable_control(self, reason):
        self.accepted_unmute = None
        if self.radio:
            self.last = self.radio.set_muted(True)
        self.error = reason
        self.unmute_after = max(self.unmute_after, self.clock())

    def _mute_command(self, *, active_visit):
        try:
            raw = self._read_control()
        except FileNotFoundError:
            if self.accepted_unmute is not None:
                self._unavailable_control("mute_command_missing")
            return
        except OSError:
            self._unavailable_control("mute_command_unreadable")
            return
        if len(raw) > MAX_CONTROL_BYTES:
            self._unavailable_control("mute_command_too_large")
            return
        fingerprint = hashlib.sha256(raw).hexdigest()
        try:
            request = strict_json(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            self._reject("invalid_mute_command", fingerprint)
            return
        identity = request.get("command_id") if isinstance(request, dict) else None
        if not isinstance(identity, str) or len(identity) != 32 or any(
                char not in "0123456789abcdef" for char in identity):
            self._reject("invalid_mute_identity", fingerprint)
            return
        at, muted = request.get("at"), request.get("muted")
        watermark = request.get("intent_watermark", at)
        valid = (type(request.get("schema_version")) is int and request["schema_version"] == 1
                 and type(muted) is bool and _timestamp(at) and _timestamp(watermark)
                 and watermark >= at)
        if not valid:
            self._reject("invalid_mute_command", fingerprint, identity)
            return
        # Bind identity to semantic content, allowing only harmless JSON formatting changes.
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if identity in self.seen:
            if self.seen[identity] != fingerprint:
                self._reject("mute_command_identity_conflict", fingerprint, identity)
            elif self.accepted_unmute is not None and self.accepted_unmute[0] == identity:
                self.accepted_unmute = (identity, raw)
            return
        self.accepted_unmute = None
        now = self.clock()
        if not _timestamp(now):
            raise ValueError("invalid_radio_host_clock")
        fresh = 0 <= now - at < 3
        ordered = at > self.unmute_after and watermark == at
        allowed = muted or fresh and ordered and active_visit is True and not self.closing
        applied = False
        reason = "stale_or_obsolete_unmute"
        if allowed:
            if self.radio:
                self.last = self.radio.snapshot()
                if not muted and (self.last["closed"] or self.last.get("failure_type") is not None):
                    reason = "radio_unavailable_for_unmute"
                    self.last = self.radio.set_muted(True)
                else:
                    self.last = self.radio.set_muted(muted)
                    applied = (self.last["muted"] == muted
                               and (muted and self.last["closed"]
                                    or not self.last["closed"]
                                    and self.last.get("failure_type") is None))
                    reason = None if applied else "radio_stop_or_state_unconfirmed"
            else:
                reason = "radio_unavailable"
        elif self.radio:
            self.last = self.radio.set_muted(True)
        self.unmute_after = max(self.unmute_after, min(watermark, now))
        self._receipt(identity, fingerprint, applied=applied, reason=reason)
        self.seen[identity] = fingerprint
        if applied:
            self.error = None
            if not muted:
                self.accepted_unmute = (identity, raw)

    def tick(self, *, active_visit=True, allow_unmute=True):
        try:
            self._mute_command(active_visit=active_visit is True and not self.closing
                               and allow_unmute is True)
            if self.radio:
                if active_visit is not True or self.closing:
                    self.accepted_unmute = None
                    self.last = self.radio.set_muted(True)
                if not self.closing:
                    self.last = self.radio.tick()
        except Exception as exc:
            self.error = type(exc).__name__
            self.closing = True
            self._safe_close()
        return self.snapshot()

    def snapshot(self):
        result = dict(self.last, configuration=dict(self.configuration))
        if self.error:
            result.update(delivery_health="degraded", host_error=self.error)
        return result

    def close(self):
        self.closing = True
        self._safe_close()
        return self.snapshot()
