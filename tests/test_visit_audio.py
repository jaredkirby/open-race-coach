"""Visit host/control tests use an injected radio; no native output is opened."""

import hashlib
import json

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live import visit_audio


class FakeRadio:
    def __init__(self):
        self.muted, self.closed = False, False
        self.failure_type = None
        self.fail_close = self.fail_tick = self.fail_mute = False
        self.mutations = []
        self.close_calls = 0

    def snapshot(self):
        return dict(muted=self.muted, closed=self.closed, failure_type=self.failure_type,
                    delivery_health="available", audible_delivery_verified=False)

    def set_muted(self, value):
        if self.fail_mute:
            raise RuntimeError("synthetic mute error")
        self.mutations.append(value)
        self.muted = value  # Mirrors FuelRadio's requested state even when closed.
        return self.snapshot()

    def tick(self):
        if self.fail_tick:
            raise RuntimeError("synthetic tick error")
        return self.snapshot()

    def close(self):
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("synthetic unconfirmed stop")
        self.closed = True
        return self.snapshot()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    (tmp_path/"receipts").mkdir()
    now = [1000.0]
    radio = FakeRadio()
    monkeypatch.setattr(visit_audio.FuelPhrase, "load", lambda path: object())
    monkeypatch.setattr(visit_audio, "FuelRadio", lambda *args, **kwargs: radio)
    host = visit_audio.VisitAudio(tmp_path, object(), archive=tmp_path, phrase=tmp_path,
                                  selection=tmp_path, clock=lambda: now[0], factory=object)
    return host, radio, now


def command(host, *, muted, at=1000.1, identity="a"*32, **fields):
    write_status(host.run/"mute.json", dict(schema_version=1, command_id=identity,
                                          muted=muted, at=at, **fields))
    return identity


def receipt(host, identity):
    return json.loads((host.run/"receipts"/f"{identity}.json").read_text())


def unmute(rig):
    host, radio, now = rig
    now[0] = 1000.2
    identity = command(host, muted=False)
    host.tick()
    assert radio.muted is False
    return identity


def test_starts_muted_and_fresh_unmute_has_bound_receipt(rig):
    host, radio, _ = rig
    assert radio.mutations == [True]
    identity = unmute(rig)
    value = receipt(host, identity)
    assert value["accepted"] is value["applied"] is True
    assert value["command_id"] == identity
    assert len(value["command_fingerprint"]) == 64
    assert value["audio_heard"] is None
    before = radio.mutations[:]
    host.tick()
    assert radio.mutations == before


def test_injected_factory_does_not_assert_loaded_output_selection(rig):
    host, _, _ = rig
    assert host.snapshot()["configuration"] == dict(
        schema_version=1, transport="injected_factory", selection_sha256=None)


def test_selected_configuration_binds_loaded_bytes_not_later_file(tmp_path, monkeypatch):
    # Replace the native constructor; this check never opens an audio device.
    from simcoach.live import radio as radio_module
    chosen = radio_module.OutputSelection("Synthetic output", "a" * 64)
    selection = tmp_path / "selection.json"
    original = json.dumps(chosen.to_dict()).encode()
    selection.write_bytes(original)
    constructed = []
    monkeypatch.setattr(radio_module, "WindowsWaveTransport",
                        lambda **kwargs: constructed.append(kwargs) or object())
    monkeypatch.setattr(visit_audio.FuelPhrase, "load", lambda path: object())
    monkeypatch.setattr(visit_audio, "FuelRadio", lambda *args, **kwargs: FakeRadio())
    host = visit_audio.VisitAudio(tmp_path, object(), archive=tmp_path, phrase=tmp_path,
                                 selection=selection, clock=lambda: 1000.)
    assert constructed == [dict(enable_audio=True, selection=chosen)]
    expected = dict(schema_version=1, transport="selected_windows_wave",
                    selection_sha256=hashlib.sha256(original).hexdigest())
    assert host.snapshot()["configuration"] == expected
    selection.write_text('{"changed": true}')
    result = host.snapshot()
    result["configuration"]["selection_sha256"] = "b" * 64
    assert host.snapshot()["configuration"] == expected
    assert host.snapshot()["audible_delivery_verified"] is False


@pytest.mark.parametrize("payload", [b"x" * 65537, b"{}", b"\xff"],
                         ids=["oversized", "wrong-schema", "invalid-utf8"])
def test_invalid_selection_never_claims_configured_transport(tmp_path, payload):
    selection = tmp_path / "selection.json"
    selection.write_bytes(payload)
    host = visit_audio.VisitAudio(tmp_path, object(), archive=tmp_path, phrase=tmp_path,
                                 selection=selection, clock=lambda: 1000.)
    result = host.snapshot()
    assert result["configuration"]["transport"] == "unconfigured"
    assert result["delivery_health"] == "degraded" and result["closed"] is True


@pytest.mark.parametrize("at", [996, 1000, 1001])
def test_old_instance_stale_and_future_unmute_rejected(rig, at):
    host, radio, now = rig
    now[0] = 1000.5
    identity = command(host, muted=False, at=at)
    host.tick()
    assert radio.muted is True
    assert receipt(host, identity)["accepted"] is False


def test_older_unmute_cannot_undo_observed_mute(rig):
    host, radio, now = rig
    unmute(rig)
    now[0] = 1000.5
    command(host, muted=True, at=1000.4, identity="b"*32)
    host.tick()
    identity = command(host, muted=False, at=1000.3, identity="c"*32)
    host.tick()
    assert radio.muted is True
    assert receipt(host, identity)["reason"] == "stale_or_obsolete_unmute"


def test_older_mute_watermark_blocks_unseen_intermediate_unmute(rig):
    host, radio, now = rig
    now[0] = 1000.9
    command(host, muted=True, at=1000.1, intent_watermark=1000.7)
    host.tick()
    identity = command(host, muted=False, at=1000.5, identity="b"*32)
    host.tick()
    assert radio.muted is True
    assert receipt(host, identity)["accepted"] is False


@pytest.mark.parametrize("corruption", ["malformed", "conflict", "json", "identity"])
def test_changed_or_malformed_command_fails_closed_preserving_receipt(rig, corruption):
    host, radio, _ = rig
    identity = unmute(rig)
    historical = (host.run/"receipts"/f"{identity}.json").read_bytes()
    if corruption == "malformed":
        command(host, muted="false")
    elif corruption == "conflict":
        command(host, muted=False, at=1000.15)
    elif corruption == "json":
        (host.run/"mute.json").write_text("{")
    else:
        command(host, muted=False, identity="../unsafe")
    state = host.tick()
    assert radio.muted is True
    assert state["delivery_health"] == "degraded"
    assert (host.run/"receipts"/f"{identity}.json").read_bytes() == historical
    rejected = list((host.run/"receipts").glob("rejected-*.json"))
    assert len(rejected) == 1
    assert json.loads(rejected[0].read_text())["accepted"] is False
    host.tick()
    assert len(list((host.run/"receipts").glob("rejected-*.json"))) == 1


@pytest.mark.parametrize("state", ["unavailable", "closed", "degraded", "closing", "absent"])
def test_unmute_requires_available_active_owner(rig, state):
    host, radio, now = rig
    now[0] = 1000.2
    if state == "unavailable":
        host.radio = None
    elif state == "closed":
        radio.closed = True
    elif state == "degraded":
        radio.failure_type = "unconfirmed"
    elif state == "closing":
        host.closing = True
    identity = command(host, muted=False)
    host.tick(active_visit=state != "absent")
    assert receipt(host, identity)["accepted"] is False
    assert radio.muted is True


def test_safe_mute_can_be_acknowledged_after_confirmed_close(rig):
    host, radio, _ = rig
    host.close()
    identity = command(host, muted=True, at=900)
    state = host.tick()
    assert state["closed"] is state["muted"] is True
    assert receipt(host, identity)["accepted"] is True
    assert radio.mutations[-1] is True


def test_failed_close_keeps_owner_and_can_retry_without_escaping(rig):
    host, radio, _ = rig
    unmute(rig)
    radio.fail_close = True
    state = host.close()
    assert state["closed"] is False
    assert state["delivery_health"] == "degraded"
    assert state["host_error"] == "radio_close_unconfirmed"
    assert host.radio is radio
    radio.fail_close = False
    assert host.close()["closed"] is True


def test_tick_error_with_failed_stop_is_contained(rig):
    host, radio, _ = rig
    radio.fail_tick = radio.fail_close = True
    state = host.tick()
    assert state["closed"] is False
    assert host.closing is True
    assert host.radio is radio
    assert state["delivery_health"] == "degraded"


def test_receipt_publication_error_stops_radio_without_escaping(rig, monkeypatch):
    host, radio, now = rig
    now[0] = 1000.2
    command(host, muted=False)

    def fail_receipt(*args):
        raise OSError("synthetic receipt failure")

    monkeypatch.setattr(visit_audio, "write_status", fail_receipt)
    state = host.tick()
    assert radio.close_calls == 1
    assert state["closed"] is True
    assert state["delivery_health"] == "degraded"


def test_initial_mute_error_releases_or_retains_owner_explicitly(tmp_path, monkeypatch):
    radio = FakeRadio()
    radio.fail_mute = radio.fail_close = True
    monkeypatch.setattr(visit_audio.FuelPhrase, "load", lambda path: object())
    monkeypatch.setattr(visit_audio, "FuelRadio", lambda *args, **kwargs: radio)
    host = visit_audio.VisitAudio(tmp_path, object(), archive=tmp_path, phrase=tmp_path,
                                  selection=tmp_path, factory=object)
    assert host.radio is radio
    assert host.snapshot()["closed"] is False
    assert host.snapshot()["delivery_health"] == "degraded"


def test_clock_failure_is_isolated_during_construction(tmp_path):
    def clock():
        raise RuntimeError("synthetic clock failure")

    host = visit_audio.VisitAudio(tmp_path, object(), archive=tmp_path, phrase=tmp_path,
                                  selection=tmp_path, clock=clock, factory=object)
    assert host.snapshot()["delivery_health"] == "degraded"


@pytest.mark.parametrize("late_control", [None, "mute", "missing", "oversized"])
def test_fuel_radio_uses_priority_controls_without_replay(tmp_path, late_control):
    from simcoach.live.supervisor import Context, FuelPlan, Journal, Supervisor, source_identity
    from tests.test_fuel_radio import BASE, CONTEXT, Clock, FakeTransport, make_phrase, status

    archive = tmp_path / "archive"
    archive.mkdir()
    write_status(archive / "run.json", dict(schema_version=1, expected_version=14,
                                            struct_size_bytes=20700))
    identity = source_identity(archive)
    run = tmp_path / "visit"
    run.mkdir()
    (run / "receipts").mkdir()
    clock = Clock()
    clock.now = BASE
    transport = FakeTransport(clock)
    journal = Journal(run / "supervision.sqlite3")
    plan = FuelPlan(Context(CONTEXT.car, CONTEXT.track, CONTEXT.variation, 1),
                    0.5, 6, 2, 2, "synthetic connected test")
    engine = Supervisor(plan, journal)
    host = visit_audio.VisitAudio(
        run, engine, archive=archive, phrase=make_phrase(tmp_path / "phrase"),
        selection=tmp_path / "unused-selection", clock=clock, factory=lambda: transport)
    try:
        assert host.snapshot()["muted"] is True and not transport.started
        for second in (0, 1):
            clock.now = BASE + second
            value = status(second, litres=8)
            write_status(archive / "status.json", value)
            engine.observe(value, source_id=identity, now=clock(), elapsed=second)
        assert engine.arm(cockpit_litres=8, verified_at=clock(), now=clock(),
                          source_id=identity, solo_player_confirmed=True,
                          note="Synthetic cockpit evidence; no live check")
        clock.now += 0.1
        unmute_id = command(host, muted=False, at=clock())
        assert host.tick()["muted"] is False
        assert receipt(host, unmute_id)["accepted"] is True
        if late_control is None:
            control = run / "mute.json"
            control.write_text(json.dumps(json.loads(control.read_text()), indent=2))
            assert host.tick()["muted"] is False

        def change_control():
            if late_control == "mute":
                command(host, muted=True, at=clock(), identity="b" * 32)
            elif late_control == "missing":
                (run / "mute.json").unlink()
            elif late_control == "oversized":
                (run / "mute.json").write_bytes(b" " * 65537)

        transport.prepare = change_control
        clock.now = BASE + 2
        value = status(2, litres=2)
        write_status(archive / "status.json", value)
        engine.observe(value, source_id=identity, now=clock(), elapsed=2)
        state = host.tick()
        assert state["delivery_state"] == ("expired" if late_control else "playing")
        assert len(transport.started) == (0 if late_control else 1)
        arm_id = engine.arm_id

        clock.now += 0.1
        if late_control is None:
            command(host, muted=True, at=clock(), identity="b" * 32)
        state = host.tick()
        assert state["delivery_state"] == ("expired" if late_control else "interrupted")
        assert state["muted"] is True
        if late_control in {"missing", "oversized"}:
            expected = {
                "missing": "mute_command_missing",
                "oversized": "mute_command_too_large",
            }[late_control]
            assert state["delivery_health"] == "degraded" and state["host_error"] == expected
        else:
            assert receipt(host, "b" * 32)["applied"] is True
        assert not transport.active and engine.arm_id == arm_id
        clock.now += 0.1
        command(host, muted=False, at=clock(), identity="c" * 32)
        state = host.tick()
        assert state["muted"] is False and len(transport.started) == (0 if late_control else 1)
        assert state["audible_delivery_verified"] is False
    finally:
        host.close()
        journal.close()
    assert host.close()["closed"] is True
