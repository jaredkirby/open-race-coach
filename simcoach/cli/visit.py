"""Chief-owned visit lifecycle; start only with explicit driver presence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from simcoach.cli.capture_health import _status_fields, continuity_generation
from simcoach.cli.race_watch import SourceLock, timestamp
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.activity_evidence import ActivityEvidence
from simcoach.live.capture_gate import MAX_CAPTURE_AGE_S, CaptureGate, recorder_deadline
from simcoach.live.fuel_radio import ArchiveStatusSource
from simcoach.live.json_read import read_with_retry
from simcoach.live.operations_client import OperationsClient
from simcoach.live.recording_storage import RecordingStorage, storage_deadline
from simcoach.live.supervisor import (
    FuelPlan,
    Journal,
    Supervisor,
    number,
    source_identity,
    strict_json,
)
from simcoach.live.visit_arrival import (
    ArrivalError,
    abandon_before_host,
    claim_arrival,
    declare_arrival,
    depart_before_host,
    note_before_host,
    pending_arrival,
)
from simcoach.live.visit_attempts import CaptureAttempts
from simcoach.live.visit_audio import VisitAudio
from simcoach.live.visit_clock import REASONS, VisitClock
from simcoach.live.visit_outcome import write_visit_outcome
from simcoach.live.visit_recovery import inspect_visit, probe_process
from simcoach.live.visit_release import active_binding
from simcoach.live.visit_review import record_review


def classify(status, now):
    """Classify one snapshot only; this alone cannot establish clock continuity."""
    sample = _status_fields(status, datetime.fromtimestamp(now, UTC), 2)
    if sample.health != "healthy":
        return "unknown"
    latest = status["latest"]
    if latest.get("mVersion") != 14:
        return "unknown"
    game = latest.get("mGameState")
    if game in (1, 3, 4, 6, 7):
        return "not_driving"
    speed = latest.get("mSpeed")
    if game != 2 or type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0:
        return "unknown"
    if speed <= 1:
        return "not_driving"
    pit = latest.get("mPitMode")
    if type(pit) is not int or not 0 <= pit <= 5:
        return "unknown"
    return "pit_moving" if pit else "driving"


def recorder_ready(status, *, now, process_alive):
    return recorder_deadline(status, now=now, process_alive=process_alive) is not None


def read_json(path, diagnostic=None):
    try:
        return read_with_retry(lambda: strict_json(path.read_text(encoding="utf-8")),
                               role="visit_artifact", diagnostic=diagnostic)
    except (OSError, ValueError):
        return None


def archive_observation(archive, diagnostic):
    """Bind observation time after bounded source I/O, never before a retry."""
    identity, status = None, None
    try:
        source = ArchiveStatusSource(archive)
        source.diagnostic = diagnostic
        identity, status = source()
    except (OSError, ValueError) as exc:
        diagnostic(dict(schema_version=1, event="archive_read_failed", at=time.time(),
                        error_type=type(exc).__name__, errno=getattr(exc, "errno", None),
                        winerror=getattr(exc, "winerror", None)))
    return identity, status, time.time(), time.perf_counter()


def operations_supervision(operation, status, *, source_id):
    """Mirror current service supervision only for the matching archive generation."""
    if operation["health"] != "healthy":
        return dict(supervision_health="degraded", fuel_state="unknown", reason=operation["reason"])
    state = operation["state"]
    summary = state.get("supervision")
    if not isinstance(summary, dict) or summary.get("fuel_state") not in {"armed", "return_due"}:
        return summary
    observed = state.get("observed_source")
    try:
        if (not isinstance(status, dict) or not isinstance(observed, dict)
                or observed["source_id"] != source_id
                or continuity_generation(observed) != continuity_generation(status)):
            raise ValueError("operations_source_continuity_unverified")
    except (KeyError, ValueError):
        return dict(summary, supervision_health="degraded", fuel_state="unknown",
                    reason="operations_source_continuity_unverified", notice=None, approval=None)
    return summary

def capture_readiness(worker, source, *, now, process_alive, storage=None, elapsed=None):
    intake = recorder_deadline(worker, now=now, process_alive=process_alive)
    expires = None
    if (intake is not None and isinstance(source, dict) and source.get("ready") is True
            and source.get("health") == "healthy"):
        try:
            source_expires = number(source["expires_at"])
            if now < source_expires <= now + MAX_CAPTURE_AGE_S:
                expires = min(intake, source_expires)
        except (KeyError, ValueError):
            pass
    if expires is not None and storage is not None:
        owner_deadline = storage_deadline(storage, now=now, elapsed=elapsed)
        worker_deadline = storage_deadline(worker.get("recording_storage"), now=now,
                                           elapsed=elapsed)
        expires = (min(expires, owner_deadline, worker_deadline)
                   if owner_deadline is not None and worker_deadline is not None else None)
    return dict(recorder_intake_ready=intake is not None, capture_ready=expires is not None,
                capture_expires_at=expires)


def closure_success(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return False
    rows = value.get("recordings")
    return bool(isinstance(rows, list) and rows and not value.get("unregistered_artifacts")
                and all(isinstance(r, dict) and r.get("validation") == "PASS"
                        and r.get("analysis") == "PASS" and isinstance(r.get("summary"), dict)
                        and r.get("analysis_path") for r in rows))


def worker_stopped_for_departure(value):
    """Exit zero alone can also mean lease expiry before the driver left."""
    return (isinstance(value, dict) and type(value.get("schema_version")) is int
            and value["schema_version"] == 1 and value.get("status") == "complete"
            and value.get("stop_reason") == "visit_stop_requested")


def process_snapshot(pid):
    """A failed diagnostic must not prevent independent capture or clock ownership."""
    try:
        return probe_process(pid)
    except Exception as exc:
        return dict(pid=pid, state="unknown", created_100ns=None,
                    reason=f"probe_failed_{type(exc).__name__}")


def publish_outcome(run, *, departure_elapsed=None):
    """Publish a separate receipt without changing the report's terminal input files."""
    started = time.perf_counter()
    timing = None
    try:
        outcome = write_visit_outcome(run)
        report_hash = hashlib.sha256(Path(outcome["report_path"]).read_bytes()).hexdigest()
        finished = time.perf_counter()
        if math.isfinite(started) and math.isfinite(finished) and 0 <= started <= finished:
            after_departure = None
            if (type(departure_elapsed) in (int, float)
                    and 0 <= departure_elapsed <= started):
                after_departure = finished - departure_elapsed
            timing = dict(scope="same_visit_host_counter", write_and_hash_s=finished - started,
                          after_declared_departure_s=after_departure)
        receipt = dict(schema_version=1, status="written", at=time.time(),
                       report_path=str(outcome["report_path"]), error_type=None,
                       report_sha256=report_hash, software_timing=timing)
    except Exception as exc:
        receipt = dict(schema_version=1, status="failed", at=time.time(),
                       report_path=None, report_sha256=None, error_type=type(exc).__name__,
                       software_timing=None)
        (run / "outcome-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
    write_status(run / "outcome-result.json", receipt)
    return receipt


def update_presence(run, clock, activity, *, now, elapsed, source_evidence):
    """Serialize an observed clock advance against departure publication.

    Skipping a contended observation cannot move the clock past a declaration
    whose timestamp has been chosen but whose stop file is not yet visible.
    """
    try:
        lock = SourceLock(run / "request.lock")
    except RuntimeError:
        return False
    try:
        if (run / "stop").exists():
            return False
        clock.update(activity, at=datetime.fromtimestamp(now, UTC), elapsed=elapsed,
                     source_evidence=source_evidence)
        return True
    finally:
        lock.close()


def request(run, action, **fields):
    state = read_json(run / "status.json")
    if not isinstance(state, dict) or state.get("status") != "running":
        raise ValueError("visit_not_running")
    if action == "stop":
        # End is an explicit presence declaration, not a timeout inference.
        deadline = time.perf_counter() + 1
        while True:
            try:
                request_lock = SourceLock(run / "request.lock")
                break
            except RuntimeError:
                if time.perf_counter() >= deadline:
                    raise
                time.sleep(.01)
        try:
            # The host may have closed while this caller waited for its clock write.
            state = read_json(run / "status.json")
            if not isinstance(state, dict) or state.get("status") != "running":
                raise ValueError("visit_not_running")
            if not (run / "stop").exists():
                write_status(run / "stop", dict(at=time.time(), elapsed=time.perf_counter(),
                                               declaration="driver_left"))
        finally:
            request_lock.close()
        return
    directory = run / "requests"
    command_id = uuid4().hex
    path = directory / f"{command_id}.json"
    write_status(path, dict(action=action, at=time.time(), **fields))
    return dict(command_id=command_id, state="requested", request_path=str(path),
                receipt_path=str(run / "receipts" / path.name))


def presence_status(clock):
    """Project one declared clock state; process completion never implies departure."""
    snapshot = clock.snapshot()
    presence = snapshot["presence"]
    return dict(clock=snapshot, arrival_confirmed=True, presence=presence,
                declared_presence={"declared_present": True, "declared_absent": False}.get(
                    presence))


def run_visit(root, *, intent, present, archive=None, plan=None, fixture=None, max_seconds=7200,
              enable_audio=False, phrase=None, selection=None, loss_phrase=None,
              recovery_phrase=None, separate_operations=False, chief_phrase=None, arrival=None):
    if recovery_phrase is not None and (loss_phrase is None or not enable_audio):
        raise ValueError("recovery_phrase_requires_loss_audio")
    if loss_phrase is not None and not enable_audio:
        raise ValueError("loss_phrase_requires_audio")
    if present is not True:
        raise ValueError("explicit_presence_required")
    if arrival is not None and (fixture is not None or archive is None):
        raise ArrivalError("arrival_requires_live_archive")
    if enable_audio and (fixture is not None or archive is None
                         or phrase is None or selection is None):
        raise ValueError("audio_requires_live_archive_phrase_and_selection")
    if separate_operations and (fixture is not None or archive is None or plan is None
                                or not 0 < max_seconds <= 7200):
        raise ValueError("operations_service_requires_live_archive_plan_and_bounded_visit")
    if chief_phrase is not None and not (separate_operations and enable_audio):
        raise ValueError("chief_phrase_requires_operations_audio")
    if separate_operations and enable_audio and chief_phrase is None:
        raise ValueError("operations_audio_requires_chief_phrase")
    lock = SourceLock(root / "visit.lock")
    run = root / uuid4().hex[:10]
    mode = "replay" if fixture else "live"
    clock = None
    arrival_binding = None
    try:
        host_at, start = datetime.now(UTC), time.perf_counter()
        clock_at, clock_start = host_at, start
        if mode == "live" and arrival is None:
            arrival = pending_arrival(root)
        if arrival is not None:
            clock_at, clock_start, arrival_binding = claim_arrival(
                root, arrival, at=host_at, elapsed=start, run=run)
        run.mkdir(parents=True, exist_ok=False)
        (run / "requests").mkdir()
        (run / "receipts").mkdir()
        clock = VisitClock(run / "clock", presence_confirmed=True,
                           at=clock_at, elapsed=clock_start)
        if arrival_binding:
            clock.update("unknown", at=host_at, elapsed=start)
        journal = Journal(run / "supervision.sqlite3")
    except BaseException:
        try:
            if clock:
                clock.close()
        finally:
            lock.close()
        raise

    def source_diagnostic(event):
        # Journal owns the durable schema version; source callbacks also carry
        # one for standalone consumers.
        journal.append({key: value for key, value in event.items() if key != "schema_version"})

    supervisor = Supervisor(plan, journal) if plan and not separate_operations else None
    gate = CaptureGate(plan.context) if plan else None
    activity_observer = ActivityEvidence()
    radio = None
    operations = None
    operations_issue = None
    process = None
    attempts = CaptureAttempts(run, root, fixture=fixture, popen=subprocess.Popen,
                               probe=probe_process, clock=lambda: time.time(),
                               elapsed=lambda: time.perf_counter())
    storage_probe = (RecordingStorage(clock=lambda: time.time(),
                                      elapsed=lambda: time.perf_counter())
                     if fixture is None else None)
    result = dict(schema_version=1, mode=mode, status="starting", intent=intent,
                  **presence_status(clock), audio="disabled", ready_to_drive=False,
                  archive=str(archive) if archive else None, pid=os.getpid())
    if arrival_binding:
        result["arrival"] = arrival_binding
    result["operations_mode"] = "service" if separate_operations else "embedded"
    processed = set()
    departure = False
    departure_boundary = None
    problem = None
    worker_code = None
    outcome = None
    phase = "waiting_for_source"
    last_observed = None
    last_storage_state = None
    try:
        write_status(run / "host-process.json", dict(schema_version=1, at=time.time(),
            process=process_snapshot(os.getpid()), source_release=active_binding()))
        if separate_operations:
            write_status(run / "heartbeat.json", dict(schema_version=1, at=time.time()))
            write_status(run / "status.json", dict(result, updated_at=time.time()))
            result["audio"] = "service_enabled_muted" if enable_audio else "disabled"
            try:
                operations = OperationsClient(
                    run, archive=archive, plan=plan, duration=max_seconds,
                    enable_audio=enable_audio,
                    phrase=phrase, selection=selection, loss_phrase=loss_phrase,
                    recovery_phrase=recovery_phrase, chief_phrase=chief_phrase)
            except Exception:
                operations_issue = "operations_launch_unconfirmed"
                (run / "operations-launch-failure.txt").write_text(
                    traceback.format_exc(), encoding="utf-8")
                result["operations"] = dict(health="degraded", reason=operations_issue, state=None)
        elif enable_audio:
            radio = VisitAudio(run, supervisor, archive=archive, phrase=phrase, selection=selection,
                               loss_phrase=loss_phrase, recovery_phrase=recovery_phrase)
            result.update(audio="enabled_muted", radio=radio.snapshot())
        print(f"Driver Visit ({mode}): {run.resolve()}", flush=True)
        while True:
            now = time.time()
            elapsed = time.perf_counter()
            if (run / "stop").exists():
                departure = True
                departure_boundary = read_json(run / "stop")
                break
            if elapsed - start >= max_seconds:
                problem = "visit_time_limit"
                break
            if radio:
                result["radio"] = radio.tick()
            if operations:
                operation = operations.poll()
                operations.sync_receipts()
                result["operations"] = operation
                if operation.get("launcher_exit") is not None or operation.get("binding_changed"):
                    operations_issue = operations_issue or "operations_interrupted"
                if operation["health"] == "healthy":
                    result["radio"] = operation["state"].get("radio")
                else:
                    result["radio"] = dict(delivery_health="degraded", muted=None,
                                           closed=False, reason=operation["reason"])
            if last_observed is not None and elapsed - last_observed < 0.2:
                time.sleep(0.05)
                continue
            last_observed = elapsed
            write_status(run / "heartbeat.json", dict(schema_version=1, at=now))
            identity, status = None, None
            if archive:
                # Journal transient reads as well as terminal errors. Read-time
                # retries cannot make a newly published source look future-dated.
                identity, status, now, elapsed = archive_observation(archive, source_diagnostic)
            activity_source = activity_observer.observe(
                status, source_id=identity, now=now, elapsed=elapsed)
            activity = activity_source["activity"]
            if not update_presence(run, clock, activity, now=now, elapsed=elapsed,
                                   source_evidence=activity_source):
                continue
            # Supervisor requires an identity even for a failed observation; the absent
            # status forces disarm. CaptureGate receives None and cannot admit this label.
            supervision = supervisor.observe(status, source_id=identity or "unavailable", now=now,
                                             elapsed=elapsed - start) if supervisor else None
            if operations:
                supervision = operations_supervision(operation, status, source_id=identity)
            elif separate_operations:
                supervision = dict(supervision_health="degraded", fuel_state="unknown",
                                   reason=operations_issue)
            if gate and isinstance(supervision, dict) and supervision.get("plan"):
                # Only a current bound operational status changes this diagnostic;
                # it never restarts capture or grants fuel approval.
                current_plan = FuelPlan.from_dict(dict(schema_version=1, **supervision["plan"]))
                gate.expected_context = current_plan.context
            source_gate = gate.observe(status, source_id=identity, now=now,
                                       elapsed=elapsed - start) if gate else None
            storage = storage_probe.poll(run) if storage_probe else None
            if storage is not None:
                state = storage["state"], storage["reason"]
                if state != last_storage_state:
                    last_storage_state = state
                    try:
                        journal.append(dict(event="recording_storage_transition", at=storage["at"],
                                            recording_storage=storage))
                    except (OSError, sqlite3.Error) as exc:
                        storage["diagnostic_error"] = type(exc).__name__
            attempts.step(source_ready=bool(fixture or source_gate and source_gate["ready"]),
                          now=now, elapsed=elapsed, allow_recovery=fixture is None,
                          source_expires_at=source_gate.get("expires_at") if source_gate else None,
                          storage=storage)
            if process is not attempts.process:
                phase = "starting_capture"
            process = attempts.process
            if process is None:
                phase = {"capture_lock_unavailable": "waiting_for_capture_lock",
                         "capture_storage_unavailable": "waiting_for_capture_storage"}.get(
                             attempts.state, "waiting_for_source")
            for path in sorted((run / "requests").glob("*.json")):
                if path.name in processed:
                    continue
                command_request = read_json(path)
                if (isinstance(command_request, dict)
                        and type(command_request.get("at")) in (int, float)
                        and command_request["at"] > now):
                    continue  # Next observation; do not attribute a reason before it was declared.
                accepted, reason = False, "invalid_request"
                receipt_fields = {}
                if isinstance(command_request, dict):
                    if command_request.get("action") == "reason":
                        value = command_request.get("reason")
                        if value in REASONS:
                            clock.annotate(value, at=datetime.fromtimestamp(now, UTC),
                                           elapsed=elapsed,
                                           activity=activity, source_evidence=activity_source)
                            accepted, reason = True, None
                    elif command_request.get("action") == "plan" and separate_operations:
                        try:
                            if operations is None:
                                raise ValueError("operations_unavailable")
                            operations.forward_plan(path.name, command_request)
                            processed.add(path.name)
                            continue
                        except (ValueError, KeyError, TypeError, OSError):
                            reason = "operations_plan_not_forwarded"
                    elif command_request.get("action") == "plan" and supervisor:
                        try:
                            receipt_fields = supervisor.change_plan(
                                plan=FuelPlan.from_dict(command_request["plan"]),
                                expected_plan_id=command_request["expected_plan_id"],
                                requested_at=command_request["at"], now=time.time(),
                                note=command_request["note"])
                            accepted, reason = receipt_fields.pop("accepted"), receipt_fields.pop(
                                "reason")
                            supervision = supervisor.last_result
                            if accepted and gate:
                                gate.expected_context = supervisor.plan.context
                        except (KeyError, ValueError, TypeError):
                            reason = "invalid_plan_request"
                    elif command_request.get("action") == "fuel" and separate_operations:
                        try:
                            if operations is None:
                                raise ValueError("operations_unavailable")
                            operations.forward_fuel(path.name, command_request)
                            processed.add(path.name)
                            continue  # Only the service's bound receipt proves acceptance.
                        except (ValueError, KeyError, TypeError, OSError):
                            reason = "operations_approval_not_forwarded"
                    elif command_request.get("action") == "fuel" and supervisor:
                        try:
                            accepted = supervisor.arm(
                                cockpit_litres=command_request["cockpit_litres"],
                                verified_at=command_request["verified_at"], now=time.time(),
                                source_id=command_request["source_id"],
                                solo_player_confirmed=command_request["solo_player_confirmed"],
                                player_view_confirmed=command_request.get(
                                    "player_view_confirmed", False),
                                participant_index=command_request.get("participant_index"),
                                note=command_request["note"],
                            )
                            reason = supervisor.approval_result["reason"]
                            supervision = dict(
                                supervision, approval=supervisor.approval_result,
                                fuel_state=supervisor.fuel_state,
                                supervision_loss=supervisor.supervision_loss,
                            )
                        except (KeyError, ValueError, TypeError):
                            pass
                write_status(run / "receipts" / path.name,
                             dict(accepted=accepted, reason=reason, at=time.time(),
                                  **receipt_fields))
                processed.add(path.name)
            worker = read_json(attempts.directory / "status.json", diagnostic=source_diagnostic)
            worker_code = process.poll() if process else None
            if process:
                if worker_code is not None:
                    phase = "capture_closed" if worker_code == 0 else "capture_failed"
                elif (isinstance(worker, dict)
                      and worker.get("capture_phase") == "waiting_for_source"):
                    phase = "waiting_for_source"
                elif isinstance(worker, dict) and worker.get("active_ticks", 0) > 0:
                    phase = "recording" if worker.get("intake_active") else "capture_closing"
            checked_at, checked_elapsed = time.time(), time.perf_counter()
            clock_coherent = (checked_at >= now and checked_elapsed >= elapsed
                              and abs((checked_at - now) - (checked_elapsed - elapsed)) <= .5)
            readiness = capture_readiness(worker, source_gate if clock_coherent else None,
                                          now=checked_at, process_alive=worker_code is None
                                          and process is not None and clock_coherent,
                                          storage=storage, elapsed=checked_elapsed)
            if readiness["capture_ready"]:
                attempts.observe_ready(now=checked_at, elapsed=checked_elapsed)
            if radio:
                result["radio"] = radio.tick()
            result.update(status="running", phase=phase, updated_at=now,
                          current_worker=attempts.directory.name, recovery=attempts.snapshot(),
                          worker_pid=process.pid if process else None, source=source_gate,
                          worker=worker, worker_exit=worker_code, supervision=supervision,
                          recording_storage=storage,
                          **presence_status(clock), activity_source=activity_source, **readiness)
            write_status(run / "status.json", result)
            if fixture and worker_code is not None:
                departure = True  # Explicit test end, never a real presence inference.
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        problem = "coordinator_interrupted"
    except Exception as exc:
        problem = type(exc).__name__
        (run / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        process = attempts.process
        if radio:
            result["radio"] = radio.close()
        # Seat time ends before potentially lengthy artifact closure or analysis.
        try:
            if departure:
                if fixture:
                    clock.end(at=datetime.now(UTC), elapsed=time.perf_counter())
                else:
                    boundary = departure_boundary
                    if (not isinstance(boundary, dict)
                            or boundary.get("declaration") != "driver_left"
                            or type(boundary.get("elapsed")) not in (int, float)
                            or not clock.elapsed <= boundary["elapsed"] <= time.perf_counter()
                            or type(boundary.get("at")) not in (int, float)
                            or not time.time() - boundary["at"] >= 0):
                        raise ValueError("invalid_departure_boundary")
                    clock.end(at=datetime.fromtimestamp(boundary["at"], UTC),
                              elapsed=boundary["elapsed"])
            else:
                clock.close()
        except Exception as exc:
            problem = problem or f"clock_close_{type(exc).__name__}"
            clock.close()
        finally:
            (run / "stop").touch(exist_ok=True)
        result.update(status="closing", updated_at=time.time(), capture_ready=False,
                      capture_expires_at=None,
                      ready_to_drive=False, **presence_status(clock))
        write_status(run / "status.json", result)
        if operations:
            deadline = time.perf_counter() + 10
            while not operations.terminal() and time.perf_counter() < deadline:
                if operations.process and operations.process.poll() is not None:
                    break
                time.sleep(.1)
            operations.sync_receipts()
            result["operations"] = operations.poll()
            if not operations.terminal():
                problem = problem or "operations_stop_unconfirmed"
            operation_state = operations.last.get("state") or {}
            result["radio"] = operation_state.get("radio", result.get("radio"))
        if process is not None:
            deadline = time.perf_counter() + 30
            while process.poll() is None and time.perf_counter() < deadline:
                if radio and not radio.snapshot()["closed"]:
                    result["radio"] = radio.close()
                time.sleep(0.2)
            worker_code = process.poll()
            if worker_code is None:
                problem = problem or "worker_stop_unconfirmed"
        attempts.observe_exit(now=time.time(), elapsed=time.perf_counter())
        final_worker = read_json(attempts.directory / "status.json")
        if (fixture is None and process is not None and worker_code is not None
                and not worker_stopped_for_departure(final_worker)):
            # Departure can race the first observation of an earlier clean exit.
            # A finalized prefix stopped by lease expiry is not a complete visit.
            attempts.interrupted = True
        if attempts.interrupted and fixture is None:
            problem = problem or "capture_interrupted"
        problem = problem or operations_issue
        closure = read_json(attempts.directory / "closure.json")
        completed = departure and problem is None and worker_code == 0 and closure_success(closure)
        result.update(status="complete" if completed else "incomplete", problem=problem,
                      current_worker=attempts.directory.name, recovery=attempts.snapshot(),
                      updated_at=time.time(), worker_exit=worker_code, closure=closure,
                      **presence_status(clock), capture_ready=False, capture_expires_at=None,
                      ready_to_drive=False)
        write_status(run / "status.json", result)
        journal.close()
        attempts.close_streams()
        if operations:
            operations.close_streams()
        try:
            boundary_elapsed = (departure_boundary["elapsed"]
                                if clock.status == "complete" and departure_boundary else None)
            outcome = publish_outcome(run, departure_elapsed=boundary_elapsed)
        finally:
            lock.close()
    print(json.dumps(dict(run=str(run.resolve()), status=result["status"], problem=problem,
                          outcome=outcome)))
    return 0 if result["status"] == "complete" and outcome["status"] == "written" else 1


def main(argv=None):
    from simcoach.live.visit_release import require_guarded_entry
    require_guarded_entry()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    arrive = commands.add_parser("arrive", help="Record seated arrival before host startup")
    arrive.add_argument("--present", action="store_true", required=True)
    arrive.add_argument("--out", type=Path, default=Path(".scratch/v"))
    note = commands.add_parser("note-before-host", help="Declare why the driver is waiting")
    note.add_argument("arrival_id")
    note.add_argument("--reason", required=True, choices=REASONS)
    note.add_argument("--out", type=Path, default=Path(".scratch/v"))
    depart = commands.add_parser("depart-before-host",
                                 help="Record explicit departure from an unclaimed arrival")
    depart.add_argument("arrival_id")
    depart.add_argument("--left", action="store_true", required=True)
    depart.add_argument("--out", type=Path, default=Path(".scratch/v"))
    abandon = commands.add_parser("abandon-before-host",
                                  help="Retire an unresolved arrival with no departure inference")
    abandon.add_argument("arrival_id")
    abandon.add_argument("--confirm", action="store_true", required=True)
    abandon.add_argument("--out", type=Path, default=Path(".scratch/v"))
    start = commands.add_parser("start")
    start.add_argument("--present", action="store_true", required=True)
    start.add_argument("--intent", required=True)
    source = start.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--fixture", type=Path, help="Synthetic replay; no real visit")
    start.add_argument("--plan", type=Path)
    start.add_argument("--out", type=Path, default=Path(".scratch/v"))
    start.add_argument("--arrival", help="Earlier seated arrival ID under the same --out")
    start.add_argument("--max-seconds", type=float, default=7200)
    start.add_argument("--enable-audio", action="store_true")
    start.add_argument("--operations-service", action="store_true",
                       help="Use the independent operational owner; no embedded fuel/audio")
    start.add_argument("--chief-phrase", type=Path,
                       help="Reviewed chief-connection-loss phrase for service audio")
    start.add_argument("--phrase", type=Path, help="Reviewed pre-rendered fuel phrase directory")
    start.add_argument("--loss-phrase", type=Path,
                       help="Reviewed 'Fuel monitoring unavailable.' render directory")
    start.add_argument("--recovery-phrase", type=Path,
                       help="Reviewed 'Fuel monitoring restored.' render directory")
    start.add_argument("--audio-selection", type=Path, help="Explicit output selection JSON")
    stop = commands.add_parser("stop")
    stop.add_argument("run", type=Path)
    checkpoint = commands.add_parser("checkpoint", help="Review a stint while the visit continues")
    checkpoint.add_argument("run", type=Path)
    inspect = commands.add_parser("inspect", help="Read-only recovery evidence; never restarts")
    inspect.add_argument("run", type=Path)
    outcome = commands.add_parser("outcome",
                                  help="Write a new outcome snapshot; preserve old reports")
    outcome.add_argument("run", type=Path)
    outcome.add_argument("--out", type=Path)
    review = commands.add_parser("review", help="Record explicit output feedback privately")
    review.add_argument("run", type=Path)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--verdict", choices=("useful", "insufficient", "misleading", "failed"),
                        required=True)
    review.add_argument("--note", required=True)
    review.add_argument("--reviewer", choices=("chief", "driver"), default="chief")
    review.add_argument("--driver-feedback-confirmed", action="store_true")
    reason = commands.add_parser("reason")
    reason.add_argument("run", type=Path)
    reason.add_argument("reason", choices=REASONS)
    fuel = commands.add_parser("fuel")
    fuel.add_argument("run", type=Path)
    fuel.add_argument("--cockpit-litres", type=float, required=True)
    fuel.add_argument("--verified-at", required=True)
    confirmation = fuel.add_mutually_exclusive_group(required=True)
    confirmation.add_argument("--confirm-solo-player", action="store_true")
    confirmation.add_argument("--confirm-player-view", action="store_true")
    fuel.add_argument("--participant-index", type=int)
    fuel.add_argument("--note", required=True)
    check = commands.add_parser("fuel-check", help="Check fuel, radio and recording at pit exit")
    check.add_argument("run", type=Path)
    check.add_argument("--plan", type=Path, required=True)
    check.add_argument("--audio-selection", type=Path, required=True)
    change_plan = commands.add_parser("plan", help="Replace the fuel plan; requires new approval")
    change_plan.add_argument("run", type=Path)
    change_plan.add_argument("--plan", type=Path, required=True)
    change_plan.add_argument("--expected-plan-id", required=True)
    change_plan.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "inspect":
            print(json.dumps(inspect_visit(args.run), indent=2))
        elif args.action == "arrive":
            print(json.dumps(declare_arrival(args.out, present=args.present)))
        elif args.action == "note-before-host":
            print(json.dumps(note_before_host(args.out, args.arrival_id, reason=args.reason)))
        elif args.action == "depart-before-host":
            print(json.dumps(depart_before_host(args.out, args.arrival_id, left=args.left)))
        elif args.action == "abandon-before-host":
            retired = abandon_before_host(args.out, args.arrival_id, confirmed=args.confirm)
            print(json.dumps(retired))
        elif args.action == "outcome":
            written = write_visit_outcome(args.run, args.out)
            print(json.dumps(dict(report_path=written["report_path"])))
        elif args.action == "review":
            written = record_review(args.run, output=args.output, verdict=args.verdict,
                                    note=args.note, reviewer=args.reviewer,
                                    driver_feedback_confirmed=args.driver_feedback_confirmed)
            print(json.dumps(dict(review_path=written["path"])))
        elif args.action == "stop":
            request(args.run, "stop")
        elif args.action == "checkpoint":
            from simcoach.live.stint_checkpoint import request_checkpoint
            print(json.dumps(request_checkpoint(args.run), indent=2))
        elif args.action == "reason":
            request(args.run, "reason", reason=args.reason)
        elif args.action == "plan":
            replacement = FuelPlan.from_dict(read_json(args.plan))
            print(json.dumps(request(
                args.run, "plan", plan=dict(schema_version=1, **asdict(replacement)),
                expected_plan_id=args.expected_plan_id, note=args.note)))
        elif args.action == "fuel-check":
            from simcoach.live.fuel_release import check_visit_fuel
            decision = check_visit_fuel(args.run, args.plan, args.audio_selection)
            print(json.dumps(decision))
            return 0 if decision["decision"] == "GO" else 2
        elif args.action == "fuel":
            if args.confirm_player_view != (args.participant_index is not None):
                parser.error("--confirm-player-view requires --participant-index together")
            state = read_json(args.run / "status.json")
            archive = Path(state["archive"])
            request(args.run, "fuel", cockpit_litres=args.cockpit_litres,
                    verified_at=timestamp(args.verified_at), source_id=source_identity(archive),
                    solo_player_confirmed=args.confirm_solo_player, note=args.note,
                    player_view_confirmed=args.confirm_player_view,
                    participant_index=args.participant_index)
        else:
            if not math.isfinite(args.max_seconds) or not 0 < args.max_seconds <= 86400:
                parser.error("--max-seconds must be positive and at most one day")
            if args.archive and not args.plan:
                parser.error("--archive requires --plan")
            audio_options = (args.enable_audio, args.phrase is not None,
                             args.audio_selection is not None)
            if any(audio_options) and (not all(audio_options) or args.fixture):
                parser.error("Audio requires --enable-audio, --phrase, "
                             "--audio-selection and --archive")
            if args.loss_phrase and not args.enable_audio:
                parser.error("--loss-phrase requires --enable-audio")
            if args.recovery_phrase and not (args.loss_phrase and args.enable_audio):
                parser.error("--recovery-phrase requires --loss-phrase and --enable-audio")
            plan = FuelPlan.from_dict(read_json(args.plan)) if args.plan else None
            return run_visit(args.out.resolve(), intent=args.intent, present=args.present,
                             archive=args.archive.resolve() if args.archive else None, plan=plan,
                             fixture=args.fixture.resolve() if args.fixture else None,
                             max_seconds=args.max_seconds, enable_audio=args.enable_audio,
                             phrase=args.phrase, selection=args.audio_selection,
                             loss_phrase=args.loss_phrase, recovery_phrase=args.recovery_phrase,
                             separate_operations=args.operations_service,
                             chief_phrase=args.chief_phrase, arrival=args.arrival)
    except ArrivalError as exc:
        parser.error(f"Arrival request refused: {exc}")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.error(f"Visit request refused: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
