"""Synthetic AMS2 and controller observations; no actual rig response is implied."""

import json
from types import SimpleNamespace

import pytest

from scripts import verify_rig_inputs
from simcoach.adapters.ams2 import (
    GAME_INGAME_PLAYING,
    GAME_INGAME_REPLAY,
    SESSION_PRACTICE,
    SHARED_MEMORY_VERSION,
    SharedMemory,
)
from simcoach.live.rig_controller import ControllerError
from simcoach.live.rig_inputs import CONTROLS, observe, read_existing


class Simulation:
    def __init__(self):
        self.now = 0.0
        self.sequence = 0
        self.prompts = []
        self.stage_started = None

    def clock(self):
        return self.now

    def sleep(self, duration):
        self.now += duration

    def prompt(self, message):
        self.prompts.append(message)
        self.stage_started = self.now

    def values(self):
        values = dict.fromkeys(CONTROLS, 0.0)
        if self.stage_started is not None and 0.55 <= self.now - self.stage_started <= 1.2:
            values[CONTROLS[len(self.prompts) - 1]] = 0.65
        return values

    def read(self):
        self.sequence += 2
        frame = SharedMemory()
        frame.mVersion = SHARED_MEMORY_VERSION
        frame.mSequenceNumber = self.sequence
        frame.mGameState = GAME_INGAME_PLAYING
        frame.mSessionState = SESSION_PRACTICE
        frame.mNumParticipants = 1
        frame.mParticipantInfo[0].mIsActive = True
        frame.mParticipantInfo[0].mName = b"synthetic-identity"
        frame.mSteering, frame.mThrottle, frame.mBrake = self.values().values()
        return frame

    def device(self):
        values = self.values()
        return {"x": 0.5 - values["steering"] / 2, "y": 1 - values["throttle"],
                "z": values["brake"]}

    def run(self, *, read=None, device=None, confirmed=True, mode="parked", **kwargs):
        return observe(
            read or self.read, mode=mode, duration_s=6, player_confirmed=confirmed,
            physical_confirmed=True, physical_read=device, prompt=self.prompt,
            clock=self.clock, sleep=self.sleep, **kwargs,
        )


def test_parked_responses_and_unique_time_matches():
    sim = Simulation()
    result = sim.run(device=sim.device)
    assert result["status"] == "observed"
    assert result["participant_index"] == 0
    assert result["player_attribution"] == "driver_confirmed_view"
    assert result["physical_attribution"] == "timing_correlated"
    assert [control["axis"] for control in result["controls"].values()] == ["x", "y", "z"]
    assert all(control["windows_response"] == "observed" for control in result["controls"].values())
    assert len(sim.prompts) == 3
    assert "synthetic-identity" not in json.dumps(result)


def test_parked_without_device_can_show_driver_confirmed_gesture():
    result = Simulation().run()
    assert result["status"] == "observed"
    assert result["physical_attribution"] == "driver_confirmed_gesture"
    assert result["controller_status"] == "not_requested"
    assert result["controls"]["brake"]["windows_response"] == "unavailable"


def test_separate_wheel_and_pedals_can_share_an_axis_name():
    sim = Simulation()
    wheel_stages = set()
    pedal_stages = set()

    def wheel():
        wheel_stages.add(len(sim.prompts))
        return {"x": sim.values()["steering"]}

    def pedals():
        pedal_stages.add(len(sim.prompts))
        return {"x": sim.values()["throttle"], "y": sim.values()["brake"]}

    result = sim.run(device=wheel, pedal_read=pedals, separate_pedals=True)
    assert result["physical_attribution"] == "timing_correlated"
    assert [
        (control["axis_source"], control["axis"]) for control in result["controls"].values()
    ] == [("wheel", "x"), ("pedals", "x"), ("pedals", "y")]
    assert wheel_stages == {0, 1}
    assert pedal_stages == {1, 2, 3}


def test_requested_unavailable_pedals_do_not_fall_back_to_wheel():
    sim = Simulation()
    result = sim.run(device=sim.device, pedal_read=None, separate_pedals=True)
    assert result["controller_status"] == "sampled"
    assert result["controls"]["steering"]["axis_source"] == "wheel"
    for name in ("throttle", "brake"):
        assert result["controls"][name]["windows_response"] == "unavailable"
        assert result["controls"][name]["physical"] == "driver_confirmed_gesture"


def test_same_slot_selection_still_checks_axis_uniqueness():
    sim = Simulation()

    def combined_axis():
        return {"x": sum(sim.values().values())}

    result = sim.run(device=combined_axis, pedal_read=combined_axis, separate_pedals=True)
    assert result["physical_attribution"] == "driver_confirmed_gesture"
    assert all(item["axis"] is None for item in result["controls"].values())


def test_duplicate_native_axis_is_ambiguous():
    sim = Simulation()
    result = sim.run(device=lambda: {**sim.device(), "duplicate": sim.device()["x"]})
    assert result["controls"]["steering"]["axis"] is None
    assert result["controls"]["steering"]["windows_response"] == "ambiguous"
    assert result["controls"]["brake"]["axis"] == "z"
    assert result["physical_attribution"] == "driver_confirmed_gesture"


def test_native_disconnect_revokes_all_native_correlation():
    sim = Simulation()

    def device():
        if sim.now > 4:
            raise ControllerError("disconnected")
        return sim.device()

    result = sim.run(device=device)
    assert result["status"] == "observed"
    assert result["controller_status"] == "lost_or_changed"
    assert all(control["axis"] is None for control in result["controls"].values())
    assert all(
        control["windows_response"] == "unavailable" for control in result["controls"].values()
    )


@pytest.mark.parametrize("expected_also_moves", [False, True])
def test_windows_brake_motion_can_reveal_wrong_in_game_binding(expected_also_moves):
    sim = Simulation()

    def read():
        frame = sim.read()
        if len(sim.prompts) == 3:
            frame.mThrottle = frame.mBrake
            if not expected_also_moves:
                frame.mBrake = 0
        return frame

    result = sim.run(read=read, device=sim.device)
    brake = result["controls"]["brake"]
    assert result["status"] == "incomplete"
    assert result["reason"] == "unexpected_control_response"
    assert brake["windows_response"] == "observed"
    assert brake["other_game_controls"] == ["throttle"]
    assert brake["in_game"] == ("observed" if expected_also_moves else "not_observed")


def test_windows_gesture_survives_flat_game_channel():
    sim = Simulation()

    def read():
        frame = sim.read()
        frame.mBrake = 0
        return frame

    result = sim.run(read=read, device=sim.device)
    assert result["reason"] == "control_response_incomplete"
    assert result["controls"]["brake"] == {
        "in_game": "not_observed", "physical": "not_observed", "axis": None,
        "windows_response": "observed", "other_game_controls": [],
    }


@pytest.mark.parametrize(("change", "reason"), [
    (lambda frame: setattr(frame, "mGear", 1),
     "parked_check_requires_stationary_neutral_practice_or_test"),
    (lambda frame: setattr(frame, "mGameState", GAME_INGAME_REPLAY), "not_in_live_cockpit"),
    (lambda frame: setattr(frame.mParticipantInfo[0], "mName", b"replacement"),
     "source_or_view_changed"),
    (lambda frame: setattr(frame, "mSequenceNumber", 2), "source_or_view_changed"),
])
def test_disqualifying_transition_discards_earlier_evidence(change, reason):
    sim = Simulation()

    def read():
        frame = sim.read()
        if sim.now > 2:
            change(frame)
        return frame

    result = sim.run(read=read)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert all(control["in_game"] == "not_observed" for control in result["controls"].values())
    assert len(sim.prompts) == 1


def test_repeated_counter_cannot_admit_or_prompt():
    sim = Simulation()
    frame = sim.read()
    result = sim.run(read=lambda: frame)
    assert result["reason"] == "source_not_advancing"
    assert result["frame_count"] == 0
    assert sim.prompts == []


def test_parked_allows_cockpit_focus_and_then_waits_before_first_cue():
    sim = Simulation()
    prompt_times = []

    def read():
        frame = sim.read()
        if sim.now < 3:
            frame.mGameState = 3  # paused while the driver returns to AMS2
        return frame

    original_prompt = sim.prompt

    def timed_prompt(message):
        prompt_times.append(sim.now)
        original_prompt(message)

    sim.prompt = timed_prompt
    result = sim.run(read=read, preparation_s=5)
    assert result["status"] == "observed"
    assert 8 <= prompt_times[0] < 8.2


def test_parked_returns_quickly_when_mapping_absent():
    sim = Simulation()

    def missing():
        raise FileNotFoundError()

    result = sim.run(
        read=missing, preparation_s=5, device=lambda: pytest.fail("No cues without fresh AMS2"),
    )
    assert result["reason"] == "mapping_unavailable"
    assert result["controller_status"] == "unavailable"
    assert sim.now < 2.2
    assert sim.prompts == []


def test_unconfirmed_passive_observation_can_only_describe_the_view():
    sim = Simulation()

    def read():
        frame = sim.read()
        value = 0.5 if sim.sequence % 4 == 0 else 0.0
        frame.mSteering = frame.mThrottle = frame.mBrake = value
        return frame

    result = sim.run(read=read, confirmed=False, mode="passive")
    assert result["status"] == "incomplete"
    assert result["reason"] == "local_player_unconfirmed"
    assert result["physical_attribution"] == "not_observed"
    assert all(control["in_game"] == "observed" for control in result["controls"].values())
    assert sim.prompts == []


def test_mapping_view_released_when_read_fails():
    class Mapping:
        closed = False

        def read(self):
            raise FileNotFoundError()

        def close(self):
            self.closed = True

    mapping = Mapping()
    with pytest.raises(FileNotFoundError):
        read_existing(mapping)
    assert mapping.closed


def test_cli_produces_single_no_source_result(monkeypatch, capsys):
    def missing():
        raise FileNotFoundError()

    monkeypatch.setattr(verify_rig_inputs.sys, "platform", "win32")
    monkeypatch.setattr(verify_rig_inputs, "ams2_reader", lambda: missing)
    assert verify_rig_inputs.main(["--duration-s", "1"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["reason"] == "mapping_unavailable"
    assert output.err == ""


def test_cli_lists_only_projected_controller_fields(monkeypatch, capsys):
    controller = SimpleNamespace(id=2, name="Synthetic Wheel", axes=("x", "r"), vendor="private")
    monkeypatch.setattr(verify_rig_inputs.sys, "platform", "win32")
    monkeypatch.setattr(verify_rig_inputs, "discover_controllers", lambda: (controller,))
    assert verify_rig_inputs.main(["--list-controllers"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "observed"
    assert result["controllers"] == [{"id": 2, "name": "Synthetic Wheel", "axes": ["x", "r"]}]


@pytest.mark.parametrize("slot_changed", [False, True])
def test_controller_name_requires_exact_unique_match_and_rechecks_slot(monkeypatch, slot_changed):
    target = SimpleNamespace(id=2, name="Exact Wheel", axes=("x", "r"))
    other = SimpleNamespace(id=4, name="Exact Wheel (Generic)", axes=("x", "r"))
    opened = SimpleNamespace(identity=other if slot_changed else target)
    monkeypatch.setattr(verify_rig_inputs, "discover_controllers", lambda: (other, target))
    monkeypatch.setattr(verify_rig_inputs, "open_controller", lambda slot: opened)
    assert verify_rig_inputs._select_controller(None, "Exact Wheel") is (
        None if slot_changed else opened
    )
    assert verify_rig_inputs._select_controller(None, "exact wheel") is None
    monkeypatch.setattr(verify_rig_inputs, "discover_controllers", lambda: (target, target))
    assert verify_rig_inputs._select_controller(None, "Exact Wheel") is None
