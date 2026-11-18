"""Monitoring-loss delivery uses fake PCM/transport, never a headset."""
import json

import pytest

from simcoach.live.fuel_radio import LOSS_PHRASE, FuelPhrase, FuelRadio
from tests.test_fuel_radio import FakeTransport, make_phrase
from tests.test_fuel_recovery import engine as engine
from tests.test_fuel_recovery import sample, step, unstable
from tests.test_race_watch import BASE


@pytest.fixture
def rig(engine, tmp_path):
    now = [BASE + .4]

    def clock():
        return now[0]

    transport = FakeTransport(clock)
    radio = FuelRadio(engine, tmp_path / "radio.jsonl",
                      FuelPhrase.load(make_phrase(tmp_path / "fuel")),
                      lambda: ("source-a", sample(now[0] - BASE)), transport, clock=clock,
                      loss_phrase=FuelPhrase.load(make_phrase(tmp_path / "loss", text=LOSS_PHRASE),
                                                 expected_text=LOSS_PHRASE))
    yield radio, engine, transport, now
    radio.close()


def lose(engine, second=.4):
    return step(engine, second, sample(second) | dict(coverage="mapping_unavailable"))


def test_loss_plays_once_with_own_identity_and_local_evidence(rig, tmp_path):
    radio, engine, transport, now = rig
    result = lose(engine)
    assert radio.tick()["loss_delivery_state"] == "none"
    now[0] = BASE + .8
    assert radio.tick()["loss_delivery_state"] == "none"
    now[0] = BASE + 1
    state = radio.tick()
    assert state["loss_delivery_state"] == "playing"
    assert state["loss_transmission_id"] == f"fuel_loss:{result['supervision_loss']['id']}"
    assert transport.started == [radio.loss_phrase.wav_path]
    assert state["fuel_state"] == "unarmed" and state["audible_delivery_verified"] is False
    now[0] = BASE + 1.6
    assert radio.tick()["loss_delivery_state"] == "played"
    radio.tick()
    assert len(transport.started) == 1
    lines = [json.loads(line) for line in (tmp_path / "radio.jsonl").read_text().splitlines()]
    assert any(row["state"] == "played" and row["driver_heard"] is None for row in lines)


def test_recovery_before_debounce_never_announces_loss(rig):
    radio, engine, transport, now = rig
    step(engine, .4, unstable(.4))
    radio.tick()
    step(engine, .6)
    step(engine, .8)
    now[0] = BASE + 1
    assert radio.tick()["loss_delivery_state"] == "none"
    assert not transport.started


def test_reapproval_interrupts_loss_and_does_not_replay(rig):
    radio, engine, transport, now = rig
    lose(engine)
    now[0] = BASE + 1
    assert radio.tick()["loss_delivery_state"] == "playing"
    step(engine, 1)
    step(engine, 1.2)
    assert engine.arm(cockpit_litres=8, verified_at=BASE + 1.2, now=BASE + 1.2,
                      source_id="source-a", solo_player_confirmed=True, note="new test approval")
    now[0] = BASE + 1.2
    assert radio.tick()["loss_delivery_state"] == "interrupted"
    assert transport.stopped == 1
    radio.tick()
    assert len(transport.started) == 1


def test_mute_consumes_loss_and_unmute_does_not_replay(rig):
    radio, engine, transport, now = rig
    radio.set_muted(True)
    lose(engine)
    now[0] = BASE + 1
    assert radio.tick()["loss_delivery_state"] == "suppressed"
    radio.set_muted(False)
    assert radio.tick()["delivery_health"] == "degraded"
    assert not transport.started


@pytest.mark.parametrize("time,expected", [(4, "suppressed"), (5.4, "expired")])
def test_no_delayed_call_after_supervisor_stalls_or_warning_expires(rig, time, expected):
    radio, engine, transport, now = rig
    lose(engine)
    now[0] = BASE + time
    assert radio.tick()["loss_delivery_state"] == expected
    assert not transport.started


def test_fresh_observations_do_not_extend_episode_deadline(rig):
    radio, engine, transport, now = rig
    lose(engine)
    for second in (1, 2, 3, 4, 5.4):
        step(engine, second)
    now[0] = BASE + 5.4
    assert radio.tick()["loss_delivery_state"] == "expired"
    assert not transport.started


def test_preparation_racing_recovery_refuses_native_output(rig):
    radio, engine, transport, now = rig
    lose(engine)
    now[0] = BASE + 1

    def recover():
        step(engine, 1)
        step(engine, 1.2)
        assert engine.arm(cockpit_litres=8, verified_at=BASE + 1.2, now=BASE + 1.2,
                          source_id="source-a", solo_player_confirmed=True,
                          note="new test approval")
        now[0] = BASE + 1.2

    transport.prepare = recover
    assert radio.tick()["loss_delivery_state"] == "expired"
    assert not transport.started


def test_loss_warning_failure_is_visible(rig):
    radio, engine, transport, now = rig
    lose(engine)
    now[0] = BASE + 1
    transport.fail_start = True
    state = radio.tick()
    assert state["loss_delivery_state"] == "failed"
    assert state["delivery_health"] == "degraded"


def test_final_permission_read_cannot_outlive_loss_observation(rig):
    radio, engine, transport, now = rig
    lose(engine)
    now[0] = BASE + 1
    final_reads = 0

    def prepare():
        nonlocal final_reads
        final_reads = 1

    def output_permitted():
        nonlocal final_reads
        if final_reads:
            final_reads += 1
            if final_reads == 3:
                now[0] = BASE + .4 + engine.max_age + .1
        return True

    transport.prepare = prepare
    radio.output_permitted = output_permitted
    state = radio.tick()
    assert final_reads == 3 and not transport.started
    assert state["loss_delivery_state"] == "expired"


def test_changed_loss_package_refuses_output(rig):
    radio, engine, transport, now = rig
    lose(engine)
    radio.loss_phrase.manifest_path.write_text("{}")
    now[0] = BASE + 1
    assert radio.tick()["loss_delivery_state"] == "suppressed"
    assert not transport.started


def test_invalid_fuel_call_stops_before_loss_warning_uses_same_transport(rig):
    radio, engine, transport, now = rig
    step(engine, .4, sample(.4, litres=1.9))
    radio.source_reader = lambda: ("source-a", sample(.4, litres=1.9))
    assert radio.tick()["delivery_state"] == "playing"
    lose(engine, .5)
    now[0] = BASE + .6
    assert radio.tick()["delivery_state"] == "interrupted"
    now[0] = BASE + 1.1
    assert radio.tick()["loss_delivery_state"] == "playing"
    assert transport.started == [radio.phrase.wav_path, radio.loss_phrase.wav_path]
    assert transport.stopped == 1


def test_phrase_type_cannot_be_swapped(rig, tmp_path):
    radio, engine, transport, _ = rig
    with pytest.raises(ValueError, match="invalid_loss_phrase"):
        FuelRadio(engine, tmp_path / "other.jsonl", radio.phrase, radio.source_reader,
                  transport, loss_phrase=radio.phrase)
    with pytest.raises(ValueError, match="unsupported_fuel_phrase_manifest"):
        FuelPhrase.load(radio.loss_phrase.manifest_path.parent)


def test_reopening_dispatcher_does_not_repeat_a_delivered_loss(rig, tmp_path):
    radio, engine, transport, now = rig
    lose(engine)
    now[0] = BASE + 1
    radio.tick()
    now[0] = BASE + 1.6
    radio.tick()
    radio.close()
    second_transport = FakeTransport(lambda: now[0])
    second = FuelRadio(engine, tmp_path / "radio.jsonl", radio.phrase,
                       radio.source_reader, second_transport, clock=lambda: now[0],
                       loss_phrase=radio.loss_phrase)
    try:
        assert second.tick()["loss_delivery_state"] == "played"
        assert not second_transport.started
    finally:
        second.close()


def test_reapproval_before_output_cancels_loss_warning(rig):
    radio, engine, transport, now = rig
    lose(engine)
    step(engine, .6)
    step(engine, .8)
    assert engine.arm(cockpit_litres=8, verified_at=BASE + .8, now=BASE + .8,
                      source_id="source-a", solo_player_confirmed=True, note="new test approval")
    now[0] = BASE + 1
    assert radio.tick()["loss_delivery_state"] == "none"
    assert not transport.started


def test_visit_host_binds_loss_package_and_priority_mute(engine, tmp_path):
    from simcoach.live.visit_audio import VisitAudio
    from tests.test_visit_audio import command

    (tmp_path / "receipts").mkdir()
    now = [BASE + .4]

    def clock():
        return now[0]

    transport = FakeTransport(clock)
    host = VisitAudio(tmp_path, engine, archive=tmp_path,
                      phrase=make_phrase(tmp_path / "fuel"), selection=tmp_path / "unused",
                      loss_phrase=make_phrase(tmp_path / "loss", text=LOSS_PHRASE),
                      clock=clock, factory=lambda: transport)
    try:
        assert host.snapshot()["loss_warning_configured"] is True
        assert host.snapshot()["muted"] is True
        now[0] = BASE + .5
        command(host, muted=False, at=now[0])
        assert host.tick()["muted"] is False
        lose(engine, .6)
        now[0] = BASE + 1.2
        assert host.tick()["loss_delivery_state"] == "playing"
        now[0] = BASE + 1.3
        command(host, muted=True, at=now[0], identity="b" * 32)
        assert host.tick()["loss_delivery_state"] == "interrupted"
        assert not transport.active
    finally:
        host.close()


def test_direct_visit_loss_package_requires_explicit_audio_before_effects(tmp_path):
    from simcoach.cli.visit import run_visit

    with pytest.raises(ValueError, match="loss_phrase_requires_audio"):
        run_visit(tmp_path, intent="synthetic", present=True, loss_phrase=tmp_path / "loss")
    assert not list(tmp_path.iterdir())


def test_suspension_is_silent_until_failed_recovery_then_one_warning(rig):
    radio, engine, transport, now = rig
    for second in (.4, 1, 2, 3):
        step(engine, second, unstable(second))
        now[0] = BASE + second
        assert radio.tick()["loss_delivery_state"] == "none"
    step(engine, 3.4)
    now[0] = BASE + 3.4
    assert radio.tick()["loss_delivery_state"] == "none"
    now[0] = BASE + 4
    assert radio.tick()["loss_delivery_state"] == "playing"
    assert len(transport.started) == 1
    message = radio.dispatcher.active
    assert message.created_at == BASE + 3.4 and message.expires_at == BASE + 8.4
