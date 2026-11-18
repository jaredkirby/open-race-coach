"""Synthetic actions and native ownership/crash evidence; no desktop or simulator input."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from uuid import uuid4

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.input_coordinator import InputCoordinator, SimulatedInput
from simcoach.live.visit_recovery import probe_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native coordinator claim")


def claim():
    return "Local\\ORC.InputTest." + uuid4().hex


def command(owner, backend, **changes):
    before = backend.observe()
    return dict(id=uuid4().hex, generation=owner.generation, recipe_sha256="a"*64,
                before=before, action="select", target="Start", expected_state="garage",
                deadline=before["at"]+1.5) | changes


@pytest.fixture
def rig(tmp_path):
    now = [10.0]
    backend = SimulatedInput(clock=lambda: now[0])
    owner = InputCoordinator(tmp_path / "journal", clock=lambda: now[0], claim_name=claim())
    owner.grant(reason="Synthetic preparation only; no driver or desktop")
    yield owner, backend, now
    if not owner.closed:
        owner.close(backend)


def test_ordered_dispatch_deduplicates_and_skips_an_already_observed_destination(rig):
    owner, backend, _ = rig
    first, second = command(owner, backend), command(owner, backend)
    owner.submit(first)
    owner.submit(second)
    with pytest.raises(ValueError, match="command_order"):
        owner.dispatch(second["id"], backend)
    assert owner.dispatch(first["id"], backend)["state"] == "verified"
    assert owner.submit(first)["state"] == "verified"
    assert owner.dispatch(first["id"], backend)["state"] == "verified"
    assert owner.dispatch(second["id"], backend)["state"] == "skipped"
    assert backend.effects == [first["id"]]
    with pytest.raises(ValueError, match="id_conflict"):
        owner.submit(first | {"expected_state": "different"})


@pytest.mark.parametrize("fault", ["focus", "modal", "driving", "pid_reuse", "window",
                                   "revision", "deadline", "regressed_clock"])
def test_fresh_admission_rechecked_before_effect(rig, fault):
    owner, backend, now = rig
    request = command(owner, backend)
    owner.submit(request)
    if fault == "focus":
        backend.value["focused"] = False
    elif fault == "modal":
        backend.value["modal"] = True
    elif fault == "driving":
        backend.value["driving"] = True
    elif fault == "pid_reuse":
        backend.value["created"] = "different_creation"
    elif fault == "window":
        backend.value["window"] += 1
    elif fault == "revision":
        backend.value["revision"] += 1
    else:
        now[0] += 3 if fault == "deadline" else -1
    assert owner.dispatch(request["id"], backend)["state"] == "refused"
    assert not backend.effects and not backend.held
    assert owner.generation is None


@pytest.mark.parametrize("fault", ["future", "old", "unknown_driving", "unknown_target",
                                   "bad_targets", "real_backend", "ungranted"])
def test_invalid_or_unauthorized_commands_cannot_reach_backend(rig, fault):
    owner, backend, now = rig
    request = command(owner, backend)
    if fault == "real_backend":
        owner.submit(request)
        with pytest.raises(ValueError, match="only_synthetic"):
            owner.dispatch(request["id"], object())
    else:
        if fault == "future":
            request["before"]["at"] += 1
        elif fault == "old":
            now[0] += 3
        elif fault == "unknown_driving":
            request["before"]["driving"] = None
        elif fault == "unknown_target":
            request["target"] = "Unobserved"
        elif fault == "bad_targets":
            request["before"]["targets"] = [{}]
        else:
            owner.takeover(reason="Human retains control", backend=backend)
        with pytest.raises(ValueError):
            owner.submit(request)
    assert not backend.effects


@pytest.mark.parametrize("phase", ["before", "after"])
def test_takeover_revokes_queued_and_inflight_work_without_a_late_replay(rig, phase):
    owner, backend, _ = rig
    reached, resume = threading.Event(), threading.Event()

    def barrier():
        reached.set()
        assert resume.wait(5)

    setattr(backend, phase + "_effect", barrier)
    request, queued = command(owner, backend), command(owner, backend)
    owner.submit(request)
    owner.submit(queued)
    result = []
    worker = threading.Thread(target=lambda: result.append(owner.dispatch(request["id"], backend)))
    worker.start()
    try:
        assert reached.wait(5)
        assert owner.takeover(reason="Driver takeover", backend=backend)["revoked"]
        assert not backend.held
        assert owner.receipt(queued["id"])["state"] == "cancelled"
        with pytest.raises(RuntimeError, match="not_reconciled"):
            owner.grant(reason="Cannot reacquire while work remains ambiguous")
        with pytest.raises(RuntimeError, match="close_unconfirmed"):
            owner.close(backend)
        assert not owner.closed
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert result[0]["state"] == "unknown"
    assert len(backend.effects) == (phase == "after")
    assert owner.dispatch(request["id"], backend)["state"] == "unknown"
    owner.reconcile(request["id"], note="Synthetic investigation; abandon without replay")
    assert owner.submit(request)["state"] == "reconciled"
    assert owner.grant(reason="Explicit new synthetic preparation") != request["generation"]


def test_same_native_owner_excludes_a_second_journal(tmp_path):
    name = claim()
    backend = SimulatedInput()
    first = InputCoordinator(tmp_path / "one", claim_name=name)
    try:
        with pytest.raises(RuntimeError, match="already_owned"):
            InputCoordinator(tmp_path / "two", claim_name=name)
    finally:
        first.close(backend)
    second = InputCoordinator(tmp_path / "two", claim_name=name)
    second.close(backend)


def test_commit_failure_prevents_effect_and_revokes_in_memory(rig):
    owner, backend, _ = rig
    request = command(owner, backend)
    owner.submit(request)
    owner.db.execute("""CREATE TRIGGER injected_storage_failure BEFORE INSERT ON events
        WHEN NEW.kind='command_state' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
    owner.db.commit()
    with pytest.raises(RuntimeError, match="owner_unavailable"):
        owner.dispatch(request["id"], backend)
    assert owner.broken and owner.generation is None and not backend.effects
    with pytest.raises(RuntimeError, match="owner_unavailable"):
        owner.close(backend)
    assert owner.closed


def test_unconfirmed_release_retains_native_owner_until_reconciled(rig):
    owner, backend, _ = rig
    backend.held = True
    backend.release = lambda: False
    with pytest.raises(RuntimeError, match="close_unconfirmed"):
        owner.close(backend)
    assert not owner.closed and owner.broken
    with pytest.raises(RuntimeError, match="already_owned"):
        InputCoordinator(owner.root.parent / "competitor", claim_name=owner.claim.name)
    backend.release = lambda: SimulatedInput.release(backend)
    with pytest.raises(RuntimeError, match="owner_unavailable"):
        owner.close(backend)
    assert owner.closed and not backend.held


def test_new_generation_cannot_start_while_prior_release_is_still_running(rig):
    owner, backend, _ = rig
    reached, resume = threading.Event(), threading.Event()

    def delayed_release():
        reached.set()
        assert resume.wait(5)
        return SimulatedInput.release(backend)

    backend.release = delayed_release
    results = []
    worker = threading.Thread(target=lambda: results.append(
        owner.takeover(reason="Synthetic delayed cleanup", backend=backend)))
    worker.start()
    try:
        assert reached.wait(5)
        with pytest.raises(RuntimeError, match="not_reconciled"):
            owner.grant(reason="Must not race prior release")
    finally:
        resume.set()
        worker.join(5)
        backend.release = lambda: SimulatedInput.release(backend)
    assert not worker.is_alive() and results[0]["revoked"]
    assert owner.grant(reason="Explicit request after confirmed cleanup")


@pytest.mark.parametrize("fault", ["wrong_destination", "expired_after_effect"])
def test_an_effect_without_a_fresh_postcondition_is_never_replayed(rig, fault):
    owner, backend, now = rig
    request = command(owner, backend)
    owner.submit(request)

    def change():
        if fault == "wrong_destination":
            backend.value["state"] = "unexpected_modal"
        else:
            now[0] += 3

    backend.after_effect = change
    assert owner.dispatch(request["id"], backend)["state"] == "unknown"
    assert backend.effects == [request["id"]]
    assert owner.dispatch(request["id"], backend)["state"] == "unknown"
    assert backend.effects == [request["id"]]
    assert owner.generation is None and not backend.held


@pytest.mark.parametrize("fault", ["deadline", "clock_regressed", "invalid_clock", "focus",
                                   "modal", "driving", "pid_reused", "window", "revision",
                                   "target_removed", "state", "destination_reached"])
def test_backend_rechecks_after_dispatch_commit_before_applying_effect(rig, fault):
    owner, backend, now = rig
    request, queued = command(owner, backend), command(owner, backend)
    owner.submit(request)
    owner.submit(queued)
    reached, resume = threading.Event(), threading.Event()

    def barrier():
        reached.set()
        assert resume.wait(5)

    backend.before_effect = barrier
    results = []
    worker = threading.Thread(target=lambda: results.append(owner.dispatch(request["id"], backend)))
    worker.start()
    try:
        assert reached.wait(5)
        assert owner.receipt(request["id"])["state"] == "dispatched"
        assert backend.effects == []
        with backend.lock:
            if fault == "deadline":
                now[0] = request["deadline"]
            elif fault == "clock_regressed":
                now[0] -= 1
            elif fault == "invalid_clock":
                now[0] = float("nan")
            else:
                key, value = {
                    "focus": ("focused", False), "modal": ("modal", True),
                    "driving": ("driving", True), "pid_reused": ("created", "new_lifetime"),
                    "window": ("window", 99), "revision": ("revision", 1),
                    "target_removed": ("targets", []), "state": ("state", "other_menu"),
                    "destination_reached": ("state", request["expected_state"]),
                }[fault]
                backend.value[key] = value
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert backend.effects == [], "A changed basis must prevent the effect, not only verification"
    assert results[0]["state"] == "unknown"
    assert owner.receipt(queued["id"])["state"] == "cancelled"
    assert owner.generation is None and not backend.held
    assert owner.dispatch(request["id"], backend)["state"] == "unknown"
    assert not backend.effects


def test_foreign_database_is_preserved_and_owner_claim_released(tmp_path):
    root = tmp_path / "foreign"
    root.mkdir()
    path = root / "input.sqlite"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE unrelated(value TEXT)")
        database.execute("INSERT INTO unrelated VALUES ('preserve')")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    name = claim()
    with pytest.raises(ValueError, match="unrecognized_input_database"):
        InputCoordinator(root, claim_name=name)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    owner = InputCoordinator(tmp_path / "valid", claim_name=name)
    owner.close(SimulatedInput())


def test_interrupt_after_effect_releases_and_retains_unknown_outcome(rig):
    owner, backend, _ = rig
    request = command(owner, backend)
    owner.submit(request)

    def interrupt():
        raise KeyboardInterrupt("Synthetic interruption")

    backend.after_effect = interrupt
    with pytest.raises(KeyboardInterrupt):
        owner.dispatch(request["id"], backend)
    assert owner.receipt(request["id"])["state"] == "unknown"
    assert owner.generation is None and not backend.held
    assert backend.effects == [request["id"]]


def test_failed_outcome_write_still_releases_inputs_and_retains_dispatch_receipt(rig):
    owner, backend, _ = rig
    request = command(owner, backend)
    owner.submit(request)

    def fail():
        owner.db.execute("""CREATE TRIGGER outcome_failure BEFORE INSERT ON events
            WHEN NEW.kind='command_state' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
        owner.db.commit()
        raise ValueError("synthetic_post_effect_failure")

    backend.after_effect = fail
    with pytest.raises(RuntimeError, match="owner_unavailable"):
        owner.dispatch(request["id"], backend)
    assert owner.broken and owner.generation is None and not backend.held
    assert owner.receipt(request["id"])["state"] == "dispatched"
    assert backend.effects == [request["id"]]
    with pytest.raises(RuntimeError, match="owner_unavailable"):
        owner.close(backend)
    assert owner.closed


def crash_child(root, name, phase):
    """Owned test process abruptly dies at a simulated effect boundary."""
    owner = InputCoordinator(root / "journal", claim_name=name)
    backend = SimulatedInput()
    write_status(root / "native.json", probe_process(os.getpid()))
    owner.grant(reason="Synthetic crash fixture")
    request = command(owner, backend)
    owner.submit(request)
    write_status(root / "request.json", request)
    if phase == "queued":
        os._exit(30)

    def fail():
        write_status(root / "effect.json", dict(phase=phase, effects=backend.effects,
                                                 held=backend.held, synthetic=True))
        os._exit(31 if phase == "before" else 32)

    setattr(backend, phase + "_effect", fail)
    owner.dispatch(request["id"], backend)


@pytest.mark.parametrize("phase,code", [("queued", 30), ("before", 31), ("after", 32)])
def test_actual_process_death_reconciles_receipts_without_repeating_effect(tmp_path, phase, code):
    name = claim()
    script = ("from pathlib import Path; import sys; "
              "from tests.test_input_coordinator import crash_child; "
              "crash_child(Path(sys.argv[1]),sys.argv[2],sys.argv[3])")
    with ((tmp_path / "child.stdout").open("wb") as out,
          (tmp_path / "child.stderr").open("wb") as err):
        child = subprocess.Popen([sys.executable, "-B", "-c", script, str(tmp_path), name, phase],
                                 stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
        assert child.wait(20) == code
    native = json.loads((tmp_path / "native.json").read_text())
    assert probe_process(native["pid"])["state"] in {"absent", "exited"}
    request = json.loads((tmp_path / "request.json").read_text())
    owner = InputCoordinator(tmp_path / "journal", claim_name=name)
    backend = SimulatedInput()
    try:
        expected = "cancelled" if phase == "queued" else "unknown"
        assert owner.receipt(request["id"])["state"] == expected
        assert owner.submit(request)["state"] == expected
        assert owner.dispatch(request["id"], backend)["state"] == expected
        assert not backend.effects
        if phase != "queued":
            effect = json.loads((tmp_path / "effect.json").read_text())
            assert len(effect["effects"]) == (phase == "after")
            with pytest.raises(RuntimeError, match="not_reconciled"):
                owner.grant(reason="Uncertain prior action must not be retried")
            owner.reconcile(request["id"], note="Crash inspected; no physical exactly-once claim")
        fresh = owner.grant(reason="Fresh permission after explicit reconciliation")
        assert fresh != request["generation"]
    finally:
        owner.close(backend)
