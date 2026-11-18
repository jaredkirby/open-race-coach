"""Explicit, bounded local supervision/radio service; never controls capture."""
from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import ExitStack, suppress
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.fuel_radio import ArchiveStatusSource
from simcoach.live.supervisor import FuelPlan, Journal, Supervisor, number, strict_json
from simcoach.live.visit_audio import VisitAudio
from simcoach.live.visit_recovery import _artifact, _confined, inspect_visit, probe_process
from simcoach.live.visit_release import active_binding
from simcoach.live.visit_watch import VisitLiveness


def _stop_request(root, now):
    path = root / "stop"
    if not path.exists():
        return None
    try:
        if not _confined(root, path):
            raise ValueError("redirected_stop")
        value = strict_json(path.read_text("utf-8"))
        at = number(value.get("at"))
        if value.get("declaration") != "driver_left" or at > now:
            raise ValueError("invalid_departure")
        return "driver_left"
    except (OSError, ValueError, AttributeError):
        return "stop_unverified"


class ChiefLink:
    """Pin a coordinator; preserve one fixed loss occurrence per connected epoch."""

    def __init__(self, visit, inspect, clock, journal):
        self.visit, self.inspect, self.clock, self.journal = visit, inspect, clock, journal
        self.policy = VisitLiveness()
        self.connected = False
        self.loss = None
        self.value = None
        self.previous = None

    def observe(self):
        report = None
        try:
            report = self.inspect(self.visit, clock=self.clock)
            result = self.policy.observe(report)
        except (OSError, ValueError):
            self.policy.heartbeat_at = None
            self.policy.responsive = False
            result = dict(state="unknown", reason="inspection_failed")
        now = number(self.clock())
        if result["state"] == "responsive":
            self.connected = True
            self.loss = None
        elif self.connected and self.loss is None:
            self.loss = dict(loss_id=uuid4().hex, created_at=now)
        state = "connected" if result["state"] == "responsive" else (
            "lost" if self.loss else "awaiting_connection")
        self.value = dict(state=state, at=now, pinned_host=self.policy.identity,
                          liveness=result, **(self.loss or {}))
        key = (state, result["state"], result["reason"])
        if key != self.previous:
            self.journal.append(dict(event="chief_link", at=now, link=self.value,
                                     inspection=report))
            self.previous = key
        return self.value

    def loss_valid(self):
        """Fresh independent evidence immediately before/during native output."""
        if not self.value or self.value["state"] != "lost":
            return False
        report = self.inspect(self.visit, clock=self.clock)
        host = report["host"]
        recorded = host.get("recorded_process") or {}
        same = {key: recorded.get(key) for key in ("pid", "created_100ns")}
        if (same != self.policy.identity or self.policy.binding_changed
                or report["reported_visit_status"] in {"closing", "complete", "incomplete"}
                or _stop_request(self.visit, self.clock())):
            return False
        return bool(host["state"] in {"absent", "exited"}
                    or host["state"] == "running" and host["identity_status"] == "matched"
                    and not report["heartbeat"]["fresh"])


def run_service(visit, out, *, archive, plan, present=False, duration=600, interval=.1,
                enable_audio=False, phrase=None, selection=None, loss_phrase=None,
                recovery_phrase=None, chief_phrase=None, transport_factory=None,
                source=None, inspect=inspect_visit, clock=time.time,
                elapsed=time.perf_counter, sleep=time.sleep, visit_controls=False):
    """New instance, initially muted/unarmed; no automatic resume or launch.

    Source/process/transport injection is for offline tests. Native output still
    requires explicit enable_audio and configured phrases/selected endpoint.
    """
    duration = number(duration, minimum=.1, maximum=7200)
    interval = number(interval, minimum=.05, maximum=.25)
    if present is not True:
        raise ValueError("explicit_presence_required")
    if not isinstance(plan, FuelPlan):
        raise ValueError("fuel_plan_required")
    if enable_audio and (phrase is None or selection is None or chief_phrase is None):
        raise ValueError("audio_requires_phrase_selection_and_chief_warning")
    if not enable_audio and any(
            p is not None for p in (loss_phrase, recovery_phrase, chief_phrase)):
        raise ValueError("warning_packages_require_audio")
    visit, out = Path(visit).resolve(strict=True), Path(out).resolve()
    if (out == visit or out in visit.parents
            or visit in out.parents and out != visit / "operations"):
        raise ValueError("service_output_must_be_separate")
    initial = inspect(visit, clock=clock)
    _, host_status = _artifact(visit, "status.json")
    if (not host_status or host_status.get("declared_presence") is not True
            or initial["host"]["state"] != "running"
            or initial["host"]["identity_status"] != "matched"
            or initial["reported_visit_status"] not in {"starting", "running"}
            or not initial["heartbeat"]["fresh"] or (visit / "stop").exists()):
        raise ValueError("fresh_declared_visit_required")
    lock = SourceLock(visit / "operations-service.lock")
    journal = audio = None
    result = None
    service_id = uuid4().hex
    started = number(elapsed())
    reason = "duration_elapsed"
    try:
        out.mkdir(parents=True, exist_ok=False)
        (out / "requests").mkdir()
        (out / "receipts").mkdir()
        journal = Journal(out / "supervision.sqlite3")
        supervisor = Supervisor(plan, journal)
        link = ChiefLink(visit, inspect, clock, journal)
        link.policy.observe(initial)
        injected = dict(source=source is not None, inspector=inspect is not inspect_visit,
                        transport=transport_factory is not None)
        source = source or ArchiveStatusSource(archive)
        write_status(out / "run.json", dict(
            schema_version=1, service_id=service_id, visit=str(visit), archive=str(archive),
            process=probe_process(os.getpid()), pinned_host=link.policy.identity,
            source_release=active_binding(),
            at=clock(), duration_s=duration, interval_s=interval,
            plan=asdict(plan), audio_enabled=enable_audio, visit_controls=visit_controls,
            injected_dependencies=injected,
            mode="injected" if any(injected.values()) else "local_operations"))
        if enable_audio:
            def output_permitted():
                stop = _stop_request(out, clock()) or _stop_request(visit, clock())
                return not stop and 0 <= elapsed() - started < duration

            audio = VisitAudio(out, supervisor, archive=archive, phrase=phrase, selection=selection,
                               clock=clock, factory=transport_factory, loss_phrase=loss_phrase,
                               recovery_phrase=recovery_phrase, chief_phrase=chief_phrase,
                               chief_loss_valid=lambda: not _stop_request(out, clock())
                               and link.loss_valid(), source_reader=source,
                               output_permitted=output_permitted,
                               controls_root=visit if visit_controls else out)
        def observe_source():
            try:
                identity, status = source()
            except (OSError, ValueError):
                identity, status = "unavailable", None
            now, monotonic = number(clock()), number(elapsed())
            result = supervisor.observe(status, source_id=identity, now=now,
                                        elapsed=monotonic - started)
            return result, now, monotonic

        def decision_time(requested_at, observed_at, observed_elapsed):
            stop = _stop_request(out, clock()) or _stop_request(visit, clock())
            if stop:
                raise ValueError(stop)
            now, monotonic = number(clock()), number(elapsed())
            since_read, elapsed_since_read = now - observed_at, monotonic - observed_elapsed
            if (min(since_read, elapsed_since_read) < 0
                    or abs(since_read - elapsed_since_read) > .5):
                raise ValueError("request_clock_changed")
            if monotonic - started >= duration:
                raise ValueError("service_duration_elapsed")
            if not 0 <= now - requested_at < 3:
                raise ValueError("stale_request")
            heartbeat = link.policy.heartbeat_at
            if heartbeat is None or not 0 <= now - heartbeat < 3:
                raise ValueError("chief_not_connected")
            return now

        processed = set()
        while True:
            now, monotonic = number(clock()), number(elapsed())
            if monotonic < started:
                raise ValueError("service_clock_regressed")
            stop = _stop_request(out, now) or _stop_request(visit, now)
            if stop or monotonic - started >= duration:
                reason = stop or "duration_elapsed"
                break
            connection = link.observe()
            if connection["liveness"]["state"] in {"closing_reported", "terminal_reported"}:
                reason = "chief_closure_reported"
                break
            supervision, observed_at, observed_elapsed = observe_source()
            for path in sorted((out / "requests").glob("*.json"))[:1024]:
                if path.name in processed:
                    continue
                artifact, command = _artifact(out, "requests/" + path.name)
                accepted, refusal = False, "invalid_request"
                decision_at = None
                try:
                    if not command or command.get("service_id") != service_id:
                        raise ValueError("service_binding_mismatch")
                    at = number(command.get("at"))
                    if not 0 <= clock() - at < 3:
                        raise ValueError("stale_request")
                    # New intent requires a fresh verified coordinator connection.
                    if link.observe()["state"] != "connected":
                        raise ValueError("chief_not_connected")
                    action = command.get("action")
                    if action == "plan":
                        plan_update = FuelPlan.from_dict(command["plan"])
                    elif action == "fuel":
                        approval = command["approval"]
                        if not isinstance(approval, dict):
                            raise ValueError("invalid_request")
                        supervision, observed_at, observed_elapsed = observe_source()
                    else:
                        raise ValueError("unsupported_request")
                    with ExitStack() as departure_locks:
                        try:
                            departure_locks.callback(SourceLock(visit / "request.lock").close)
                            departure_locks.callback(SourceLock(out / "request.lock").close)
                        except RuntimeError:
                            continue  # Leave the request pending for the next observation.
                        decision_at = decision_time(at, observed_at, observed_elapsed)
                        if action == "plan":
                            change = supervisor.change_plan(
                                plan=plan_update, expected_plan_id=command["expected_plan_id"],
                                requested_at=at, now=decision_at, note=command["note"])
                            accepted, refusal = change["accepted"], change["reason"]
                            supervision = supervisor.last_result
                        else:
                            if supervisor.preapproval_anchor is not None:
                                # Keep the original request and cockpit timestamps. A short
                                # contested copy can settle within the existing request TTL.
                                continue
                            accepted = supervisor.arm(now=decision_at, **approval)
                            refusal = supervisor.approval_result["reason"]
                except (ValueError, TypeError, KeyError) as error:
                    refusal = str(error) if isinstance(error, ValueError) else "invalid_request"
                journal.append(dict(event="service_request", at=clock(), path=path.name,
                                     sha256=artifact["sha256"], accepted=accepted, reason=refusal,
                                     decision_at=decision_at))
                write_status(out / "receipts" / path.name, dict(
                    schema_version=1, service_id=service_id, at=clock(), accepted=accepted,
                    applied=accepted, reason=refusal, request_sha256=artifact["sha256"],
                    decision_at=decision_at,
                    action=command.get("action") if command else None,
                    plan_id=supervisor.plan_status()["plan_id"]))
                processed.add(path.name)
            connection = link.value
            if audio:
                if audio.radio:
                    audio.radio.update_chief_link(connection)
                radio = audio.tick(allow_unmute=connection["state"] == "connected")
            else:
                radio = dict(delivery_health="disabled", muted=True, closed=True)
            current = supervisor.current
            observed_source = None
            if current is not None:
                observed_source = dict(
                    source_id=current.source_id, at=current.at, sequence=current.sequence,
                    participant_index=current.participant.index if current.participant else None,
                    context=asdict(current.context),
                )
                if current.continuity_generation is not None:
                    observed_source["continuity_generation"] = current.continuity_generation
            result = dict(schema_version=1, service_id=service_id, status="running",
                          observed_source=observed_source,
                          updated_at=clock(), declared_presence=True, ready_to_drive=False,
                          chief_link=connection, supervision=dict(
                              supervision, fuel_state=supervisor.fuel_state,
                              approval=supervisor.approval_result), radio=radio,
                          driver_departure_inferred=False, capture_health_verified=False)
            write_status(out / "heartbeat.json", dict(schema_version=1, at=clock()))
            write_status(out / "status.json", result)
            sleep(interval)
        radio = audio.close() if audio else dict(closed=True, delivery_health="disabled")
        complete = radio.get("closed") is True
        journal.append(dict(event="service_stopped", at=clock(), reason=reason,
                            audio_closed=complete))
        result = dict(result or {}, schema_version=1, service_id=service_id,
                      status="complete" if complete else "incomplete", updated_at=clock(),
                      stop_reason=reason, radio=radio, ready_to_drive=False,
                      driver_departure_inferred=False,
                      declared_presence=reason != "driver_left")
        write_status(out / "status.json", result)
        return result
    except BaseException as error:
        if audio:
            audio.close()
        if journal:
            # Preserve the original failure and all prior artifacts.
            with suppress(Exception):
                journal.append(dict(event="service_failed", at=clock(),
                                    error_type=type(error).__name__))
        raise
    finally:
        if journal:
            journal.close()
        lock.close()


def main(argv=None):
    from simcoach.live.visit_release import require_guarded_entry
    require_guarded_entry()
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("visit", "out", "archive", "plan"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--present", action="store_true")
    parser.add_argument("--visit-controls", action="store_true")
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--enable-audio", action="store_true")
    for name in ("phrase", "selection", "loss-phrase", "recovery-phrase", "chief-phrase"):
        parser.add_argument("--" + name, type=Path)
    args = vars(parser.parse_args(argv))
    args["plan"] = FuelPlan.from_dict(strict_json(args["plan"].read_text("utf-8")))
    result = run_service(**args)
    print(json.dumps(dict(status=result["status"], service_id=result["service_id"],
                         stop_reason=result["stop_reason"])))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
