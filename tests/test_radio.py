from __future__ import annotations

import ctypes
import json
import math
import wave
from array import array
from dataclasses import replace
from pathlib import Path

import pytest

from simcoach.live.radio import (
    Dispatcher,
    OutputDevice,
    OutputSelection,
    Transmission,
    WindowsWaveTransport,
    _output_devices,
    _WaveHeader,
    _WaveOutputCaps,
    main,
    process_radio,
    read_pcm,
)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class FakeTransport:
    def __init__(self):
        self.starts = []
        self.stops = 0
        self.result = "playing"
        self.fail_start = self.fail_stop = False

    def start(self, path, *, can_start):
        if self.fail_start:
            raise RuntimeError("device unavailable")
        if not can_start():
            return False
        self.starts.append(path)
        self.result = "playing"
        return True

    def poll(self):
        return self.result

    def stop(self):
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError("stop failed")


def wav(path: Path, frequency=1000, amplitude=0.4, seconds=0.3):
    rate = 22050
    data = array("h", (round(32767 * amplitude * math.sin(2 * math.pi * frequency * i / rate))
                       for i in range(round(seconds * rate))))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(data.tobytes())
    return path


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    transport = FakeTransport()
    valid = {"current": True}
    dispatcher = Dispatcher(tmp_path / "radio.jsonl", transport, clock=clock,
                            evidence_valid=lambda message: valid["current"])
    message = Transmission("one", "fuel:first", "observation:1", wav(tmp_path / "voice.wav"),
                           50, 1000, 1010)
    yield dispatcher, transport, clock, valid, message
    transport.fail_stop = False
    clock.now = max(clock.now, dispatcher._last_now)
    dispatcher.close()


def test_missing_software_clock_does_not_prevent_output(rig, tmp_path):
    dispatcher, transport, _, _, message = rig

    def unavailable():
        raise OSError("synthetic timing counter unavailable")

    dispatcher.monotonic = unavailable
    dispatcher.submit(message)
    dispatcher.tick()
    transport.result = "played"
    dispatcher.tick()
    rows = [json.loads(line) for line in (tmp_path / "radio.jsonl").read_text().splitlines()]
    assert transport.starts and rows[-1]["state"] == "played"
    assert all(row["monotonic_s"] is None for row in rows)
    assert rows[-1]["transport_start"]["confirmed"]["monotonic_s"] is None


def test_priority_preempts_and_does_not_replay(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(replace(message, priority=10, optional=True))
    dispatcher.tick()
    urgent = replace(message, transmission_id="urgent", dedup_key="fuel:urgent", priority=100)
    dispatcher.submit(urgent)
    dispatcher.tick()
    assert dispatcher.states == {"one": "interrupted", "urgent": "playing"}
    assert transport.stops == 1 and len(transport.starts) == 2
    transport.result = "played"
    dispatcher.tick()
    assert dispatcher.states["urgent"] == "played"
    assert dispatcher.active is None and not dispatcher.queue


def test_expiry_is_checked_before_play_and_during_play(rig):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    clock.now = 1010
    dispatcher.tick()
    assert dispatcher.states["one"] == "expired" and not transport.starts
    next_message = replace(message, transmission_id="two", dedup_key="two", expires_at=1011)
    dispatcher.submit(next_message)
    dispatcher.tick()
    clock.now = 1011
    dispatcher.tick()
    assert dispatcher.states["two"] == "interrupted" and transport.stops == 1


def test_mute_stops_active_clears_queue_and_never_replays(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    dispatcher.submit(replace(message, transmission_id="two", dedup_key="two"))
    dispatcher.set_muted(True)
    assert dispatcher.states == {"one": "interrupted", "two": "suppressed"}
    dispatcher.set_muted(False)
    dispatcher.tick()
    assert len(transport.starts) == 1


def test_submitted_while_muted_does_not_wait_for_unmute(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.set_muted(True)
    assert not dispatcher.submit(message)
    dispatcher.set_muted(False)
    dispatcher.tick()
    assert dispatcher.states["one"] == "suppressed" and not transport.starts


def test_failed_transport_is_stopped_before_next_output(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    dispatcher.submit(replace(message, transmission_id="two", dedup_key="two"))
    transport.result = "failed"
    dispatcher.tick()
    assert transport.stops == 1 and len(transport.starts) == 2
    assert dispatcher.states == {"one": "failed", "two": "playing"}


def test_failed_transport_with_unconfirmed_stop_cannot_start_successor(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    dispatcher.submit(replace(message, transmission_id="two", dedup_key="two"))
    transport.result, transport.fail_stop = "failed", True
    with pytest.raises(RuntimeError):
        dispatcher.tick()
    assert len(transport.starts) == 1 and dispatcher.faulted


def test_freshness_rechecked_and_optional_waits_for_window(rig):
    dispatcher, transport, _, valid, message = rig
    dispatcher.submit(replace(message, optional=True))
    dispatcher.tick(allow_optional=False)
    assert not transport.starts
    valid["current"] = False
    dispatcher.tick()
    assert dispatcher.states["one"] == "suppressed" and not transport.starts


def test_evidence_invalidation_stops_current_audio(rig):
    dispatcher, transport, _, valid, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    valid["current"] = False
    dispatcher.tick()
    assert dispatcher.states["one"] == "interrupted" and transport.stops == 1


def test_optional_current_audio_stops_when_workload_changes(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(replace(message, optional=True))
    dispatcher.tick()
    dispatcher.tick(allow_optional=False)
    assert dispatcher.states["one"] == "interrupted" and transport.stops == 1


def test_duplicate_identity_conflict_and_persistent_dedup(rig, tmp_path):
    dispatcher, transport, clock, _, message = rig
    assert dispatcher.submit(message)
    assert not dispatcher.submit(message)
    assert not dispatcher.submit(replace(message, transmission_id="other"))
    with pytest.raises(ValueError, match="reused"):
        dispatcher.submit(replace(message, priority=99))
    dispatcher.close()
    restarted = Dispatcher(tmp_path / "radio.jsonl", transport, clock=clock,
                           evidence_valid=lambda _: True)
    try:
        assert not restarted.submit(message)
        restarted.tick()
        assert not transport.starts
    finally:
        restarted.close()


def test_unclean_restart_marks_unfinished_interrupted_not_played(rig, tmp_path):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    # Simulate process loss: durable journal survived; no close lifecycle event.
    dispatcher._journal.close()
    dispatcher._lock.close()
    dispatcher.closed = True
    restarted = Dispatcher(tmp_path / "radio.jsonl", transport, clock=clock,
                           evidence_valid=lambda _: True)
    try:
        assert restarted.states["one"] == "interrupted"
        assert not restarted.submit(message)
        restarted.tick()
        assert len(transport.starts) == 1
    finally:
        restarted.close()


def test_second_dispatcher_cannot_share_journal(rig, tmp_path):
    _, transport, clock, _, _ = rig
    with pytest.raises(RuntimeError, match="owns"):
        Dispatcher(tmp_path / "radio.jsonl", transport, clock=clock,
                   evidence_valid=lambda _: True)


def test_corrupt_journal_refuses_output_and_is_preserved(tmp_path):
    path = tmp_path / "radio.jsonl"
    path.write_text('{"schema_version": 1,', encoding="utf-8")
    transport = FakeTransport()
    with pytest.raises(ValueError):
        Dispatcher(path, transport, evidence_valid=lambda _: True)
    assert path.read_text() == '{"schema_version": 1,'
    assert not transport.starts


@pytest.mark.parametrize("state", ["played", "acknowledged"])
def test_incomplete_state_history_refused(rig, tmp_path, state):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    dispatcher.close()
    journal = tmp_path / "radio.jsonl"
    generated = json.loads(journal.read_text().splitlines()[0])
    invalid = {"schema_version": 1, "at": 1000, "transmission_id": "one", "state": state,
               "reason": "bad_history"}
    journal.write_text(json.dumps(generated) + "\n" + json.dumps(invalid) + "\n")
    with pytest.raises(ValueError, match="transition"):
        Dispatcher(journal, transport, clock=clock, evidence_valid=lambda _: True)


def test_incomplete_spec_refused(rig, tmp_path):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    dispatcher.close()
    journal = tmp_path / "radio.jsonl"
    generated = json.loads(journal.read_text().splitlines()[0])
    del generated["spec"]["evidence_id"]
    journal.write_text(json.dumps(generated) + "\n")
    with pytest.raises(TypeError):
        Dispatcher(journal, transport, clock=clock, evidence_valid=lambda _: True)


def test_changed_audio_fails_without_playback(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    wav(message.wav_path, frequency=400)
    dispatcher.tick()
    assert dispatcher.states["one"] == "failed" and not transport.starts


def test_disappeared_audio_is_failed_without_output(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    message.wav_path.unlink()
    dispatcher.tick()
    assert dispatcher.states["one"] == "failed" and not transport.starts


def test_slow_evidence_recheck_can_expire_before_output(rig):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    calls = 0

    def slow(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            clock.now = 1011
        return True

    dispatcher.evidence_valid = slow
    dispatcher.tick()
    assert dispatcher.states["one"] == "expired" and not transport.starts


def test_stop_failure_faults_and_prevents_successor_audio(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    transport.fail_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        dispatcher.set_muted(True)
    assert dispatcher.faulted and dispatcher.states["one"] == "failed"
    with pytest.raises(RuntimeError, match="faulted"):
        dispatcher.tick()
    assert len(transport.starts) == 1


def test_close_retains_owner_until_stop_is_confirmed(rig, tmp_path):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    transport.fail_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        dispatcher.close()
    assert not dispatcher.closed
    with pytest.raises(RuntimeError, match="owns"):
        Dispatcher(tmp_path / "radio.jsonl", FakeTransport(), clock=clock,
                   evidence_valid=lambda _: True)
    transport.fail_stop = False
    dispatcher.close()
    next_owner = Dispatcher(tmp_path / "radio.jsonl", FakeTransport(), clock=clock,
                            evidence_valid=lambda _: True)
    next_owner.close()


def test_start_failure_is_preserved_not_retried(rig):
    dispatcher, transport, _, _, message = rig
    transport.fail_start = True
    dispatcher.submit(message)
    dispatcher.tick()
    assert dispatcher.states["one"] == "failed" and not transport.starts
    assert not dispatcher.submit(message)


def test_clock_regression_stops_audio_and_faults(rig):
    dispatcher, transport, clock, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    clock.now = 999
    with pytest.raises(ValueError, match="clock"):
        dispatcher.tick()
    assert dispatcher.faulted and transport.stops == 1


def test_evidence_exception_stops_audio_and_faults(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()

    def failure(_):
        raise OSError("source unavailable")

    dispatcher.evidence_valid = failure
    with pytest.raises(OSError):
        dispatcher.tick()
    assert dispatcher.faulted and transport.stops == 1
    assert dispatcher.states["one"] == "interrupted"


def test_journal_failure_stops_audio_and_propagates(rig):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    dispatcher._journal.close()
    with pytest.raises(ValueError):
        dispatcher.set_muted(True)
    assert dispatcher.faulted and transport.stops >= 1


def test_acknowledgment_requires_explicit_action_after_completion(rig, tmp_path):
    dispatcher, transport, _, _, message = rig
    dispatcher.submit(message)
    dispatcher.tick()
    with pytest.raises(ValueError, match="completed"):
        dispatcher.acknowledge("one")
    transport.result = "played"
    dispatcher.tick()
    rows = [json.loads(row) for row in (tmp_path / "radio.jsonl").read_text().splitlines()]
    assert [row["state"] for row in rows] == ["generated", "queued", "playing", "played"]
    assert rows[-1]["driver_heard"] is None and rows[-1]["audible_onset_at"] is None
    dispatcher.acknowledge("one")
    assert dispatcher.states["one"] == "acknowledged"


@pytest.mark.parametrize("changes", [
    {"created_at": float("nan")}, {"expires_at": float("inf")},
    {"priority": True}, {"created_at": 1001}, {"expires_at": 1000}, {"optional": 1},
])
def test_invalid_transmission_refused_before_output(rig, changes):
    dispatcher, transport, _, _, message = rig
    with pytest.raises(ValueError):
        dispatcher.submit(replace(message, **changes))
    assert not transport.starts and not dispatcher.queue


def test_dsp_preserves_duration_limits_peak_and_bandlimits(tmp_path):
    rms = {}
    for frequency in (50, 1000, 9000):
        source = wav(tmp_path / f"in{frequency}.wav", frequency=frequency, seconds=0.5)
        destination = tmp_path / f"out{frequency}.wav"
        result = process_radio(source, destination, intensity=1)
        rate, samples = read_pcm(destination)
        assert rate == 22050 and len(samples) == 11025
        assert max(abs(sample) for sample in samples) <= round(32767 * 0.85)
        assert result["dry_sha256"] != result["radio_sha256"]
        assert samples[0] == samples[-1] == 0
        central = samples[1000:-1000]
        rms[frequency] = math.sqrt(sum(value * value for value in central) / len(central))
    assert rms[1000] > rms[50] * 3 and rms[1000] > rms[9000] * 3


def test_dsp_refuses_overwrite_and_invalid_intensity(tmp_path):
    source = wav(tmp_path / "in.wav")
    destination = tmp_path / "out.wav"
    process_radio(source, destination)
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        process_radio(source, destination)
    with pytest.raises(ValueError):
        process_radio(source, tmp_path / "no.wav", intensity=float("nan"))
    assert destination.read_bytes() == before


def test_pcm_rejects_truncation(tmp_path):
    source = wav(tmp_path / "in.wav")
    source.write_bytes(source.read_bytes()[:-10])
    with pytest.raises(ValueError, match="Truncated"):
        read_pcm(source)


def test_audio_transport_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="explicit"):
        WindowsWaveTransport()


class FakeWinMM:
    def __init__(self):
        self.calls = []
        self.header = None
        self.fail = None
        self.devices = [OutputDevice(0, "Example headset", 1, 2, 3, 65535, 2, 0),
                        OutputDevice(1, "Example speakers", 1, 4, 3, 65535, 2, 0)]
        self.opened_device = None
        self.open_ids = []
        self.before_call = lambda name, args: None

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            self.before_call(name, args)
            if name == self.fail:
                return 5
            if name == "waveOutGetNumDevs":
                return len(self.devices)
            if name == "waveOutGetDevCapsW":
                identifier = args[0]
                if identifier == 123:
                    device = self.opened_device
                elif 0 <= identifier < len(self.devices):
                    device = self.devices[identifier]
                else:
                    return 2
                caps = ctypes.cast(args[1], ctypes.POINTER(_WaveOutputCaps)).contents
                for field in ("manufacturer_id", "product_id", "driver_version", "name",
                              "formats", "channels", "support"):
                    setattr(caps, field, getattr(device, field))
            if name == "waveOutOpen":
                self.open_ids.append(args[1])
                index = 0 if args[1] == 0xFFFFFFFF else args[1]
                if index >= len(self.devices):
                    return 2
                self.opened_device = self.devices[index]
                ctypes.cast(args[0], ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
            if name == "waveOutPrepareHeader":
                self.header = ctypes.cast(args[1], ctypes.POINTER(_WaveHeader)).contents
            if name == "waveOutReset" and self.header:
                self.header.flags |= 1
            return 0
        return call


def native_fake():
    transport = WindowsWaveTransport.__new__(WindowsWaveTransport)
    transport._dll = FakeWinMM()
    transport._handle = ctypes.c_void_p()
    transport._header = _WaveHeader()
    transport._buffer = None
    transport._prepared = False
    transport.selection = None
    transport.selected_device = None
    transport._claim = FakeOutputClaim()
    transport._closed = False
    return transport


class FakeOutputClaim:
    def __init__(self):
        self.state = "unclaimed"

    def acquire(self):
        self.state = "held"

    def close(self):
        self.state = "closed"

    def snapshot(self):
        return dict(state=self.state)


def test_native_transport_completion_uses_driver_flag_not_duration(tmp_path):
    transport = native_fake()
    transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    assert transport.poll() == "playing"
    transport._dll.header.flags |= 1
    assert transport.poll() == "played"
    assert transport._dll.calls[-2:] == ["waveOutUnprepareHeader", "waveOutClose"]
    assert transport._buffer is None


def test_native_stop_resets_before_release_and_refuses_overlap(tmp_path):
    transport = native_fake()
    source = wav(tmp_path / "voice.wav")
    transport.start(source, can_start=lambda: True)
    with pytest.raises(RuntimeError, match="busy"):
        transport.start(source, can_start=lambda: True)
    transport.stop()
    assert transport._dll.calls[-3:] == ["waveOutReset", "waveOutUnprepareHeader", "waveOutClose"]


def test_native_reset_failure_retains_buffer_and_busy_handle(tmp_path):
    transport = native_fake()
    transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    transport._dll.fail = "waveOutReset"
    with pytest.raises(RuntimeError, match="code 5"):
        transport.stop()
    assert transport._buffer is not None and transport._handle.value
    assert "waveOutUnprepareHeader" not in transport._dll.calls


def test_native_final_guard_prevents_write_and_releases_buffer(tmp_path):
    transport = native_fake()
    assert not transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: False)
    assert "waveOutWrite" not in transport._dll.calls
    assert transport._dll.calls[-3:] == ["waveOutReset", "waveOutUnprepareHeader", "waveOutClose"]
    assert not transport._handle.value


def test_slow_native_prepare_crossing_deadline_does_not_emit(rig):
    dispatcher, _, clock, _, message = rig
    transport = native_fake()
    ordinary_call = transport._call

    def slow_call(name, *args):
        ordinary_call(name, *args)
        if name == "waveOutPrepareHeader":
            clock.now = 1011

    transport._call = slow_call
    dispatcher.transport = transport
    dispatcher.submit(message)
    dispatcher.tick()
    assert "waveOutWrite" not in transport._dll.calls
    assert dispatcher.states["one"] == "expired" and not transport._handle.value


def test_inventory_reads_caps_without_opening_or_playing():
    dll = FakeWinMM()
    devices = _output_devices(dll)
    assert [device.name for device in devices] == ["Example headset", "Example speakers"]
    assert all(device.fingerprint == device.selection().fingerprint for device in devices)
    assert set(dll.calls) == {"waveOutGetNumDevs", "waveOutGetDevCapsW"}


def test_inventory_refuses_partial_or_changing_result():
    dll = FakeWinMM()
    dll.fail = "waveOutGetDevCapsW"
    with pytest.raises(RuntimeError, match="code"):
        _output_devices(dll)
    dll.fail = None
    count_calls = 0

    def change(name, _):
        nonlocal count_calls
        if name == "waveOutGetNumDevs":
            count_calls += 1
            if count_calls == 2:
                dll.devices.pop()

    dll.before_call = change
    with pytest.raises(RuntimeError, match="changed"):
        _output_devices(dll)


def test_selection_follows_unique_capability_identity_after_reorder(tmp_path):
    transport = native_fake()
    transport.selection = transport._dll.devices[0].selection()
    transport._dll.devices.reverse()
    assert transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    assert transport._dll.open_ids == [1]  # Re-resolved ID, not the former zero.
    assert transport.selected_device.name == "Example headset"
    transport.stop()


@pytest.mark.parametrize("change", ["missing", "ambiguous", "renamed", "capabilities"])
def test_selected_output_mismatch_refuses_open_without_default_fallback(tmp_path, change):
    transport = native_fake()
    expected = transport._dll.devices[0]
    transport.selection = expected.selection()
    if change == "missing":
        transport._dll.devices.pop(0)
    elif change == "ambiguous":
        transport._dll.devices.append(expected)
    elif change == "renamed":
        transport._dll.devices[0] = replace(expected, name="Different headset")
    else:
        transport._dll.devices[0] = replace(expected, driver_version=99)
    with pytest.raises(RuntimeError, match="missing or ambiguous"):
        transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    assert not transport._dll.open_ids and "waveOutWrite" not in transport._dll.calls


def test_reorder_during_open_checks_handle_before_emitting(tmp_path):
    transport = native_fake()
    transport.selection = transport._dll.devices[0].selection()

    def change(name, _):
        if name == "waveOutOpen":
            transport._dll.devices.reverse()

    transport._dll.before_call = change
    with pytest.raises(RuntimeError, match="does not match"):
        transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    assert transport._dll.open_ids == [0]
    assert "waveOutWrite" not in transport._dll.calls and not transport._handle.value


def test_disappearance_after_prepare_refuses_write_and_closes(tmp_path):
    transport = native_fake()
    transport.selection = transport._dll.devices[0].selection()

    def change(name, _):
        if name == "waveOutPrepareHeader":
            transport._dll.devices.clear()

    transport._dll.before_call = change
    with pytest.raises(RuntimeError, match="missing"):
        transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    assert "waveOutWrite" not in transport._dll.calls and not transport._handle.value


def test_selected_output_open_failure_never_falls_back(tmp_path):
    transport = native_fake()
    transport.selection = transport._dll.devices[0].selection()
    transport._dll.fail = "waveOutOpen"
    with pytest.raises(RuntimeError, match="waveOutOpen"):
        transport.start(wav(tmp_path / "voice.wav"), can_start=lambda: True)
    assert transport._dll.calls.count("waveOutOpen") == 1
    assert "waveOutWrite" not in transport._dll.calls


def test_selected_output_loss_during_playback_stops_dispatcher(rig):
    dispatcher, _, _, _, message = rig
    transport = native_fake()
    transport.selection = transport._dll.devices[0].selection()
    dispatcher.transport = transport
    dispatcher.submit(message)
    dispatcher.tick()
    transport._dll.devices.clear()
    with pytest.raises(RuntimeError, match="missing"):
        dispatcher.tick()
    assert dispatcher.faulted and not transport._handle.value
    assert dispatcher.states["one"] == "interrupted"


def test_selection_roundtrip_and_strict_fields():
    selection = FakeWinMM().devices[0].selection()
    assert OutputSelection.from_dict(selection.to_dict()) == selection
    for invalid in (selection.to_dict() | {"index": 0},
                    selection.to_dict() | {"schema_version": True},
                    selection.to_dict() | {"fingerprint": "not-a-sha"},
                    selection.to_dict() | {"name": ""}):
        with pytest.raises(ValueError):
            OutputSelection.from_dict(invalid)


def test_inventory_cli_never_creates_transport(monkeypatch, capsys):
    from simcoach.live import radio
    monkeypatch.setattr(radio, "list_output_devices", lambda: _output_devices(FakeWinMM()))

    def no_transport(**_):
        raise AssertionError("Inventory must not construct an output transport")

    monkeypatch.setattr(radio, "WindowsWaveTransport", no_transport)
    assert main(["--list-outputs"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["audio_output_attempted"] is False and result["route_verified"] is False
    assert len(result["devices"]) == 2
