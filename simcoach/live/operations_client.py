"""Visit-owned single launch and evidence-bound access to local operations."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.supervisor import number
from simcoach.live.visit_recovery import (
    _artifact,
    _confined,
    _created,
    _identity,
    _pid,
    probe_process,
)
from simcoach.live.visit_release import child_command


def _publish(path, value):
    # Failed temporaries survive later receipt-publication attempts.
    write_status(path, value, temporary=path.parent / f".{uuid4().hex}.tmp")


class OperationsClient:
    """No retry/adoption/kill. The owned launcher and actual service are distinct."""

    def __init__(self, run, *, archive, plan, duration, enable_audio=False, phrase=None,
                 selection=None, loss_phrase=None, recovery_phrase=None, chief_phrase=None,
                 popen=None, probe=None, clock=None):
        self.run = Path(run).resolve()
        self.directory = self.run / "operations"
        self.clock, self.probe = clock or time.time, probe or probe_process
        self.process = None
        self.service_id = None
        self.pinned_process = None
        self.binding_changed = False
        self.streams = []
        self.forwarded = {}
        self.receipt_errors = {}
        self.submission_errors = {}
        self.last = dict(health="unknown", reason="launch_not_confirmed", state=None)
        plan_path = self.run / "operations-plan.json"
        _publish(plan_path, dict(schema_version=1, **asdict(plan)))
        command = ["--visit", str(self.run), "--out", str(self.directory),
                   "--archive", str(Path(archive).resolve()),
                   "--plan", str(plan_path), "--present", "--duration", str(duration),
                   "--visit-controls"]
        if enable_audio:
            command.append("--enable-audio")
        for key, value in (("phrase", phrase), ("selection", selection),
                           ("loss-phrase", loss_phrase), ("recovery-phrase", recovery_phrase),
                           ("chief-phrase", chief_phrase)):
            if value is not None:
                command.extend(["--" + key, str(Path(value).resolve())])
        command = child_command("simcoach.live.operations_service", command,
                                self.run / "operations-source-launch")
        _publish(self.run / "operations-intent.json", dict(
            schema_version=1, at=self.clock(), command=command, directory="operations",
            plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest()))
        (self.run / "operations-forwarded").mkdir(exist_ok=False)
        # Intent precedes spawn; any exception is ambiguous and never retried.
        for name in ("stdout", "stderr"):
            self.streams.append((self.run / f"operations.{name}.log").open("xb"))
        try:
            self.process = (popen or subprocess.Popen)(
                command, stdout=self.streams[0], stderr=self.streams[1],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _publish(self.run / "operations-spawn.json", dict(
                schema_version=1, at=self.clock(), process=self.probe(self.process.pid)))
        except Exception:
            self.close_streams()
            raise

    def poll(self):
        _, run = _artifact(self.run, "operations/run.json")
        _, status = _artifact(self.run, "operations/status.json")
        _, heartbeat = _artifact(self.run, "operations/heartbeat.json")
        identity = run.get("service_id") if run else None
        if (isinstance(identity, str) and len(identity) == 32
                and all(c in "0123456789abcdef" for c in identity)):
            if self.service_id is None:
                self.service_id = identity
            elif identity != self.service_id:
                self.binding_changed = True
        process = _identity(run, self.probe, self.clock)
        record = process["recorded_process"] or {}
        actual = dict(pid=record.get("pid"), created_100ns=record.get("created_100ns"))
        if _pid(actual["pid"]) and _created(actual["created_100ns"]):
            if self.pinned_process is None:
                self.pinned_process = actual
            elif actual != self.pinned_process:
                self.binding_changed = True
        code = self.process.poll() if self.process else None
        healthy = False
        bound = False
        reason = "service_evidence_unavailable"
        try:
            now = number(self.clock())
            bound = (run and Path(run["visit"]).resolve() == self.run
                     and run["service_id"] == self.service_id and self.service_id is not None
                     and not self.binding_changed and status["service_id"] == self.service_id
                     and self.pinned_process is not None and actual == self.pinned_process)
            if not bound:
                reason = "service_binding_mismatch"
            elif code is not None:
                reason = "service_launcher_exited"
            elif process["state"] != "running" or process["identity_status"] != "matched":
                reason = "service_process_unverified"
            elif status["status"] != "running":
                reason = "service_not_running"
            elif not (0 <= now - number(status["updated_at"]) < 3
                      and 0 <= now - number(heartbeat["at"]) < 3):
                reason = "service_status_stale"
            else:
                healthy, reason = True, None
        except (ValueError, TypeError, KeyError, OSError):
            pass
        self.last = dict(health="healthy" if healthy else "degraded", reason=reason,
                         service_id=self.service_id, binding_changed=self.binding_changed,
                         binding_valid=bool(bound),
                         pending_receipts=list(self.forwarded),
                         receipt_errors=dict(self.receipt_errors),
                         submission_errors=dict(self.submission_errors),
                         process=process, launcher_exit=code, state=status)
        return self.last

    def forward_fuel(self, filename, command):
        approval = {key: command[key] for key in (
            "cockpit_litres", "verified_at", "source_id", "solo_player_confirmed", "note")}
        approval.update({key: command[key] for key in ("player_view_confirmed", "participant_index")
                         if key in command})
        return self._forward(filename, command["at"], "fuel", approval=approval)

    def forward_plan(self, filename, command):
        return self._forward(filename, command["at"], "plan", **{
            key: command[key] for key in ("plan", "expected_plan_id", "note")})

    def _forward(self, filename, at, action, **fields):
        value = self.poll()
        if value["health"] != "healthy" or value["state"]["chief_link"]["state"] != "connected":
            raise ValueError("operations_unavailable_for_approval")
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError("invalid_request_filename")
        forwarded = dict(schema_version=1, service_id=self.service_id, at=at,
                         action=action, **fields)
        destination = self.directory / "requests" / filename
        audit = self.run / "operations-forwarded" / filename
        if not _confined(self.run, destination) or not _confined(self.run, audit):
            raise ValueError("operations_request_path_redirected")
        if destination.exists() or audit.exists() or filename in self.forwarded:
            raise ValueError("operations_request_already_exists")
        text = json.dumps(forwarded, indent=2, allow_nan=False) + "\n"
        encoded = text.replace("\n", os.linesep).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        _publish(audit, dict(
            schema_version=1, service_id=self.service_id, at=self.clock(), request_sha256=digest))
        self.forwarded[filename] = digest
        try:
            _publish(destination, forwarded)
        except OSError as error:
            # Publication may have taken effect; never retry or claim rejection.
            self.submission_errors[filename] = type(error).__name__
            return False
        return True

    def sync_receipts(self):
        if self.binding_changed:
            return
        for name, digest in list(self.forwarded.items()):
            _, receipt = _artifact(self.run, "operations/receipts/" + name)
            if (receipt and receipt.get("service_id") == self.service_id
                    and receipt.get("request_sha256") == digest
                    and type(receipt.get("accepted")) is bool):
                target = self.run / "receipts" / name
                if not _confined(self.run, target):
                    self.receipt_errors[name] = "receipt_path_redirected"
                    continue
                if target.exists():
                    _, existing = _artifact(self.run, "receipts/" + name)
                    if existing != dict(receipt, authority="operations_service"):
                        self.receipt_errors[name] = "receipt_conflict"
                        continue
                else:
                    try:
                        _publish(target, dict(receipt, authority="operations_service"))
                    except OSError as error:
                        self.receipt_errors[name] = type(error).__name__
                        continue
                del self.forwarded[name]
                self.receipt_errors.pop(name, None)
                self.submission_errors.pop(name, None)
        self.last.update(pending_receipts=list(self.forwarded),
                         receipt_errors=dict(self.receipt_errors),
                         submission_errors=dict(self.submission_errors))

    def terminal(self):
        value = self.poll()
        state = value["state"] or {}
        return bool(value["binding_valid"] and self.service_id
                    and state.get("service_id") == self.service_id
                    and value["launcher_exit"] == 0
                    and value["process"]["state"] in {"absent", "exited"}
                    and state.get("status") == "complete"
                    and state.get("radio", {}).get("closed") is True)

    def close_streams(self):
        for stream in self.streams:
            stream.close()
