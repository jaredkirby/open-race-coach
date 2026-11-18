"""Read-only fuel and recording handoff for the separate operations service."""

import hashlib
import re
import time
from dataclasses import asdict
from pathlib import Path

from simcoach.live.capture_gate import published_capture_ready, recorder_deadline
from simcoach.live.fuel_radio import ArchiveStatusSource
from simcoach.live.supervisor import FuelPlan, number, reading, strict_json
from simcoach.live.visit_recovery import _artifact, _identity, probe_process

# The Laguna service frequently looked healthy for a single publication between faults.
# Accumulate this history while the Chief prepares the car, independent of approval time.
PIT_STABILITY_S = 10


def evaluate_fuel_release(current, service, plan, selection_hash, *, now):
    supervision = service["supervision"]
    if supervision.get("supervision_health") != "healthy":
        raise ValueError(supervision.get("reason") or "supervision_degraded")
    if supervision.get("fuel_state") != "armed" or (supervision.get("approval") or {}).get(
            "accepted") is not True:
        raise ValueError("fuel_approval_required")
    if (supervision.get("plan") != asdict(plan) or current.context != plan.context
            or current.continuity_generation is None
            or supervision.get("evidence_id") != current.evidence_id):
        raise ValueError("plan_or_current_fuel_observation_mismatch")
    if (current.participant is None or current.speed is None or current.speed > 0.1
            or current.game != 2 or current.pit not in {2, 5}
            or not (current.race == 2 or current.solo_practice_exit)):
        raise ValueError("stationary_pit_unverified")
    if current.litres < plan.required_load or current.litres <= plan.return_threshold_litres:
        raise ValueError("insufficient_departure_fuel")
    if number(supervision["at"]) - number(supervision["healthy_since"]) < PIT_STABILITY_S:
        raise ValueError("awaiting_stable_fuel_feed")
    radio = service["radio"]
    if (radio.get("delivery_health") != "available" or radio.get("muted") is not False
            or radio.get("closed") is not False or radio.get("loss_warning_configured") is not True
            or (radio.get("output_ownership") or {}).get("state") != "held"
            or radio["configuration"].get("transport") != "selected_windows_wave"
            or radio["configuration"].get("selection_sha256") != selection_hash):
        raise ValueError("fuel_radio_unavailable")
    deadline = min(number(supervision["expires_at"]), current.at + 3,
                   number(service["updated_at"]) + 3, number(service["chief_link"]["at"]) + 3)
    if (service.get("status") != "running" or service["chief_link"].get("state") != "connected"
            or not max(current.at, supervision["at"], service["updated_at"],
                       service["chief_link"]["at"]) <= now < deadline):
        raise ValueError("operations_expired_or_disconnected")
    return dict(decision="GO", reason=None, expires_at=deadline,
                fuel_litres=current.litres, required_litres=plan.required_load,
                return_litres=plan.return_threshold_litres)


def check_visit_fuel(run, plan_path, selection_path, *, clock=time.time, elapsed=time.perf_counter,
                     probe=probe_process):
    """Evaluate at the end of I/O. No request, output, inference or simulator access."""
    def artifact(name):
        _, value = _artifact(run, name)
        if value is None:
            raise ValueError("visit_evidence_unavailable")
        return value

    try:
        start, start_elapsed = number(clock()), number(elapsed())
        run = Path(run).resolve(strict=True)
        host = artifact("status.json")
        if (host.get("status") != "running" or host.get("mode") != "live"
                or host.get("declared_presence") is not True):
            raise ValueError("no_active_driver_visit")
        plan = FuelPlan.from_dict(strict_json(Path(plan_path).read_text(encoding="utf8")))
        selection_hash = hashlib.sha256(Path(selection_path).read_bytes()).hexdigest()
        archive = Path(host["archive"]).resolve(strict=True)
        owners = host["operations"]
        service_run = artifact("operations/run.json")
        if (owners.get("health") != "healthy" or owners.get("binding_valid") is not True
                or not service_run.get("service_id")
                or service_run["service_id"] != owners.get("service_id")
                or service_run.get("mode") != "local_operations"
                or service_run.get("audio_enabled") is not True
                or service_run.get("visit_controls") is not True
                or Path(service_run["visit"]).resolve() != run
                or Path(service_run["archive"]).resolve() != archive):
            raise ValueError("operations_owner_unverified")
        worker = host["current_worker"]
        if not isinstance(worker, str) or Path(worker).name != worker:
            raise ValueError("recorder_owner_unverified")
        host_process = artifact("host-process.json")
        worker_process = artifact(f"{worker}/worker-process.json")
        for owner in (host_process, worker_process, service_run):
            identity = _identity(owner, probe, clock)
            if identity["state"] != "running" or identity["identity_status"] != "matched":
                raise ValueError("owner_no_longer_running")
        if (worker_process["process"]["pid"] != host.get("worker_pid")
                or any(service_run["pinned_host"].get(key) != host_process["process"][key]
                       for key in ("pid", "created_100ns"))):
            raise ValueError("visit_owner_changed")
        control, mute = _artifact(run, "mute.json")
        command = mute.get("command_id") if mute else None
        if not isinstance(command, str) or not re.fullmatch("[0-9a-f]{32}", command):
            raise ValueError("radio_unmute_unverified")
        receipt = artifact(f"receipts/{command}.json")
        if (mute.get("muted") is not False
                or receipt.get("command_fingerprint") != control["sha256"]
                or receipt.get("command_id") != command or receipt.get("applied") is not True
                or receipt.get("muted") is not False):
            raise ValueError("radio_unmute_unverified")
        beats = [number(artifact(path)["at"])
                 for path in ("heartbeat.json", "operations/heartbeat.json")]
        recorder = artifact(f"{worker}/status.json")
        service = artifact("operations/status.json")
        if service.get("service_id") != service_run["service_id"]:
            raise ValueError("operations_owner_changed")
        source_id, status = ArchiveStatusSource(archive)()
        if (run / "stop").exists() or (run / "operations/stop").exists():
            raise ValueError("stop_requested")
        now = clock()
        intake = recorder_deadline(recorder, now=now, process_alive=True)
        if (intake is None or recorder.get("pid") != host["worker_pid"]
                or not published_capture_ready(host, now=now)
                or host["source"]["source_id"] != source_id):
            raise ValueError("recording_unverified")
        if any(not 0 <= now - beat < 3 for beat in beats):
            raise ValueError("owner_heartbeat_stale")
        current = reading(status, source_id, now, 3)
        result = evaluate_fuel_release(current, service, plan, selection_hash, now=now)
        result["expires_at"] = min(result["expires_at"], intake, host["capture_expires_at"],
                                   min(beats) + 3)
        final, duration = number(clock()), number(elapsed()) - start_elapsed
        if (not start <= now <= final < result["expires_at"] or duration < 0
                or abs((final - start) - duration) > .5):
            raise ValueError("fuel_check_expired")
        return result
    except (OSError, TypeError, ValueError, KeyError, AttributeError,
            OverflowError, RecursionError) as exc:
        reason = str(exc) if type(exc) is ValueError else ""
        return dict(decision="HOLD", reason=reason if re.fullmatch("[a-z_]+", reason)
                    else "fuel_evidence_unavailable", expires_at=None)
