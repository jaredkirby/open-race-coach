"""One bounded stint finalization job alongside an owned recording worker."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from collections import Counter
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.ingest.session import finalize_recorded_session
from simcoach.live.capture_gate import capture_deadline_current, published_capture_ready
from simcoach.live.lap_timing import summarize_timing
from simcoach.live.visit_recovery import _artifact, _confined, inspect_visit


def publish(path, value):
    write_status(path, value, temporary=path.parent / f".{uuid4().hex}.tmp")


def consistency_debrief(directory, recordings):
    """Describe every lap; never combine contexts or infer a driving cause."""
    groups = []
    text = ["# Stint review", "", "Lap timing sources are retained; no leaderboard claim.",
            "Chief review is required before choosing the next objective.", ""]
    for recording in recordings:
        group = dict(path=recording["path"], validation=recording["validation"],
                     laps=[], statistics=None, invalid_reasons={})
        if recording["validation"] == "PASS":
            path = Path(recording["path"]) / "laps.jsonl"
            laps = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            group["laps"] = [{key: lap.get(key) for key in (
                "lap_id", "lap", "lap_time_s", "lap_time_source", "valid", "invalid_reason")}
                for lap in laps]
            timing = summarize_timing(laps)
            group.update(timing)
            group["statistics"]["valid_timed_laps"] = group["statistics"]["valid_laps"]
            group["invalid_reasons"] = dict(Counter(
                lap.get("invalid_reason") or "unspecified" for lap in laps
                if lap.get("valid") is not True))
            text.append(f"- Recorded Session: {Path(recording['path']).name}")
            text.append(f"  Timing basis: {timing['timing_basis']}; "
                        f"valid timed laps: {group['statistics']['valid_laps']}; "
                        f"total lap records: {len(laps)}.")
            for source, values in timing["timing_groups"].items():
                text.append(f"  {source}: {values['valid_laps']} valid timed laps.")
                if values["best_s"] is not None:
                    text.append(f"  Best {values['best_s']:.3f}s; "
                                f"median {values['median_s']:.3f}s.")
                if values["spread_s"] is not None:
                    text.append(f"  Full valid-lap spread {values['spread_s']:.3f}s; "
                                "slow laps retained.")
            if timing["timing_basis"] in {"mixed", "unknown"}:
                text.append("  Combined pace and consistency withheld: timing sources differ "
                            "or are unknown.")
            text.append(f"  Exclusions: {json.dumps(group['invalid_reasons'], sort_keys=True)}")
        groups.append(group)
    text.extend(["", "## Deterministic analysis", "",
                 (directory / "debrief.md").read_text(encoding="utf-8")])
    publish(directory / "consistency.json", dict(
        schema_version=2, timing_basis="per_recorded_session", recordings=groups,
        cross_context_aggregation=False, objective="chief_review_required"))
    temporary = directory / f".{uuid4().hex}.md.partial"
    with temporary.open("x", encoding="utf-8") as output:
        output.write("\n".join(text) + "\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(directory / "stint-debrief.md")


def request_checkpoint(visit: Path):
    """Chief request bound to a fresh, actual worker. No capture/presence effects."""
    visit = visit.resolve(strict=True)
    lock = SourceLock(visit / "request.lock")
    try:
        inspection = inspect_visit(visit)
        _, status = _artifact(visit, "status.json")
        worker = inspection["current_worker"]
        now = time.time()
        if (not status or status.get("status") != "running"
                or not published_capture_ready(status, now=now)
                or not inspection["heartbeat"]["fresh"]
                or (visit / "stop").exists()
                or any(inspection[role]["state"] != "running"
                       or inspection[role]["identity_status"] != "matched"
                       for role in ("host", "worker"))):
            raise ValueError("checkpoint_visit_or_worker_unverified")
        request_id = uuid4().hex
        directory = visit / worker / "checkpoint-requests"
        if not _confined(visit, directory):
            raise ValueError("checkpoint_path_redirected")
        directory.mkdir(exist_ok=True)
        requested_at = time.time()
        if not published_capture_ready(status, now=requested_at):
            raise ValueError("checkpoint_visit_or_worker_unverified")
        path = directory / f"{request_id}.json"
        publish(path, dict(schema_version=1, request_id=request_id, action="checkpoint",
                           at=requested_at, capture_expires_at=status["capture_expires_at"],
                           worker=inspection["worker"]["recorded_process"]))
        return dict(request_id=request_id, request_path=str(path), state="requested",
                    receipt_path=str(visit / worker / "checkpoints" / request_id / "status.json"))
    finally:
        lock.close()


class StintCheckpoints:
    """No retries of detached finalization or analysis; failures remain inspectable.

    Only the worker's control thread calls poll/finish. A single job thread owns
    finalization/review. A second request while busy is refused, never queued.
    """

    def __init__(self, run, recorder):
        from simcoach.live.visit_worker import local_path

        self.run, self.recorder = local_path(Path(run)), recorder
        self.root = self.run / "checkpoints"
        self.requests = self.run / "checkpoint-requests"
        if not _confined(self.run, self.root) or not _confined(self.run, self.requests):
            raise ValueError("checkpoint_path_redirected")
        self.root.mkdir(exist_ok=True)
        self.requests.mkdir(exist_ok=True)
        self.processed = set()
        self.active = None
        self.thread = None
        self.last = None
        self.rows = {}
        self.errors = []
        self.detached = None

    def _status(self, directory, state, **fields):
        value = dict(schema_version=1, request_id=directory.name, state=state,
                     updated_at=time.time(), **fields)
        self.last = value
        try:
            publish(directory / "status.json", value)
        except OSError as exc:
            self.errors.append(dict(request_id=directory.name, error_type=type(exc).__name__,
                                    effect="status_publication"))

    def _job(self, result, directory):
        from simcoach.live.visit_worker import close_recordings

        started = time.perf_counter()
        prefix = result["prefix"]
        try:
            publish(directory / "handoff.json", dict(
                schema_version=1, request_id=directory.name, at=time.time(),
                source_session_id=prefix["session_id"], prefix_path=str(prefix["paths"].root),
                successor_path=str(result["successor"]), tick_count=len(prefix["ticks"]),
                first_t=prefix["ticks"][0].t, last_t=prefix["ticks"][-1].t,
                rotation_s=result["rotation_s"]))
            finalized = finalize_recorded_session(
                prefix["paths"], prefix["session_id"], prefix["info"], prefix["started_at"],
                prefix["ticks"], interrupted=False,
                sim_invalidated_laps=prefix["sim_invalidated_laps"])
            if finalized.get("complete") is not True:
                raise RuntimeError("checkpoint_prefix_incomplete")
            paths = [p for p in prefix["sessions"]
                     if self.rows[str(p)].get("checkpoint_id") == directory.name]
            closure = close_recordings(directory, paths)
            for row in closure["recordings"]:
                self.rows[row["path"]] = dict(row, checkpoint_id=directory.name)
            consistency_debrief(directory, closure["recordings"])
            good = bool(closure["recordings"]) and all(
                r["validation"] == r["analysis"] == "PASS" for r in closure["recordings"])
            self._status(directory, "complete" if good else "failed",
                         recordings=closure["recordings"], elapsed_s=time.perf_counter()-started,
                         debrief_path=str(directory / "stint-debrief.md"),
                         review="chief_review_required", live_validation="NOT_ESTABLISHED")
        except BaseException as exc:
            self.errors.append(dict(request_id=directory.name, error_type=type(exc).__name__,
                                    effect="finalization_or_review"))
            with suppress(OSError):
                (directory / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._status(directory, "failed", error_type=type(exc).__name__,
                         elapsed_s=time.perf_counter()-started, review="unavailable")

    def poll(self, *, admission_block=None):
        if self.thread is not None and not self.thread.is_alive():
            self.thread.join()
            self.thread, self.active = None, None
            self.detached = None  # Completed job artifacts own the evidence now.
        if self.active and self.thread is None:
            result = self.recorder.take_checkpoint()
            if result is not None:
                self.detached = result  # Retain handoff if thread startup/publication fails.
                directory = self.root / self.active
                if result.get("error_type"):
                    self.errors.append(dict(result))
                    self._status(directory, "failed", error_type=result["error_type"])
                    self.active = None
                else:
                    for path in result["prefix"]["sessions"]:
                        self.rows.setdefault(str(path), dict(
                            path=str(path), validation="NOT RUN", analysis="NOT RUN",
                            summary=None, analysis_path=None, error_type="checkpoint_unconfirmed",
                            checkpoint_id=self.active))
                    self._status(directory, "detached")
                    self.thread = threading.Thread(target=self._job, args=(result, directory),
                                                   name="stint-finalization")
                    self.thread.start()
        if not _confined(self.run, self.requests) or not _confined(self.run, self.root):
            raise ValueError("checkpoint_path_redirected")
        for path in sorted(self.requests.glob("*.json"))[:256]:
            if path.name in self.processed:
                continue
            self.processed.add(path.name)
            if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                self.errors.append(dict(effect="invalid_request_filename"))
                continue
            directory = self.root / path.stem
            if directory.exists():
                self.errors.append(dict(request_id=path.stem, effect="ambiguous_existing_intent"))
                continue
            artifact, command = _artifact(self.run, "checkpoint-requests/" + path.name)
            _, identity = _artifact(self.run, "worker-process.json")
            directory.mkdir()
            publish(directory / "intent.json", dict(schema_version=1, request=command,
                                                     sha256=artifact["sha256"], at=time.time()))
            reason = None
            if (not command or command.get("request_id") != path.stem
                    or command.get("action") != "checkpoint" or not identity
                    or command.get("worker") != identity.get("process")):
                reason = "request_binding_invalid"
            elif (type(command.get("at")) not in (int, float)
                  or not 0 <= time.time()-command["at"] < 3):
                reason = "request_expired"
            elif not capture_deadline_current(command["at"], command.get("capture_expires_at"),
                                              now=time.time()):
                reason = "capture_evidence_expired_or_unbound"
            elif admission_block:
                reason = admission_block
            elif self.active or self.thread:
                reason = "checkpoint_busy"
            elif (self.run.parent / "stop").exists() or not self.recorder.intake_active:
                reason = "capture_unavailable_or_departing"
            if reason:
                self._status(directory, "refused", reason=reason)
                continue
            self.active = path.stem
            self._status(directory, "accepted", request_sha256=artifact["sha256"])
            if not self.recorder.request_checkpoint(self.active):
                self._status(directory, "refused", reason="recorder_checkpoint_busy")
                self.active = None

    def tick(self, *, admission_block=None):
        try:
            self.poll(admission_block=admission_block)
        except Exception as exc:
            # Diagnostic/control I/O must not stop capture. Never retry a consumed
            # intent whose publication or handoff may have taken effect.
            self.errors.append(dict(effect="checkpoint_control", error_type=type(exc).__name__))

    def finish(self):
        self.tick(admission_block="capture_unavailable_or_departing")
        if self.thread:
            self.thread.join()  # Keep worker ownership; parent bounds its observation wait.
            self.thread = None
            self.active = None
            self.detached = None
        elif self.active:
            self._status(self.root / self.active, "failed", reason="capture_ended_before_handoff")
            self.errors.append(dict(request_id=self.active, effect="handoff_not_observed"))
            self.active = None

    def snapshot(self):
        return dict(active=self.active, job_running=bool(self.thread and self.thread.is_alive()),
                    last=self.last, errors=list(self.errors), reviewed_recordings=len(self.rows))
