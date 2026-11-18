"""Independent, read-only visit liveness monitor. Never owns capture or audio."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.supervisor import Journal, number
from simcoach.live.visit_recovery import _created, _pid, inspect_visit, probe_process


class VisitLiveness:
    """Pin one host identity; process evidence and responsiveness stay separate."""

    def __init__(self):
        self.identity = None
        self.binding_changed = False
        self.heartbeat_at = None
        self.responsive = False

    def observe(self, report):
        host = report["host"]
        recorded = host["recorded_process"] or {}
        identity = (dict(pid=recorded["pid"], created_100ns=recorded["created_100ns"])
                    if _pid(recorded.get("pid")) and _created(recorded.get("created_100ns"))
                    else None)
        if self.identity is None:
            self.identity = identity
        elif identity is not None and identity != self.identity:
            self.binding_changed = True
        reported = report["reported_visit_status"]
        beat = report["heartbeat"]
        state, reason = "unknown", "process_evidence_unknown"
        if self.binding_changed:
            state, reason = "binding_changed", "host_identity_replaced"
        elif identity is None:
            reason = "recorded_identity_unavailable"
        elif host["state"] in {"absent", "exited"}:
            state = "terminal_reported" if reported in {"complete", "incomplete"} else "stopped"
            reason = host["reason"] or "recorded_host_exited"
        elif host["state"] == "running" and host["identity_status"] == "matched":
            if reported in {"complete", "incomplete"}:
                state, reason = "terminal_reported", "terminal_status_not_exit_proof"
            elif reported == "closing":
                state, reason = "closing_reported", "closure_not_verified"
            elif not beat["fresh"]:
                state, reason = "unresponsive", "heartbeat_unavailable_or_stale"
            elif self.heartbeat_at is not None and beat["at"] < self.heartbeat_at:
                state, reason = "unresponsive", "heartbeat_regressed"
            elif reported not in {"starting", "running"}:
                reason = "visit_status_unavailable"
            elif (self.heartbeat_at is not None and beat["at"] > self.heartbeat_at
                  or self.responsive and beat["at"] == self.heartbeat_at):
                state, reason = "responsive", None
            else:
                state, reason = "awaiting_progress", "awaiting_advancing_heartbeat"
        self.responsive = state == "responsive"
        self.heartbeat_at = (beat["at"] if beat["fresh"] and identity == self.identity
                             and host["state"] == "running" and host["identity_status"] == "matched"
                             and reported in {"starting", "running"} else None)
        return dict(state=state, reason=reason, pinned_host=self.identity,
                    host=host, heartbeat=beat, reported_visit_status=reported,
                    current_worker=report["current_worker"], worker=report["worker"],
                    worker_launcher=report["worker_launcher"],
                    reported_worker_status=report["reported_worker_status"],
                    launch_ambiguous=report["launch_ambiguous"],
                    capture_health_verified=False, driver_departure_inferred=False,
                    audio_output_attempted=False, actions_taken=[])


def watch_visit(visit: Path, out: Path, *, duration=60, interval=.5,
                inspect=inspect_visit, probe=probe_process, clock=time.time,
                elapsed=time.perf_counter, sleep=time.sleep, publish=write_status):
    """Write only a fresh external evidence directory, never the observed visit.

    Read/probe errors are observations, not evidence of exit. Output failure is
    terminal for this monitor and propagates. It never stops the source process.
    """
    duration = number(duration, minimum=.1, maximum=86400)
    interval = number(interval, minimum=.1, maximum=10)
    visit, out = Path(visit).resolve(), Path(out).resolve()
    if not visit.is_dir():
        raise ValueError("visit_directory_missing")
    if out == visit or visit in out.parents or out in visit.parents:
        raise ValueError("monitor_output_must_be_separate")
    out.mkdir(parents=True, exist_ok=False)
    journal = Journal(out / "events.sqlite3")
    policy = VisitLiveness()
    start = elapsed()
    previous_key = None
    observation_count = incident_count = 0
    last = None
    last_elapsed = None
    try:
        publish(out / "run.json", dict(schema_version=1, visit=str(visit), started_at=clock(),
                                       duration_s=duration, interval_s=interval,
                                       mode="read_only", audio="disabled",
                                       process=probe_process(os.getpid())))
        # Always inspect after the final wait, including an oversleep/resume.
        # Otherwise completion could republish a pre-gap responsive assessment.
        while True:
            observed_elapsed = number(elapsed())
            if observed_elapsed < start or (last_elapsed is not None
                                            and observed_elapsed < last_elapsed):
                raise ValueError("monitor_clock_regressed")
            gap = (observed_elapsed - last_elapsed if last_elapsed is not None else 0)
            if gap > max(3, 2 * interval):
                policy.responsive = False
                policy.heartbeat_at = None
            at = clock()
            report = None
            try:
                report = inspect(visit, probe=probe, clock=clock)
                value = policy.observe(report)
                if gap > max(3, 2 * interval):
                    value.update(state="unknown", reason="monitor_observation_gap",
                                 observation_gap_s=gap)
            except (OSError, ValueError) as error:
                policy.responsive = False
                policy.heartbeat_at = None
                value = dict(state="unknown", reason="inspection_failed",
                             error_type=type(error).__name__, pinned_host=policy.identity,
                             audio_output_attempted=False, actions_taken=[],
                             driver_departure_inferred=False, capture_health_verified=False)
            last_elapsed = observed_elapsed
            observation_count += 1
            key = (value["state"], value["reason"], value.get("reported_visit_status"),
                   value.get("current_worker"), value.get("reported_worker_status"),
                   value.get("error_type"), value.get("launch_ambiguous"))
            key += tuple((value.get(role, {}).get("state"),
                          value.get(role, {}).get("identity_status"),
                          value.get(role, {}).get("reason"))
                         for role in ("host", "worker", "worker_launcher"))
            if key != previous_key:
                incident_count += 1
                path = out / f"observation-{incident_count:05d}.json"
                evidence = dict(schema_version=1, at=at, result=value, inspection=report)
                publish(path, evidence)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                journal.append(dict(event="liveness_transition", at=at, result=value,
                                    evidence=path.name, evidence_sha256=digest))
                previous_key = key
            last = dict(schema_version=1, status="watching", updated_at=clock(),
                        observations=observation_count, transitions=incident_count, **value)
            publish(out / "status.json", last)
            if elapsed() - start >= duration:
                break
            sleep(min(interval, max(0, duration - (elapsed() - start))))
        journal.append(dict(event="monitor_stopped", at=clock(), reason="duration_elapsed",
                            observations=observation_count, transitions=incident_count))
        last.update(status="complete", updated_at=clock())
        publish(out / "status.json", last)
        return last
    except BaseException as error:
        # Preserve original failure even if recording the monitor's own failure
        # also fails. No repair or retry of ambiguous output effects.
        with suppress(Exception):
            journal.append(dict(event="monitor_failed", at=clock(),
                                error_type=type(error).__name__))
        raise
    finally:
        journal.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("visit", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--interval", type=float, default=.5)
    args = parser.parse_args(argv)
    out = args.out or Path(".scratch/visit-watch") / uuid4().hex[:10]
    try:
        result = watch_visit(args.visit, out, duration=args.duration, interval=args.interval)
    except (OSError, ValueError) as error:
        print(json.dumps(dict(result="FAIL", error_type=type(error).__name__, output=str(out))))
        return 1
    print(json.dumps(dict(result="PASS", scope="observation_completed_not_rig_health",
                         state=result["state"], output=str(out.resolve()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
