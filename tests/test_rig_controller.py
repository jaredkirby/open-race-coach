from dataclasses import replace

import pytest

from simcoach.live import rig_controller as rig


@pytest.fixture
def winmm(monkeypatch):
    backend = object.__new__(rig._WinMM)
    backend.get_count = lambda: 3

    def get_caps(slot, pointer, size):
        assert size == rig.ct.sizeof(rig._JoyCaps)
        if slot != 1:
            return 165
        caps = pointer._obj
        caps.name = "Synthetic driver"
        caps.mid = 42
        caps.pid = 9
        caps.num_axes = 4
        caps.caps = 0x09  # Z and V, in addition to X and Y.
        caps.x_min, caps.x_max = 100, 900
        caps.y_min, caps.y_max = 0, 50
        caps.z_min, caps.z_max = 200, 500
        caps.v_min, caps.v_max = 0, 100
        caps.registry_key = "Test joystick registry"
        return 0

    def get_pos(slot, pointer):
        assert slot == 1
        info = pointer._obj
        assert info.size == 52
        assert info.flags == 0x27
        info.x, info.y, info.z, info.v = 500, 0, 500, 75
        return 0

    backend.get_caps = get_caps
    backend.get_pos = get_pos
    monkeypatch.setattr(rig, "_WinMM", lambda: backend)
    monkeypatch.setattr(
        rig, "_oem_identity", lambda _key, _slot: ("VID_0000&PID_0000", "Synthetic wheel")
    )
    return backend


def test_select_exact_slot_and_normalize_available_axes(winmm):
    (identity,) = rig.discover_controllers()
    assert identity.id == 1
    assert identity.name == "Synthetic wheel"
    assert identity.driver_name == "Synthetic driver"
    assert identity.axes == ("x", "y", "z", "v")
    controller = rig.open_controller(1)
    assert controller.identity == identity
    assert controller.sample() == {"x": 0.5, "y": 0.0, "z": 1.0, "v": 0.75}
    with pytest.raises(rig.ControllerError, match="controller 0 .*165"):
        rig.open_controller(0)
    with pytest.raises(rig.ControllerError, match="slot range"):
        rig.open_controller(3)


def test_read_fails_if_slot_identity_changes_during_sample(winmm):
    controller = rig.open_controller(1)
    changed = replace(controller.identity, name="Other synthetic wheel")
    identities = iter((controller.identity, changed))
    winmm.identify = lambda _slot: next(identities)
    with pytest.raises(rig.ControllerError, match="identity changed"):
        controller.sample()


def test_read_fails_if_device_disconnects(winmm):
    controller = rig.open_controller(1)
    winmm.get_pos = lambda _slot, _pointer: 167
    with pytest.raises(rig.ControllerError, match="joyGetPosEx.*167"):
        controller.sample()
    assert rig.discover_controllers() == ()


def test_unexpected_native_error_is_visible_from_discovery(winmm):
    winmm.get_caps = lambda _slot, _pointer, _size: 6
    with pytest.raises(rig.ControllerError, match="joyGetDevCapsW.*6"):
        rig.discover_controllers()


def test_zero_axis_slot_is_unverifiable_and_does_not_hide_a_readable_slot(winmm):
    original_caps = winmm.get_caps

    def get_caps(slot, pointer, size):
        if slot == 0:
            caps = pointer._obj
            caps.x_max = caps.y_max = 65535
            return 0
        return original_caps(slot, pointer, size)

    winmm.get_caps = get_caps
    assert [controller.id for controller in rig.discover_controllers()] == [1]
    with pytest.raises(rig.ControllerError, match="no verifiable axis capabilities"):
        rig.open_controller(0)
    winmm.get_count = lambda: 1
    with pytest.raises(rig.ControllerError, match="no verifiable axis capabilities"):
        rig.discover_controllers()


def test_partial_axis_ranges_expose_only_advertised_axes_with_valid_limits(winmm):
    original_caps = winmm.get_caps
    original_pos = winmm.get_pos

    def get_caps(slot, pointer, size):
        result = original_caps(slot, pointer, size)
        caps = pointer._obj
        caps.z_max = caps.z_min
        return result

    def get_pos(slot, pointer):
        info = pointer._obj
        assert info.flags == 0x23  # Request valid X, Y and V, without malformed Z.
        info.flags = 0x27
        return original_pos(slot, pointer)

    winmm.get_caps, winmm.get_pos = get_caps, get_pos
    controller = rig.open_controller(1)
    assert controller.identity.axes == ("x", "y", "v")
    assert controller.sample() == {"x": 0.5, "y": 0.0, "v": 0.75}


def test_oem_lookup_uses_selected_slot_and_driver_registry(monkeypatch):
    values = {
        (f"{rig._CONFIG_REGISTRY}\\driver\\CurrentJoystickSettings", "Joystick2OEMName"):
            "VID_0000&PID_0000",
        (f"{rig._JOYSTICK_REGISTRY}\\OEM\\VID_0000&PID_0000", "OEMName"):
            "Synthetic wheel",
    }
    monkeypatch.setattr(rig, "_registry_string", lambda key, value: values.get((key, value)))
    assert rig._oem_identity("driver", 1) == ("VID_0000&PID_0000", "Synthetic wheel")
    assert rig._oem_identity("driver", 0) == (None, None)
