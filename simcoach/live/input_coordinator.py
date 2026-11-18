"""Durable synthetic input coordination; no desktop backend or live input API.

The native claim excludes cooperating coordinators across journals in this
Windows session. It is not authentication, a desktop security boundary, or proof
that an unrelated controller has yielded. Physical input remains unimplemented.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from simcoach.live.output_owner import WindowsOutputClaim

CLAIM_NAME = r"Local\OpenRaceCoach.InputCoordinator.Synthetic.v1"
APPLICATION_ID = 0x4F524349


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def label(value, maximum=128):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 for c in value)):
        raise ValueError("invalid_input_label")
    return value


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid_input_time")
    return value


def observation(value):
    required = {"at", "revision", "app", "pid", "created", "window", "state",
                "focused", "modal", "driving", "targets"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("invalid_input_observation")
    result = dict(value)
    finite(result["at"])
    for key in ("revision", "pid", "window"):
        if type(result[key]) is not int or result[key] < (0 if key == "revision" else 1):
            raise ValueError("invalid_input_identity")
    for key in ("app", "created", "state"):
        label(result[key])
    for key in ("focused", "modal", "driving"):
        if type(result[key]) is not bool:
            raise ValueError("input_condition_unknown")
    if not isinstance(result["targets"], list) or len(result["targets"]) > 64:
        raise ValueError("invalid_input_targets")
    result["targets"] = [label(target) for target in result["targets"]]
    if len(set(result["targets"])) != len(result["targets"]):
        raise ValueError("invalid_input_targets")
    return result


class InputCoordinator:
    """Journal and admission owner for a fake backend only.

    A caller must explicitly grant a preparation generation. Dispatch commits
    before the simulated effect. Startup never resumes old commands or grants.
    Tests may select a unique native claim; there is no CLI or real UI adapter.
    """

    def __init__(self, directory, *, claim_name=CLAIM_NAME, clock=time.perf_counter):
        self.root = Path(directory).absolute()
        if self.root.resolve() != self.root:
            raise ValueError("input_journal_redirected")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "input.sqlite"
        if path.resolve() != path:
            raise ValueError("input_database_redirected")
        self.claim = WindowsOutputClaim(name=claim_name)
        self.claim.acquire()
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.cancel.set()
        self.clock, self.last_time = clock, None
        self.generation, self.broken, self.closed = None, False, False
        self.inflight = None
        self.release_confirmed = True
        self.releasing = 0
        self.db = None
        try:
            existed = path.exists()
            self.db = sqlite3.connect(path, timeout=1, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            if existed and (self.db.execute("PRAGMA application_id").fetchone()[0] !=
                            APPLICATION_ID or
                            self.db.execute("PRAGMA user_version").fetchone()[0] != 1):
                raise ValueError("unrecognized_input_database")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if not existed:
                self.db.executescript(f"""
                    CREATE TABLE commands (
                        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                        state TEXT NOT NULL, reason TEXT, ordinal INTEGER UNIQUE NOT NULL);
                    CREATE TABLE events (
                        seq INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL,
                        value TEXT NOT NULL);
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version=1;
                """)
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("input_database_integrity_failed")
            with self.transaction():
                rows = self.db.execute(
                    "SELECT id,state FROM commands WHERE state IN ('queued','dispatched')"
                ).fetchall()
                for row in rows:
                    self.transition(row["id"], "unknown" if row["state"] == "dispatched"
                                    else "cancelled", "prior_owner_ended")
                self.event("owner_opened", dict(mode="synthetic_only", resumed=False))
        except BaseException:
            if self.db:
                self.db.close()
            self.claim.close()
            raise

    @contextmanager
    def transaction(self):
        try:
            with self.db:
                yield
        except BaseException:
            self.broken = True
            self.generation = None
            self.cancel.set()
            raise

    def event(self, kind, value):
        self.db.execute("INSERT INTO events(at,kind,value) VALUES (?,?,?)",
                        (time.time(), kind, encoded(value)))

    def transition(self, identity, state, reason):
        self.db.execute("UPDATE commands SET state=?,reason=? WHERE id=?",
                        (state, reason, identity))
        self.event("command_state", dict(id=identity, state=state, reason=reason))

    def now(self):
        now = finite(self.clock())
        if self.last_time is not None and now < self.last_time:
            self.cancel.set()
            self.generation = None
            raise ValueError("input_clock_regressed")
        self.last_time = now
        return now

    def usable(self):
        if self.closed or self.broken:
            raise RuntimeError("input_owner_unavailable")

    def unresolved(self):
        return bool(self.db.execute(
            "SELECT 1 FROM commands WHERE state='unknown' LIMIT 1").fetchone())

    def receipt(self, identity):
        with self.lock:
            row = self.db.execute("SELECT * FROM commands WHERE id=?", (identity,)).fetchone()
            return dict(row) if row else None

    def grant(self, *, reason):
        """Trusted Chief call for synthetic preparation; not a user-auth API."""
        label(reason, 512)
        with self.lock:
            self.usable()
            self.now()
            if (self.generation or self.inflight or self.releasing
                    or not self.release_confirmed or self.unresolved()):
                raise RuntimeError("input_owner_not_reconciled")
            generation = uuid4().hex
            with self.transaction():
                self.event("preparation_granted", dict(generation=generation, reason=reason))
            self.generation = generation
            self.cancel = threading.Event()
            return generation

    def submit(self, command):
        if not isinstance(command, dict) or set(command) != {
                "id", "generation", "recipe_sha256", "before", "action", "target",
                "expected_state", "deadline"}:
            raise ValueError("invalid_input_command")
        for key, size in (("id", 32), ("generation", 32), ("recipe_sha256", 64)):
            value = command[key]
            if (not isinstance(value, str) or len(value) != size
                    or any(c not in "0123456789abcdef" for c in value)):
                raise ValueError("invalid_input_command_identity")
        if command["action"] != "select":
            raise ValueError("unsupported_input_action")
        label(command["target"])
        label(command["expected_state"])
        before = observation(command["before"])
        deadline = finite(command["deadline"])
        payload = encoded(command)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.lock:
            self.usable()
            previous = self.receipt(command["id"])
            if previous:
                if previous["fingerprint"] != digest:
                    raise ValueError("input_command_id_conflict")
                return previous
            now = self.now()
            if (self.cancel.is_set() or command["generation"] != self.generation
                    or self.unresolved()):
                raise ValueError("input_permission_unavailable")
            if not before["at"] <= now < deadline <= before["at"] + 2:
                raise ValueError("input_command_expired")
            self.conditions(before, now)
            if (command["target"] not in before["targets"]
                    and before["state"] != command["expected_state"]):
                raise ValueError("input_target_unobserved")
            with self.transaction():
                ordinal = self.db.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
                if ordinal >= 10000:
                    raise ValueError("input_journal_command_limit")
                self.db.execute("INSERT INTO commands VALUES (?,?,?,?,?,?)", (
                    command["id"], digest, payload, "queued", None, ordinal))
                self.event("command_accepted", dict(id=command["id"], fingerprint=digest))
            return self.receipt(command["id"])

    @staticmethod
    def conditions(value, now):
        if not 0 <= now - value["at"] < 2:
            raise ValueError("input_observation_expired")
        if not value["focused"] or value["modal"] or value["driving"]:
            raise ValueError("input_conditions_changed")

    def dispatch(self, identity, backend):
        """Run one simulated action, with admission rechecked after observation."""
        if type(backend) is not SimulatedInput:
            raise ValueError("only_synthetic_input_backend_supported")
        with self.lock:
            self.usable()
            row = self.receipt(identity)
            if not row or row["state"] != "queued":
                return row
            if self.inflight:
                raise RuntimeError("input_action_in_flight")
            first = self.db.execute(
                "SELECT id FROM commands WHERE state='queued' ORDER BY ordinal LIMIT 1").fetchone()
            if first[0] != identity:
                raise ValueError("input_command_order")
            command = json.loads(row["payload"])
            self.inflight = identity
            cancelled = self.cancel
        dispatched = False
        try:
            current = observation(backend.observe())
            with self.lock:
                self.usable()
                now = self.now()
                if (cancelled.is_set() or self.generation != command["generation"]
                        or self.unresolved() or now >= command["deadline"]):
                    raise ValueError("input_permission_or_deadline_changed")
                self.conditions(current, now)
                prior = command["before"]
                for key in ("app", "pid", "created", "window"):
                    if current[key] != prior[key]:
                        raise ValueError("input_target_identity_changed")
                if current["state"] == command["expected_state"]:
                    with self.transaction():
                        self.transition(identity, "skipped", "destination_already_observed")
                    return self.receipt(identity)
                if (any(current[key] != prior[key] for key in ("revision", "state", "targets"))
                        or command["target"] not in current["targets"]):
                    raise ValueError("input_basis_changed")
                with self.transaction():
                    self.transition(identity, "dispatched", "effect_not_yet_verified")
                dispatched = True
            backend.perform(command, cancelled)
            after = observation(backend.observe())
            with self.lock:
                now = self.now()
                self.conditions(after, now)
                if (cancelled.is_set() or self.generation != command["generation"]
                        or now >= command["deadline"]
                        or after["state"] != command["expected_state"]
                        or any(after[key] != prior[key]
                               for key in ("app", "pid", "created", "window"))):
                    raise ValueError("input_postcondition_unconfirmed")
                with self.transaction():
                    self.transition(identity, "verified", "synthetic_destination_observed")
        except BaseException as exc:
            try:
                with self.lock:
                    if not self.broken:
                        with self.transaction():
                            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                            if self.receipt(identity)["state"] in {"queued", "dispatched"}:
                                self.transition(identity, "unknown" if dispatched else "refused",
                                                reason)
                            else:
                                self.event("late_result", dict(id=identity, reason=reason))
            finally:
                # A failed outcome write must not suppress held-input cleanup.
                self.takeover(reason="dispatch_stopped", backend=backend)
            if not isinstance(exc, Exception):
                raise
        finally:
            with self.lock:
                self.inflight = None
        return self.receipt(identity)

    def takeover(self, *, reason, backend):
        """Revoke before journal I/O; backend release is attempted even on I/O failure."""
        label(reason, 512)
        self.cancel.set()
        with self.lock:
            self.releasing += 1
            self.release_confirmed = False
        try:
            try:
                with self.lock:
                    self.cancel.set()
                    self.generation = None
                    self.usable()
                    with self.transaction():
                        rows = self.db.execute(
                            "SELECT id,state FROM commands WHERE state IN ('queued','dispatched')"
                        ).fetchall()
                        for row in rows:
                            self.transition(row["id"], "unknown" if row["state"] == "dispatched"
                                            else "cancelled", reason)
                        self.event("control_revoked", dict(reason=reason))
            finally:
                released = backend.release()
                with self.lock:
                    self.release_confirmed = released is True
                    if released is not True:
                        self.broken = True
                        raise RuntimeError("input_release_unconfirmed")
            with self.lock, self.transaction():
                self.event("synthetic_inputs_released", dict(confirmed=True))
            return dict(revoked=True, synthetic_release_confirmed=released)
        finally:
            with self.lock:
                self.releasing -= 1

    def reconcile(self, identity, *, note):
        """Explicitly abandon an unknown action; never infer it did not execute."""
        label(note, 512)
        with self.lock:
            self.usable()
            row = self.receipt(identity)
            if self.inflight or not row or row["state"] != "unknown":
                raise ValueError("input_reconciliation_not_available")
            with self.transaction():
                self.transition(identity, "reconciled", "no_replay_effect_remains_unknown")
                self.event("chief_reconciliation", dict(id=identity, note=note))
            return self.receipt(identity)

    def close(self, backend):
        if self.closed:
            return
        try:
            self.takeover(reason="owner_closing", backend=backend)
        finally:
            with self.lock:
                if (self.inflight or self.releasing or backend.held
                        or not self.release_confirmed):
                    raise RuntimeError("input_owner_close_unconfirmed")
                self.db.close()
                self.claim.close()
                self.closed = True


class SimulatedInput:
    """Test backend, incapable of OS input. Optional callbacks inject test failures."""

    def __init__(self, *, clock=time.perf_counter, before_effect=None, after_effect=None):
        self.clock = clock
        self.before_effect, self.after_effect = before_effect, after_effect
        self.lock = threading.RLock()
        self.held = False
        self.effects = []
        self.value = dict(revision=0, app="synthetic_menu", pid=1, created="synthetic",
                          window=1, state="menu", focused=True, modal=False, driving=False,
                          targets=["Start"])

    def observe(self):
        with self.lock:
            return json.loads(encoded(dict(self.value, at=self.clock())))

    def perform(self, command, cancelled):
        if self.before_effect:
            self.before_effect()
        with self.lock:
            if cancelled.is_set():
                raise ValueError("synthetic_input_cancelled_before_effect")
            # A successful journal commit is not a current target observation.
            # Recheck after any executor delay, under the same fake-state lock
            # used to apply the effect. A real backend needs its own supported
            # final check; these checks do not establish OS input atomicity.
            current = observation(self.observe())
            now = finite(current["at"])
            prior = command["before"]
            if not prior["at"] <= now < command["deadline"]:
                raise ValueError("synthetic_dispatch_deadline_changed")
            InputCoordinator.conditions(current, now)
            if (any(current[key] != prior[key] for key in prior if key != "at")
                    or command["target"] not in current["targets"]):
                raise ValueError("synthetic_dispatch_basis_changed")
            self.held = True
            self.effects.append(command["id"])
            self.value["state"] = command["expected_state"]
            self.value["revision"] += 1
        if self.after_effect:
            self.after_effect()
        self.release()

    def release(self):
        with self.lock:
            self.held = False
        return True
