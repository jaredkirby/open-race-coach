"""Bounded owned-worker replacement without changing Driver Visit presence."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.recording_storage import storage_deadline
from simcoach.live.supervisor import strict_json
from simcoach.live.visit_recovery import _identity, probe_process
from simcoach.live.visit_release import child_command


def read(path):
    try:
        value = strict_json(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


class CaptureAttempts:
    """One host's Popen handles; never adopt a surviving process after restart.

    Three starts maximum. Every start checks the shared capture lock and source
    admission. Live starts also require fresh storage. Replacements require launcher exit, actual
    worker identity
    observed absent/exited and cooldown. No closure effect is retried.
    """

    def __init__(self, run, root, *, fixture=None, popen=None, probe=None,
                 clock=None, elapsed=None):
        self.run, self.root = Path(run).resolve(), Path(root).resolve()
        self.fixture = Path(fixture).resolve() if fixture is not None else None
        self.popen = popen or subprocess.Popen
        self.probe = probe or probe_process
        self.clock = clock or time.time
        self.elapsed = elapsed or time.perf_counter
        self.process = None
        self.directory = self.run / "worker"
        self.attempts = []
        self.code = None
        self.state = "awaiting_source"
        self.exited_elapsed = None
        self.streams = []
        self.launch_uncertain = False
        self.worker_observation = None
        self.interrupted = False

    def _snapshot_process(self, pid):
        try:
            return self.probe(pid)
        except Exception as exc:
            return dict(pid=pid, state="unknown", created_100ns=None,
                        reason=f"probe_failed_{type(exc).__name__}")

    def _launch(self, now, elapsed, storage):
        index = len(self.attempts) + 1
        name = "worker" if index == 1 else f"worker-{index:04d}"
        self.directory = self.run / name
        receipt = self.run / "capture-attempts" / f"{index:04d}"
        receipt.mkdir(parents=True, exist_ok=False)
        arguments = ["--run", str(self.directory), "--lock-root", str(self.root)]
        if self.fixture:
            arguments.extend(["--fixture", str(self.fixture)])
        command = child_command("simcoach.live.visit_worker", arguments, receipt / "source-launch")
        intent = dict(schema_version=1, at=now, attempt=index, worker_directory=name,
                      command=command, mode="replay" if self.fixture else "live")
        if storage is not None:
            intent["recording_storage"] = storage
        intent["recovery_basis"] = (dict(
            prior_attempt=index-1, launcher_exit_code=self.code,
            actual_worker=self.worker_observation, capture_lock_available=True,
            source_admitted_at=now, cooldown_elapsed_s=elapsed-self.exited_elapsed,
        ) if index > 1 else None)
        write_status(receipt / "intent.json", intent)
        if index == 1:
            write_status(self.run / "worker-intent.json", intent)
        self.attempts.append(dict(attempt=index, worker_directory=name,
                                  exit_code=None, started_at=now, exited_at=None,
                                  first_ready_at=None))
        self.started_elapsed = elapsed
        self.prior_exit_elapsed = self.exited_elapsed
        # Latch before spawn: a partially successful launch must never be retried.
        self.launch_uncertain = True
        self.process = None
        self.code = None
        self.exited_elapsed = None
        self.worker_observation = None
        self.streams = []
        for stream in ("stdout", "stderr"):
            self.streams.append((self.run / f"{name}.{stream}.log").open("xb"))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = self.popen(command, stdout=self.streams[0], stderr=self.streams[1],
                                  creationflags=flags)
        spawn = dict(intent, pid=self.process.pid, process=self._snapshot_process(self.process.pid))
        write_status(receipt / "spawn.json", spawn)
        if index == 1:
            write_status(self.run / "worker-spawn.json", spawn)
        self.launch_uncertain = False
        self.state = "starting"

    def observe_exit(self, *, now, elapsed):
        if self.process is None:
            return
        code = self.process.poll()
        if code is None:
            return
        self.code = code
        if self.exited_elapsed is None:
            self.exited_elapsed = elapsed
            self.attempts[-1].update(exit_code=code, exited_at=now)
            receipt = self.run / "capture-attempts" / f"{len(self.attempts):04d}" / "exit.json"
            write_status(receipt, dict(schema_version=1, at=now, exit_code=code,
                                       attempt=len(self.attempts),
                                       worker_directory=self.directory.name))
            self.close_streams()

    def step(self, *, source_ready, now, elapsed, allow_recovery=True, source_expires_at=None,
             storage=None):
        self.observe_exit(now=now, elapsed=elapsed)
        if (self.run / "stop").exists():
            self.state = "departure_requested"
            return
        if self.launch_uncertain:
            self.state = "launch_uncertain"
            return
        if self.process is not None and self.code is None:
            self.state = "worker_running"
            return
        if self.attempts:
            self.interrupted = True
            if not allow_recovery or len(self.attempts) >= 3:
                self.state = "recovery_disabled" if not allow_recovery else "attempt_limit"
                return
            if elapsed - self.exited_elapsed < 2:
                self.state = "recovery_cooldown"
                return
            receipt = read(self.directory / "worker-process.json")
            if (receipt is not None and (type(receipt.get("schema_version")) is not int
                                         or receipt["schema_version"] != 1)):
                receipt = None
            try:
                self.worker_observation = _identity(receipt, self.probe, lambda: now)
            except Exception:
                self.worker_observation = dict(state="unknown", reason="probe_failed")
            if self.worker_observation["state"] not in ("absent", "exited"):
                self.state = "worker_exit_unconfirmed"
                return
        if storage is not None and storage_deadline(storage, now=self.clock(),
                                                      elapsed=self.elapsed()) is None:
            self.state = "capture_storage_unavailable"
            return
        if not source_ready:
            self.state = "awaiting_source"
            return
        try:
            lock = SourceLock(self.root / "capture.lock")
        except (OSError, RuntimeError):
            self.state = "capture_lock_unavailable"
            return
        # This check only avoids starting a worker into known contention. The
        # child acquires the same lock before opening its adapter.
        lock.close()
        if (self.run / "stop").exists():
            self.state = "departure_requested"
            return
        # Process/filesystem probes may take long enough for admission to expire.
        # Recheck its original deadline, never extend it from this later poll.
        checked_at = self.clock()
        if source_expires_at is not None and not now <= checked_at < source_expires_at:
            self.state = "awaiting_source"
            return
        if storage is not None and storage_deadline(storage, now=checked_at,
                                                      elapsed=self.elapsed()) is None:
            self.state = "capture_storage_unavailable"
            return
        self._launch(now, elapsed, storage)

    def observe_ready(self, *, now, elapsed):
        """Record first fresh admission, never infer full coverage or driver delay."""
        if not self.attempts or self.attempts[-1]["first_ready_at"] is not None:
            return
        receipt = dict(schema_version=1, at=now, attempt=len(self.attempts),
                       worker_directory=self.directory.name,
                       launch_to_ready_s=elapsed-self.started_elapsed,
                       observed_exit_to_ready_s=(elapsed-self.prior_exit_elapsed
                                                if self.prior_exit_elapsed is not None else None),
                       basis="host monotonic observations; not coverage or driver waiting time")
        write_status(self.run / "capture-attempts" / f"{len(self.attempts):04d}"
                     / "capture-ready.json", receipt)
        self.attempts[-1]["first_ready_at"] = now

    def snapshot(self):
        return dict(schema_version=1, state=self.state, max_attempts=3,
                    current_worker=self.directory.name,
                    attempts=[dict(row) for row in self.attempts],
                    worker_exit_observation=self.worker_observation,
                    capture_interrupted=self.interrupted,
                    prior_attempts_preserved=len(self.attempts) > 1)

    def close_streams(self):
        for stream in self.streams:
            stream.close()
        self.streams = []
