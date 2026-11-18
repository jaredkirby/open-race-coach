"""Read-only operations display, independently bound and expiring; no approvals."""
from __future__ import annotations

import hashlib
from pathlib import Path

from simcoach.cli.capture_health import continuity_generation
from simcoach.live.dashboard import _read
from simcoach.live.supervisor import number, strict_json
from simcoach.live.visit_recovery import _identity, probe_process


class OperationsDisplay:
    def __init__(self, directory, archive, *, probe=probe_process):
        self.directory = Path(directory).absolute() if directory else None
        self.archive = Path(archive).resolve()
        self.probe = probe
        self.pinned = None
        self.changed = False
        self.previous = None
        self.progress_ready = False
        self.last_clock = None

    def observe(self, telemetry, *, now, elapsed):
        if self.directory is None:
            return dict(state="not_reported", valid_for_ms=0, reason="no_service_selected")
        try:
            now, elapsed = number(now), number(elapsed)
            if self.last_clock:
                wall, mono = self.last_clock
                if (now < wall or elapsed < mono or elapsed - mono > 1
                        or abs((now - wall) - (elapsed - mono)) > .5):
                    self.previous = None
                    raise ValueError("operations_observation_gap")
            root = self.directory
            if root.resolve() != root:
                raise ValueError("operations_path_redirected")
            raw = _read(root, "run.json")
            run = strict_json(raw.decode("utf8"))
            state = strict_json(_read(root, "status.json").decode("utf8"))
            beat = strict_json(_read(root, "heartbeat.json").decode("utf8"))
            if _read(root, "run.json") != raw:
                raise ValueError("operations_identity_changed")
            digest = hashlib.sha256(raw).hexdigest()
            if self.pinned is None:
                self.pinned = digest
            if self.pinned != digest:
                self.changed = True
            if self.changed:
                raise ValueError("operations_identity_changed")
            if (any(not isinstance(row, dict) or type(row.get("schema_version")) is not int
                    or row["schema_version"] != 1
                    for row in (run, state, beat)) or run.get("mode") != "local_operations"
                    or Path(run["archive"]).resolve() != self.archive
                    or state.get("service_id") != run["service_id"]):
                raise ValueError("operations_binding_unverified")
            process = _identity(run, self.probe, lambda: now)
            if process["state"] != "running" or process["identity_status"] != "matched":
                raise ValueError("operations_process_unverified")
            updated, heartbeat = number(state["updated_at"]), number(beat["at"])
            if (state.get("status") != "running" or not 0 <= now - updated < 3
                    or not 0 <= now - heartbeat < 3):
                raise ValueError("operations_status_expired")
            if self.previous is None or updated < self.previous:
                self.previous = updated
                raise ValueError("operations_awaiting_progress")
            if updated == self.previous and not self.progress_ready:
                raise ValueError("operations_awaiting_progress")
            self.previous = updated
            self.progress_ready = True
            supervision = state["supervision"]
            fuel = supervision["fuel_state"]
            if fuel not in {"unarmed", "armed", "return_due", "suspended"}:
                raise ValueError("operations_state_unrecognized")
            if fuel in {"unarmed", "suspended"}:
                remaining = min(2, updated + 3 - now, heartbeat + 3 - now)
                return dict(state=fuel, valid_for_ms=int(remaining * 1000),
                            service_id=run["service_id"], updated_at=updated,
                            reason="supervision_not_active")
            source = state.get("observed_source")
            if (telemetry.get("state") != "fresh" or not isinstance(source, dict)
                    or source.get("source_id") != telemetry.get("source_id")):
                raise ValueError("operations_source_unverified")
            if continuity_generation(source) != continuity_generation(telemetry):
                raise ValueError("operations_source_continuity_changed")
            if source["context"] != telemetry["telemetry"]["context"]:
                raise ValueError("operations_context_mismatch")
            if ("participant_index" not in source or source["participant_index"] !=
                    telemetry["telemetry"].get("view", {}).get("index")):
                raise ValueError("operations_view_mismatch")
            source_at = number(source["at"])
            sequence = source["sequence"]
            display_at = number(telemetry["source_at"])
            display_sequence = telemetry["source_sequence"]
            if (type(sequence) is not int or type(display_sequence) is not int
                    or not number(telemetry["source_epoch_at"]) <= source_at <= display_at
                    or sequence > display_sequence
                    or (sequence == display_sequence) != (source_at == display_at)):
                raise ValueError("operations_source_sample_mismatch")
            if supervision.get("supervision_health") != "healthy":
                raise ValueError("operations_supervision_degraded")
            remaining = min(updated + 3, heartbeat + 3,
                            number(supervision["expires_at"]), source_at + 2) - now
            if remaining <= 0 or source_at > now:
                raise ValueError("operations_source_expired")
            radio = state.get("radio")
            radio = radio if isinstance(radio, dict) else {}
            return dict(state=fuel, reason=None, valid_for_ms=int(min(2, remaining) * 1000),
                        service_id=run["service_id"], updated_at=updated,
                        radio_delivery=radio.get("delivery_health") if radio.get("delivery_health")
                            in {"healthy", "degraded", "disabled"} else "unknown",
                        audible_delivery_verified=False)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            self.progress_ready = False
            reason = str(error) if isinstance(error, ValueError) else "operations_unavailable"
            if not reason.startswith("operations_"):
                reason = "operations_evidence_invalid"
            return dict(state="unknown", reason=reason, valid_for_ms=0)
        finally:
            self.last_clock = (now, elapsed)
