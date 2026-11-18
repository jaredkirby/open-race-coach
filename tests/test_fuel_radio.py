from __future__ import annotations

import hashlib
import json
import wave
from datetime import UTC, datetime

import pytest

from simcoach.live.fuel_radio import ArchiveStatusSource, FuelPhrase, FuelRadio
from simcoach.live.supervisor import Context, FuelPlan, Journal, Supervisor

BASE = 1_800_000_000.0
CONTEXT = Context("example car", "example track", "short", 1)


class Clock:
    now = BASE + 2

    def __call__(self):
        return self.now


def status(second, litres=2.0, **fields):
    return {
        "schema_version": 1, "status": "running", "coverage": "available",
        "latest_is_current": True, "error": None, "target_hz": 60,
        "updated_at": datetime.fromtimestamp(BASE + second, UTC).isoformat(),
        "samples": int(second) * 60 + 1, "offset_s": float(second),
        "latest": dict(mVersion=14, mSequenceNumber=int(second) * 2 + 2,
                       mFuelCapacity=33, mFuelLevel=litres / 33, mCarName=CONTEXT.car,
                       mTrackLocation=CONTEXT.track, mTrackVariation=CONTEXT.variation,
                       mSessionState=1, mGameState=2, mRaceState=2, mPitMode=0,
                       mCurrentLap=1) | fields,
    }


def observe(engine, second, litres=8.0, **fields):
    return engine.observe(status(second, litres, **fields), source_id="source-a",
                          now=BASE + second, elapsed=float(second))


def approve(engine, second=1):
    return engine.arm(cockpit_litres=8, verified_at=BASE + second, now=BASE + second,
                      source_id="source-a", solo_player_confirmed=True,
                      note="Synthetic cockpit approval, no live test")


def make_phrase(directory, *, duration=0.5, text="Pit this lap."):
    directory.mkdir()
    audio = directory / "radio.wav"
    rate, frames = 22050, int(22050 * duration)
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * frames)
    metadata = dict(schema_version=1, status="generated", text=text, audio_output_attempted=False,
                    sample_rate_hz=rate, frames=frames, duration_s=frames / rate,
                    radio_sha256=hashlib.sha256(audio.read_bytes()).hexdigest())
    (directory / "render.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


class FakeTransport:
    def __init__(self, clock):
        self.clock = clock
        self.started = []
        self.stopped = 0
        self.active = False
        self.start_at = None
        self.prepare = lambda: None
        self.fail_start = self.fail_stop = False

    def start(self, path, *, can_start):
        self.prepare()
        if not can_start():
            return False
        if self.fail_start:
            raise RuntimeError("fake unavailable device")
        self.started.append(path)
        self.active, self.start_at = True, self.clock()
        with wave.open(str(path)) as audio:
            self.duration = audio.getnframes() / audio.getframerate()
        return True

    def poll(self):
        if self.active and self.clock() - self.start_at < self.duration:
            return "playing"
        self.active = False
        return "played"

    def stop(self):
        self.stopped += 1
        if self.fail_stop:
            raise RuntimeError("fake unconfirmed stop")
        self.active = False


@pytest.fixture
def rig(tmp_path):
    engine_journal = Journal(tmp_path / "supervisor.sqlite3")
    engine = Supervisor(FuelPlan(CONTEXT, 0.5, 6, 2, 2, "synthetic estimate"), engine_journal)
    observe(engine, 0)
    observe(engine, 1)
    assert approve(engine)
    observe(engine, 2, 2)
    clock = Clock()
    transport = FakeTransport(clock)
    latest = {"identity": "source-a", "value": status(2)}
    def source():
        return latest["identity"], latest["value"]
    phrase = FuelPhrase.load(make_phrase(tmp_path / "phrase"))
    radio = FuelRadio(engine, tmp_path / "radio.jsonl", phrase, source, transport, clock=clock)
    yield radio, engine, transport, clock, latest
    transport.fail_stop = False
    radio.close()
    engine_journal.close()


@pytest.mark.parametrize("during_prepare", [False, True])
def test_fuel_radio_rechecks_source_clock_before_output(rig, during_prepare):
    radio, _, transport, clock, latest = rig

    def jump():
        clock.now = BASE + 3
        latest["value"] = status(3) | {"offset_s": 23.0}

    if during_prepare:
        transport.prepare = jump
    else:
        jump()
    radio.tick()
    assert not transport.started
    assert radio.guard_reason == "fresh_source_clock_changed"


def test_connected_warning_completes_but_does_not_claim_heard(rig, tmp_path):
    radio, engine, transport, clock, _ = rig
    state = radio.tick()
    assert state["fuel_state"] == "return_due" and state["delivery_state"] == "playing"
    assert engine.arm_id is not None and len(transport.started) == 1
    clock.now += 0.6
    state = radio.tick()
    assert state["delivery_state"] == "played" and state["delivery_health"] == "available"
    assert state["audible_delivery_verified"] is False
    radio.tick()
    assert len(transport.started) == 1
    rows = [json.loads(line) for line in (tmp_path / "radio.jsonl").read_text().splitlines()]
    assert rows[0]["source_at"] == engine.return_notice["source_at"] == BASE + 2
    assert rows[-1]["transport_start"]["confirmed"]["at"] == BASE + 2
    assert rows[-1]["audible_onset_at"] is None


def test_radio_fresh_checks_are_read_only_for_supervisor(rig):
    radio, engine, _, clock, latest = rig
    before = engine.journal.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    old_result, old_reading, arm_id = engine.last_result, engine.current, engine.arm_id
    clock.now = BASE + 3
    latest["value"] = status(3, 1.9)
    assert radio.tick()["delivery_state"] == "playing"
    after = engine.journal.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after and engine.last_result == old_result
    assert engine.current == old_reading and engine.arm_id == arm_id


@pytest.mark.parametrize("fields,reason", [
    ({"mPitMode": 2}, "fresh_source_not_driving"),
    ({"mRaceState": 4}, "fresh_source_not_driving"),
    ({"mGameState": 4}, "fresh_source_not_driving"),
    ({"mCarName": "different"}, "fresh_source_context_changed"),
    ({"mFuelCapacity": 34}, "fresh_capacity_changed"),
    ({"mFuelLevel": 0.1}, "fresh_refuel_detected"),
    ({"mCurrentLap": 0}, "fresh_source_progress_invalid"),
    ({"mSequenceNumber": 2}, "fresh_source_progress_invalid"),
    ({"mSequenceNumber": 3}, "fresh_source_unavailable_or_invalid"),
])
def test_fresh_source_changes_suppress_old_notice_without_mutating_engine(rig, fields, reason):
    radio, engine, transport, clock, latest = rig
    clock.now = BASE + 3
    latest["value"] = status(3, 1.9, **fields)
    original_arm = engine.arm_id
    state = radio.tick()
    assert state["delivery_state"] == "suppressed" and state["reason"] == reason
    assert state["delivery_health"] == "degraded" and state["fuel_state"] == "return_due"
    assert not transport.started and engine.arm_id == original_arm


def test_new_source_identity_refuses_output(rig):
    radio, _, transport, _, latest = rig
    latest["identity"] = "source-b"
    state = radio.tick()
    assert state["reason"] == "fresh_source_context_changed" and not transport.started


def test_missing_source_degrades_delivery_without_capture_actions(rig):
    radio, engine, transport, _, _ = rig

    def unavailable():
        raise OSError("fake file unavailable")

    radio.source_reader = unavailable
    state = radio.tick()
    assert state["delivery_health"] == "degraded" and not transport.started
    assert state["supervision_health"] == "healthy" and engine.return_notice is not None


def test_insufficient_remaining_lifetime_never_starts_a_truncated_call(rig):
    radio, _, transport, clock, latest = rig
    clock.now = BASE + 4.5
    latest["value"] = status(4, 1.9)
    state = radio.tick()
    assert state["delivery_state"] == "expired" and not transport.started
    assert state["reason"] == "insufficient_playback_lifetime"


def test_expensive_native_preparation_gets_fresh_evidence_guard(rig):
    radio, _, transport, _, latest = rig
    transport.prepare = lambda: latest.update(value=status(2, mPitMode=2))
    state = radio.tick()
    assert state["delivery_state"] == "expired" and not transport.started
    assert state["reason"] == "fresh_source_not_driving"


def test_slow_read_cannot_extend_evidence_lifetime(rig):
    radio, _, transport, clock, latest = rig

    def slow():
        clock.now += 4
        return latest["identity"], latest["value"]

    radio.source_reader = slow
    state = radio.tick()
    assert not transport.started and state["reason"] == "fresh_source_expired_during_read"


def test_snapshot_published_during_read_uses_post_io_freshness_time(rig):
    radio, _, transport, clock, _ = rig
    calls = [0]

    def concurrent_publication():
        calls[0] += 1
        clock.now = BASE + 2 + calls[0] * .01
        value = status(clock.now - BASE, 1.9)
        value["samples"] += calls[0]
        value["latest"]["mSequenceNumber"] += calls[0] * 2
        return "source-a", value

    radio.source_reader = concurrent_publication
    state = radio.tick()
    assert state["delivery_state"] == "playing" and len(transport.started) == 1


def test_muted_notice_is_consumed_and_unmute_does_not_replay(rig):
    radio, _, transport, _, _ = rig
    assert radio.set_muted(True)["muted"] is True
    assert radio.tick()["delivery_state"] == "suppressed"
    radio.set_muted(False)
    assert radio.tick()["delivery_health"] == "degraded"
    assert not transport.started


def test_mute_interrupts_in_progress_message(rig):
    radio, _, transport, _, _ = rig
    radio.tick()
    assert radio.set_muted(True)["delivery_state"] == "interrupted"
    assert transport.stopped == 1 and not transport.active


def test_changed_manifest_stops_in_progress_message(rig):
    radio, _, transport, _, _ = rig
    radio.tick()
    radio.phrase.manifest_path.write_text("{}", encoding="utf-8")
    state = radio.tick()
    assert state["delivery_state"] == "interrupted" and not transport.active
    assert state["reason"] == "fuel_phrase_changed"


def test_device_failure_is_degraded_while_fuel_remains_due(rig):
    radio, engine, transport, _, _ = rig
    transport.fail_start = True
    state = radio.tick()
    assert state["delivery_state"] == "failed" and state["delivery_health"] == "degraded"
    assert state["fuel_state"] == "return_due" and engine.health == "healthy"
    assert not transport.started


def test_unconfirmed_close_retains_owner_and_can_be_retried(rig, tmp_path):
    radio, engine, transport, clock, _ = rig
    radio.tick()
    transport.fail_stop = True
    state = radio.close()
    assert not state["closed"] and state["delivery_health"] == "degraded"
    with pytest.raises(RuntimeError, match="owns"):
        FuelRadio(engine, tmp_path / "radio.jsonl", radio.phrase, radio.source_reader,
                  FakeTransport(clock), clock=clock)
    transport.fail_stop = False
    assert radio.close()["closed"] is True


def test_restart_same_notice_never_replays(rig, tmp_path):
    radio, engine, transport, clock, _ = rig
    radio.tick()
    assert radio.close()["closed"]
    restarted = FuelRadio(engine, tmp_path / "radio.jsonl", radio.phrase, radio.source_reader,
                          transport, clock=clock)
    try:
        state = restarted.tick()
        assert state["delivery_state"] == "interrupted" and len(transport.started) == 1
    finally:
        restarted.close()


def test_disarm_prevents_old_message_and_fresh_arm_can_emit(rig):
    radio, engine, transport, clock, latest = rig
    radio.tick()
    observe(engine, 3, 8)  # Refuel cancels the original approval.
    clock.now = BASE + 3
    latest["value"] = status(3, 8)
    state = radio.tick()
    assert state["fuel_state"] == "unarmed" and not state["delivery_matches_current_notice"]
    assert len(transport.started) == 1
    observe(engine, 4, 8)
    assert approve(engine, 4)
    observe(engine, 5, 2)
    clock.now = BASE + 5
    latest["value"] = status(5)
    assert radio.tick()["delivery_state"] == "playing" and len(transport.started) == 2


def test_archive_reader_binds_run_identity_and_status(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps(dict(
        schema_version=1, expected_version=14, struct_size_bytes=20700)))
    (tmp_path / "status.json").write_text(json.dumps(status(2)))
    identity, value = ArchiveStatusSource(tmp_path)()
    assert identity == hashlib.sha256((tmp_path / "run.json").read_bytes()).hexdigest()
    assert value == status(2)


def test_archive_reader_retries_whole_transaction_and_preserves_diagnostics(tmp_path, monkeypatch):
    import errno
    from pathlib import Path

    (tmp_path / "run.json").write_text(json.dumps(dict(
        schema_version=1, expected_version=14, struct_size_bytes=20700)))
    (tmp_path / "status.json").write_text(json.dumps(status(2)))
    original = Path.read_text
    calls = []

    def read(path, *args, **kwargs):
        if path.name == "status.json":
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(errno.EACCES, "synthetic read race")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    diagnostics = []
    source = ArchiveStatusSource(tmp_path)
    source.diagnostic = diagnostics.append
    _, value = source()
    assert len(calls) == 2 and value == status(2)
    assert diagnostics[0]["recovered"] and diagnostics[0]["role"] == "archive_snapshot"


def test_archive_reader_changed_identity_or_corrupt_data_never_retried(tmp_path, monkeypatch):
    from simcoach.live import fuel_radio

    (tmp_path / "status.json").write_text(json.dumps(status(2)))
    identities = iter(["before", "after"])
    monkeypatch.setattr(fuel_radio, "source_identity", lambda _: next(identities))
    with pytest.raises(ValueError, match="archive_changed_during_read"):
        ArchiveStatusSource(tmp_path)()
    monkeypatch.setattr(fuel_radio, "source_identity", lambda _: "stable")
    (tmp_path / "status.json").write_text("broken JSON")
    with pytest.raises(ValueError):
        ArchiveStatusSource(tmp_path)()


@pytest.mark.parametrize("text,duration", [("Wrong phrase.", 0.5), ("Pit this lap.", 3.1)])
def test_phrase_rejects_wrong_content_or_long_audio(tmp_path, text, duration):
    with pytest.raises(ValueError):
        FuelPhrase.load(make_phrase(tmp_path / "phrase", text=text, duration=duration))


def test_phrase_rejects_substituted_waveform(tmp_path):
    directory = make_phrase(tmp_path / "phrase")
    audio = directory / "radio.wav"
    audio.write_bytes(audio.read_bytes()[:-2] + b"\x01\x00")
    with pytest.raises(ValueError, match="hash"):
        FuelPhrase.load(directory)

def test_fuel_radio_refuses_a_reconnect_during_device_prepare(rig):
    radio, engine, transport, clock, latest = rig

    def reconnect():
        clock.now = BASE + 3
        latest["value"] = status(3, 1.9) | {"continuity_generation": 1}

    transport.prepare = reconnect
    radio.tick()
    assert not transport.started
    assert radio.guard_reason == "fresh_source_continuity_changed"
    assert engine.arm_id is not None  # Final output verification is read-only.
