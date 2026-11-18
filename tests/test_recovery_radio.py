"""Recovery speech requires actual current supervision and a prior output attempt."""
import pytest

from simcoach.live.fuel_radio import LOSS_PHRASE, RECOVERY_PHRASE, FuelPhrase, FuelRadio
from tests.test_fuel_radio import FakeTransport, make_phrase
from tests.test_fuel_recovery import engine as engine
from tests.test_fuel_recovery import sample, step, unstable
from tests.test_loss_radio import lose
from tests.test_race_watch import BASE


@pytest.fixture
def rig(engine, tmp_path):
    now = [BASE + .4]

    def clock():
        return now[0]

    transport = FakeTransport(clock)
    radio = FuelRadio(engine, tmp_path / "radio.jsonl",
                      FuelPhrase.load(make_phrase(tmp_path / "fuel")),
                      lambda: ("source-a", sample(round(now[0] - BASE, 6))),
                      transport, clock=clock,
                      loss_phrase=FuelPhrase.load(make_phrase(tmp_path / "loss", text=LOSS_PHRASE),
                                                 expected_text=LOSS_PHRASE),
                      recovery_phrase=FuelPhrase.load(
                          make_phrase(tmp_path / "recovery", text=RECOVERY_PHRASE),
                          expected_text=RECOVERY_PHRASE))
    yield radio, engine, transport, now
    radio.close()


def warn(rig, *, finish=True):
    radio, engine, _, now = rig
    lose(engine)
    now[0] = BASE + 1
    assert radio.tick()["loss_delivery_state"] == "playing"
    if finish:
        now[0] = BASE + 1.6
        assert radio.tick()["loss_delivery_state"] == "played"


def reapprove(rig, second=2):
    _, engine, _, now = rig
    step(engine, second - .2)
    step(engine, second)
    now[0] = BASE + second
    assert engine.arm(cockpit_litres=8, verified_at=now[0], now=now[0], source_id="source-a",
                      solo_player_confirmed=True, note="synthetic reapproval")


def test_recovery_radio_rechecks_source_clock_before_output(rig):
    radio, _, transport, now = rig
    warn(rig)
    reapprove(rig)

    def jump():
        now[0] = BASE + 2.2
        radio.source_reader = lambda: ("source-a", sample(2.2) | {"offset_s": 22.2})

    transport.prepare = jump
    radio.tick()
    assert transport.started == [radio.loss_phrase.wav_path]
    assert radio.guard_reason == "recovery_source_clock_changed"


def test_loss_then_reapproval_then_one_recovery(rig):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)
    state = radio.tick()
    assert state["recovery_delivery_state"] == "playing"
    assert transport.started == [radio.loss_phrase.wav_path, radio.recovery_phrase.wav_path]
    now[0] = BASE + 2.6
    step(engine, 2.6)
    assert radio.tick()["recovery_delivery_state"] == "played"
    radio.tick()
    assert len(transport.started) == 2
    assert radio.snapshot()["audible_delivery_verified"] is False


def test_cut_short_confirmed_loss_output_gets_clarification(rig):
    radio, _, transport, _ = rig
    warn(rig, finish=False)
    reapprove(rig, 1.2)
    state = radio.tick()
    assert state["loss_delivery_state"] == "interrupted"
    assert state["recovery_delivery_state"] == "playing"
    assert transport.stopped == 1 and len(transport.started) == 2


@pytest.mark.parametrize("mode", ["muted", "not_submitted", "failed_start", "transient"])
def test_silent_loss_does_not_generate_recovery_chatter(rig, mode):
    radio, engine, transport, now = rig
    if mode == "muted":
        radio.set_muted(True)
    if mode == "failed_start":
        transport.fail_start = True
    if mode == "transient":
        step(engine, .4, unstable(.4))
        step(engine, .6)
        step(engine, .8)
    else:
        lose(engine)
    now[0] = BASE + 1
    if mode != "not_submitted":
        radio.tick()
    if mode != "transient":
        reapprove(rig)
    radio.set_muted(False)
    transport.fail_start = False
    assert radio.tick()["recovery_delivery_state"] == "none"


@pytest.mark.parametrize("change", [
    {"mCarName": "other"}, {"mGameState": 3}, {"mRaceState": 3},
    {"mFuelCapacity": 40}, {"mFuelLevel": .9}, {"mCurrentLap": 0},
])
def test_fresh_read_change_blocks_recovery_without_mutating_supervisor(rig, change):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)
    arm = engine.arm_id
    radio.source_reader = lambda: ("source-a", sample(now[0] - BASE, **change))
    assert radio.tick()["recovery_delivery_state"] == "suppressed"
    assert engine.arm_id == arm and len(transport.started) == 1


def test_missing_source_and_stale_local_state_block_recovery(rig):
    radio, _, transport, _ = rig
    warn(rig)
    reapprove(rig)

    def missing():
        raise OSError("synthetic unavailable source")

    radio.source_reader = missing
    assert radio.tick()["recovery_delivery_state"] == "suppressed"
    assert len(transport.started) == 1


def test_relapse_interrupts_recovery_and_new_loss_keeps_own_identity(rig):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)
    radio.tick()
    old = radio.snapshot()["loss_transmission_id"]
    lose(engine, 2.1)
    now[0] = BASE + 2.1
    assert radio.tick()["recovery_delivery_state"] == "interrupted"
    now[0] = BASE + 2.7
    state = radio.tick()
    assert state["loss_delivery_state"] == "playing" and state["loss_transmission_id"] != old
    assert len(transport.started) == 3


def test_mute_consumes_recovery_occurrence(rig):
    radio, _, transport, _ = rig
    warn(rig)
    reapprove(rig)
    radio.set_muted(True)
    assert radio.tick()["recovery_delivery_state"] == "suppressed"
    radio.set_muted(False)
    assert radio.tick()["delivery_health"] == "degraded"
    assert len(transport.started) == 1


@pytest.mark.parametrize("second", [5, 7])
def test_stale_supervisor_or_expired_resolution_never_announces_recovery(rig, second):
    radio, _, transport, now = rig
    warn(rig)
    reapprove(rig)
    now[0] = BASE + second
    assert radio.tick()["recovery_delivery_state"] in {"suppressed", "expired"}
    assert len(transport.started) == 1


def test_source_io_racing_relapse_cannot_start_recovery(rig):
    radio, engine, transport, _ = rig
    warn(rig)
    reapprove(rig)

    def changed():
        lose(engine, 2)
        return "source-a", sample(2)

    radio.source_reader = changed
    assert radio.tick()["recovery_delivery_state"] == "suppressed"
    assert len(transport.started) == 1


def test_reopen_preserves_consumed_recovery(rig, tmp_path):
    radio, engine, _, now = rig
    warn(rig)
    reapprove(rig)
    radio.tick()
    now[0] = BASE + 2.6
    radio.tick()
    radio.close()
    transport = FakeTransport(lambda: now[0])
    second = FuelRadio(engine, tmp_path / "radio.jsonl", radio.phrase, radio.source_reader,
                       transport, clock=lambda: now[0], loss_phrase=radio.loss_phrase,
                       recovery_phrase=radio.recovery_phrase)
    try:
        assert second.tick()["recovery_delivery_state"] == "played"
        assert not transport.started
    finally:
        second.close()


def test_urgent_fuel_return_preempts_recovery(rig):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)
    assert radio.tick()["recovery_delivery_state"] == "playing"
    step(engine, 2.1, sample(2.1, litres=1.9))
    radio.source_reader = lambda: ("source-a", sample(2.1, litres=1.9))
    now[0] = BASE + 2.1
    state = radio.tick()
    assert state["delivery_state"] == "playing"
    assert state["recovery_delivery_state"] == "interrupted"
    assert transport.started[-1] == radio.phrase.wav_path


def test_native_preparation_rechecks_current_monitoring(rig):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)

    def fault():
        lose(engine, 2.1)
        now[0] = BASE + 2.1

    transport.prepare = fault
    assert radio.tick()["recovery_delivery_state"] == "expired"
    assert len(transport.started) == 1


def test_fresh_status_does_not_extend_recovery_deadline(rig):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)
    for second in (3, 4, 5, 6, 7):
        step(engine, second)
    now[0] = BASE + 7
    assert radio.tick()["recovery_delivery_state"] == "expired"
    assert len(transport.started) == 1


@pytest.mark.parametrize("passage", [2.9, 3.1, -0.1])
def test_final_permission_read_cannot_age_or_regress_recovery_evidence(rig, passage):
    radio, _, transport, now = rig
    warn(rig)
    reapprove(rig)
    prepared = False
    final_reads = 0

    def prepare():
        nonlocal prepared
        prepared = True

    def output_permitted():
        nonlocal final_reads
        if prepared:
            final_reads += 1
            if final_reads == 2:
                now[0] = BASE + 2 + passage
        return True

    transport.prepare = prepare
    radio.output_permitted = output_permitted
    state = radio.tick()
    assert final_reads == 2
    if passage == 2.9:
        assert state["recovery_delivery_state"] == "playing"
        assert transport.started[-1] == radio.recovery_phrase.wav_path
    elif passage < 0:
        assert len(transport.started) == 1
        assert state["delivery_health"] == "degraded"
        assert state["failure_type"] == "ValueError"
    else:
        assert len(transport.started) == 1
        assert state["recovery_delivery_state"] == "expired"
        assert state["reason"] == "evidence_expired_during_output_check"


def test_changed_recovery_package_cannot_play(rig):
    radio, _, transport, _ = rig
    warn(rig)
    reapprove(rig)
    radio.recovery_phrase.manifest_path.write_text("{}")
    assert radio.tick()["recovery_delivery_state"] == "suppressed"
    assert len(transport.started) == 1


def test_visit_binds_recovery_package_in_same_output_owner(engine, tmp_path):
    from simcoach.live.visit_audio import VisitAudio
    from tests.test_visit_audio import command

    (tmp_path / "receipts").mkdir()
    now = [BASE + .4]
    transport = FakeTransport(lambda: now[0])
    host = VisitAudio(tmp_path, engine, archive=tmp_path, selection=tmp_path / "unused",
                      phrase=make_phrase(tmp_path / "fuel"),
                      loss_phrase=make_phrase(tmp_path / "loss", text=LOSS_PHRASE),
                      recovery_phrase=make_phrase(tmp_path / "restore", text=RECOVERY_PHRASE),
                      clock=lambda: now[0], factory=lambda: transport)
    try:
        assert host.snapshot()["recovery_warning_configured"]
        host.radio.source_reader = lambda: ("source-a", sample(round(now[0] - BASE, 6)))
        now[0] = BASE + .5
        command(host, muted=False, at=now[0])
        host.tick()
        lose(engine, .6)
        now[0] = BASE + 1.2
        assert host.tick()["loss_delivery_state"] == "playing"
        now[0] = BASE + 1.8
        host.tick()
        reapprove((host.radio, engine, transport, now), 2.2)
        assert host.tick()["recovery_delivery_state"] == "playing"
        assert transport.started[-1] == host.radio.recovery_phrase.wav_path
    finally:
        host.close()


@pytest.mark.parametrize("options", [{}, {"enable_audio": True}])
def test_recovery_configuration_without_loss_audio_refused_before_effects(tmp_path, options):
    from simcoach.cli.visit import run_visit

    with pytest.raises(ValueError, match="recovery_phrase_requires_loss_audio"):
        run_visit(tmp_path, intent="synthetic", present=True, recovery_phrase=tmp_path, **options)
    assert not list(tmp_path.iterdir())

def test_recovery_radio_refuses_a_second_break_during_prepare(rig):
    radio, engine, transport, now = rig
    warn(rig)
    reapprove(rig)

    def reconnect():
        now[0] = BASE + 2.2
        radio.source_reader = lambda: ("source-a", sample(2.2) | {"continuity_generation": 1})

    transport.prepare = reconnect
    arm = engine.arm_id
    radio.tick()
    assert transport.started == [radio.loss_phrase.wav_path]
    assert radio.guard_reason == "recovery_source_continuity_changed"
    assert engine.arm_id == arm
