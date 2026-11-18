"""Bounded synthetic transport and actual restart evidence; no UI or provider."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

from simcoach.live.input_coordinator import InputCoordinator, SimulatedInput
from simcoach.live.input_gateway import InputGateway, InputHTTP
from simcoach.live.visit_recovery import probe_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native owner")


def command(owner, backend):
    before = backend.observe()
    return dict(
        id=uuid4().hex,
        generation=owner.generation,
        recipe_sha256="a" * 64,
        before=before,
        action="select",
        target="Start",
        expected_state="garage",
        deadline=before["at"] + 1.5,
    )


def envelope(binding, op="submit", command_id=None):
    value = {key: binding[key] for key in ("schema_version", "owner", "binding", "principal")}
    value["op"] = op
    if command_id is not None:
        value["command_id"] = command_id
    return value


@pytest.fixture
def rig(tmp_path):
    clock = [10.0]
    owner = InputCoordinator(
        tmp_path / "journal",
        clock=lambda: clock[0],
        claim_name="Local\\ORC.InputGatewayTest." + uuid4().hex,
    )
    backend = SimulatedInput(clock=lambda: clock[0])
    owner.grant(reason="Synthetic trusted Chief")
    gateway = InputGateway(owner, backend)
    request = command(owner, backend)
    binding = gateway.bind("rig-engineer", [request])
    yield gateway, owner, backend, binding, request, clock
    if not gateway.closed:
        try:
            gateway.close()
        except RuntimeError:
            assert owner.broken
    if not owner.closed:
        try:
            owner.close(backend)
        except RuntimeError:
            assert owner.broken and owner.closed


def call(rig, op="submit"):
    gateway, _, _, binding, request, _ = rig
    return gateway.handle(
        binding["token"], envelope(binding, op, request["id"] if op != "cancel" else None)
    )


def test_intake_is_exact_idempotent_and_separate_from_dispatch(rig):
    gateway, owner, backend, binding, request, _ = rig
    first = call(rig)
    assert first == call(rig)
    assert first["receipt"]["state"] == "queued"
    assert first["process"]["pid"] == os.getpid()
    assert not backend.effects
    assert owner.db.execute("SELECT count(*) FROM commands").fetchone()[0] == 1
    request["target"] = "Changed after binding"
    assert call(rig)["receipt"] == first["receipt"]
    assert owner.dispatch(request["id"], backend)["state"] == "verified"
    assert call(rig, "inspect")["receipt"]["state"] == "verified"
    events = owner.db.execute("SELECT kind,value FROM events").fetchall()
    assert binding["token"] not in json.dumps([tuple(row) for row in events])
    assert {"gateway_opened", "gateway_bound", "gateway_intake"} <= {row[0] for row in events}


@pytest.mark.parametrize("field", ["owner", "binding", "principal"])
def test_caller_cannot_substitute_identity(rig, field):
    gateway, owner, backend, binding, request, _ = rig
    value = envelope(binding, command_id=request["id"])
    value[field] = "wrong"
    with pytest.raises(PermissionError, match="binding_mismatch"):
        gateway.handle(binding["token"], value)
    assert owner.receipt(request["id"]) is None and not backend.effects


@pytest.mark.parametrize("token", [None, "bad", "0" * 64, "é" * 64])
def test_wrong_credentials_cannot_inspect_or_cancel(rig, token):
    gateway, owner, _, binding, _, _ = rig
    with pytest.raises(PermissionError, match="caller_unverified"):
        gateway.handle(token, envelope(binding, "cancel"))
    assert owner.generation == binding["generation"]


@pytest.mark.parametrize("op", ["grant", "dispatch", [], None])
def test_no_remote_grant_dispatch_or_unknown_operations(rig, op):
    gateway, _, backend, binding, _, _ = rig
    with pytest.raises(ValueError, match="invalid_input_envelope"):
        gateway.handle(binding["token"], envelope(binding, op))
    assert not backend.effects


def test_unbound_id_and_extra_command_body_are_refused(rig):
    gateway, _, _, binding, request, _ = rig
    with pytest.raises(PermissionError, match="not_bound"):
        gateway.handle(binding["token"], envelope(binding, command_id=uuid4().hex))
    value = envelope(binding, command_id=request["id"]) | {"command": request}
    with pytest.raises(ValueError, match="invalid_input_envelope"):
        gateway.handle(binding["token"], value)


def test_one_gateway_and_one_binding_per_generation(rig):
    gateway, owner, backend, _, request, _ = rig
    with pytest.raises(RuntimeError, match="already_owned"):
        InputGateway(owner, backend)
    with pytest.raises(ValueError, match="already_bound"):
        gateway.bind("other", [request])


def test_cancel_receipt_survives_repeat_and_late_submit_cannot_revive(rig):
    _, owner, backend, _, request, _ = rig
    call(rig)
    result = call(rig, "cancel")
    assert result["cancellation"] == dict(state="revoked", release_confirmed=True)
    assert result == call(rig, "cancel")
    with pytest.raises(PermissionError, match="revoked"):
        call(rig)
    assert call(rig, "inspect")["receipt"]["state"] == "cancelled"
    assert owner.dispatch(request["id"], backend)["state"] == "cancelled"
    assert not backend.effects and owner.generation is None


def test_old_caller_cannot_cancel_new_generation(rig):
    gateway, owner, backend, binding, _, _ = rig
    call(rig, "cancel")
    fresh = owner.grant(reason="New explicit Chief grant")
    with pytest.raises(PermissionError, match="generation_replaced"):
        call(rig, "cancel")
    assert owner.generation == fresh
    new_binding = gateway.bind("rig-engineer", [command(owner, backend)])
    assert new_binding["token"] != binding["token"]
    with pytest.raises(PermissionError, match="caller_unverified"):
        call(rig, "cancel")


def test_generation_change_between_authentication_and_cancel_is_rechecked(rig, monkeypatch):
    gateway, owner, backend, _, _, _ = rig
    original = gateway._cancel
    fresh = []

    def interleave(bound):
        owner.takeover(reason="Chief changed context", backend=backend)
        fresh.append(owner.grant(reason="New context permission"))
        return original(bound)

    monkeypatch.setattr(gateway, "_cancel", interleave)
    with pytest.raises(PermissionError, match="generation_replaced"):
        call(rig, "cancel")
    assert owner.generation == fresh[0]


def test_expired_bound_command_is_not_admitted(rig):
    _, owner, backend, _, request, clock = rig
    clock[0] = 12
    with pytest.raises(ValueError, match="expired"):
        call(rig)
    assert owner.receipt(request["id"]) is None and not backend.effects


def test_cancel_during_dispatch_prevents_simulated_effect(rig):
    _, owner, backend, _, request, _ = rig
    call(rig)
    entered, proceed = threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert proceed.wait(3)

    backend.before_effect = wait
    results = []
    thread = threading.Thread(target=lambda: results.append(owner.dispatch(request["id"], backend)))
    thread.start()
    try:
        assert entered.wait(2)
        assert call(rig, "cancel")["cancellation"]["release_confirmed"]
    finally:
        proceed.set()
        thread.join(3)
    assert not thread.is_alive()
    assert results[0]["state"] == "unknown" and not backend.effects
    with pytest.raises(RuntimeError, match="not_reconciled"):
        owner.grant(reason="Unknown action cannot be bypassed")


def test_storage_failure_during_cancel_is_unknown_not_confirmed(rig):
    _, owner, backend, _, _, _ = rig
    call(rig)
    owner.db.execute(
        "CREATE TRIGGER fail_cancel BEFORE INSERT ON events "
        "WHEN NEW.kind='control_revoked' "
        "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
    )
    owner.db.commit()
    result = call(rig, "cancel")
    assert result["cancellation"] == dict(
        state="unknown", release_confirmed=False, error="IntegrityError"
    )
    assert owner.broken and owner.generation is None and not backend.held


def test_intake_audit_storage_failure_prevents_command_admission(rig):
    _, owner, backend, _, request, _ = rig
    owner.db.execute(
        "CREATE TRIGGER fail_intake BEFORE INSERT ON events "
        "WHEN NEW.kind='gateway_intake' "
        "BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END"
    )
    owner.db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="synthetic audit failure"):
        call(rig)
    assert owner.broken and owner.generation is None
    assert owner.receipt(request["id"]) is None and not backend.effects


def test_gateway_closure_revokes_queued_work_without_remote_cancel(rig):
    gateway, owner, backend, binding, request, _ = rig
    call(rig)
    gateway.close()
    assert owner.generation is None
    assert owner.receipt(request["id"])["state"] == "cancelled"
    with pytest.raises(RuntimeError, match="gateway_unavailable"):
        gateway.handle(binding["token"], envelope(binding, "cancel"))
    assert not backend.effects


def post(service, binding, value, **headers):
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    request = urllib.request.Request(
        service.endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + binding["token"],
            **headers,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=4)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def test_actual_http_submit_inspect_cancel_and_shutdown(rig):
    gateway, owner, backend, binding, request, _ = rig
    service = InputHTTP(gateway)
    try:
        status, result = post(service, binding, envelope(binding, command_id=request["id"]))
        assert status == 200 and result["receipt"]["state"] == "queued"
        assert not backend.effects
        status, result = post(service, binding, envelope(binding, "inspect", request["id"]))
        assert status == 200 and result["owner"] == binding["owner"]
        status, result = post(service, binding, envelope(binding, "cancel"))
        assert status == 200 and result["cancellation"]["state"] == "revoked"
    finally:
        service.close()
    assert not service.thread.is_alive() and owner.generation is None


@pytest.mark.parametrize(
    "body,headers",
    [
        (b'{"op":"submit","op":"cancel"}', {}),
        (b"{}", {"Origin": "http://untrusted.invalid"}),
        (b"{}", {"Host": "untrusted.invalid"}),
        (b"x" * 4097, {}),
        (b"NaN", {}),
    ],
)
def test_http_bad_envelopes_do_not_change_authority(rig, body, headers):
    gateway, owner, backend, binding, _, _ = rig
    service = InputHTTP(gateway)
    try:
        status, _ = post(service, binding, body, **headers)
        assert status == 400
        assert owner.generation == binding["generation"] and not backend.effects
    finally:
        service.close()


def gateway_crash_child(root, claim):
    owner = InputCoordinator(root / "journal", claim_name=claim)
    backend = SimulatedInput()
    owner.grant(reason="Synthetic crash fixture")
    gateway = InputGateway(owner, backend)
    request = command(owner, backend)
    binding = gateway.bind("rig-engineer", [request])
    gateway.handle(binding["token"], envelope(binding, command_id=request["id"]))
    (root / "prior.json").write_text(json.dumps(dict(binding=binding, command=request)))
    os._exit(61)


def test_actual_owner_death_invalidates_gateway_credentials(tmp_path):
    claim = "Local\\ORC.GatewayCrash." + uuid4().hex
    script = (
        "from pathlib import Path; from tests.test_input_gateway import gateway_crash_child; "
        "import sys; gateway_crash_child(Path(sys.argv[1]), sys.argv[2])"
    )
    args = [sys.executable, "-c", script, str(tmp_path), claim]
    child = subprocess.run(
        args, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW
    )
    (tmp_path / "child-result.json").write_text(
        json.dumps(
            dict(
                args=args,
                exit_code=child.returncode,
                stdout=child.stdout.decode(),
                stderr=child.stderr.decode(),
            )
        )
    )
    assert child.returncode == 61, child.stderr.decode()
    prior = json.loads((tmp_path / "prior.json").read_text())
    assert probe_process(prior["binding"]["process"]["pid"])["state"] in {"absent", "exited"}
    owner = InputCoordinator(tmp_path / "journal", claim_name=claim)
    backend = SimulatedInput()
    gateway = InputGateway(owner, backend)
    try:
        assert gateway.epoch != prior["binding"]["owner"]
        assert owner.receipt(prior["command"]["id"])["state"] == "cancelled"
        with pytest.raises(PermissionError, match="caller_unverified"):
            gateway.handle(
                prior["binding"]["token"],
                envelope(prior["binding"], command_id=prior["command"]["id"]),
            )
        assert owner.generation is None and not backend.effects
        owner.grant(reason="Fresh explicit grant after native owner exit")
        new = gateway.bind("rig-engineer", [command(owner, backend)])
        assert new["token"] != prior["binding"]["token"]
        with pytest.raises(PermissionError):
            gateway.handle(new["token"], envelope(prior["binding"], "cancel"))
    finally:
        gateway.close()
        owner.close(backend)
