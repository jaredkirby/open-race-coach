"""Read-only Windows game-controller positions via the WinMM joystick API.

Axis names are Windows labels. A pedal connected to a wheelbase can appear as
another axis of that wheelbase. Determine each control by observing it move.
WinMM slot and OEM metadata are point-in-time observations, without an
instance handle or a guarantee against an unobserved same-model reconnect.
"""

from __future__ import annotations

import ctypes as ct
import sys
from dataclasses import dataclass

_WORD = ct.c_uint16
_UINT = ct.c_uint32
_UNAVAILABLE_SLOT = {2, 165, 167}  # MMSYSERR_BADDEVICEID, JOYERR_PARMS, JOYERR_UNPLUGGED
_JOYSTICK_REGISTRY = r"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick"
_CONFIG_REGISTRY = r"System\CurrentControlSet\Control\MediaResources\Joystick"
_AXES = (
    ("x", 0x01, 0),
    ("y", 0x02, 0),
    ("z", 0x04, 0x01),
    ("r", 0x08, 0x02),
    ("u", 0x10, 0x04),
    ("v", 0x20, 0x08),
)


class ControllerError(RuntimeError):
    """The selected Windows controller could not supply an observation."""


class _WinMMError(ControllerError):
    def __init__(self, operation: str, slot: int, code: int):
        super().__init__(f"{operation} failed for controller {slot} (WinMM error {code})")
        self.code = code


class _JoyCaps(ct.Structure):
    _fields_ = [
        ("mid", _WORD), ("pid", _WORD), ("name", ct.c_wchar * 32),
        ("x_min", _UINT), ("x_max", _UINT), ("y_min", _UINT), ("y_max", _UINT),
        ("z_min", _UINT), ("z_max", _UINT), ("buttons", _UINT),
        ("period_min", _UINT), ("period_max", _UINT),
        ("r_min", _UINT), ("r_max", _UINT), ("u_min", _UINT), ("u_max", _UINT),
        ("v_min", _UINT), ("v_max", _UINT), ("caps", _UINT),
        ("max_axes", _UINT), ("num_axes", _UINT), ("max_buttons", _UINT),
        ("registry_key", ct.c_wchar * 32), ("oem_vxd", ct.c_wchar * 260),
    ]


class _JoyInfo(ct.Structure):
    _fields_ = [
        (name, _UINT)
        for name in (
            "size", "flags", "x", "y", "z", "r", "u", "v",
            "buttons", "button_number", "pov", "reserved1", "reserved2",
        )
    ]


@dataclass(frozen=True)
class Controller:
    id: int
    name: str
    axes: tuple[str, ...]
    driver_name: str
    manufacturer_id: int
    product_id: int
    oem_id: str | None
    axis_ranges: tuple[tuple[int, int], ...]


def _registry_string(path: str, value: str) -> str | None:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, path, access=winreg.KEY_QUERY_VALUE) as key:
                result, kind = winreg.QueryValueEx(key, value)
        except (FileNotFoundError, PermissionError):
            continue
        if kind == winreg.REG_SZ and isinstance(result, str) and result.strip():
            return result.strip()
    return None


def _oem_identity(registry_key: str, slot: int) -> tuple[str | None, str | None]:
    if not registry_key:
        return None, None
    settings = f"{_CONFIG_REGISTRY}\\{registry_key}\\CurrentJoystickSettings"
    oem_id = _registry_string(settings, f"Joystick{slot + 1}OEMName")
    if oem_id is None:
        return None, None
    return oem_id, _registry_string(f"{_JOYSTICK_REGISTRY}\\OEM\\{oem_id}", "OEMName")


class _WinMM:
    def __init__(self):
        if sys.platform != "win32":
            raise ControllerError("Windows controller observation requires Windows")
        try:
            dll = ct.WinDLL("winmm.dll", winmode=0x00000800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
            self.get_count = dll.joyGetNumDevs
            self.get_caps = dll.joyGetDevCapsW
            self.get_pos = dll.joyGetPosEx
        except (OSError, AttributeError) as exc:
            raise ControllerError("Windows joystick API is unavailable") from exc
        self.get_count.argtypes = []
        self.get_count.restype = _UINT
        self.get_caps.argtypes = [ct.c_size_t, ct.POINTER(_JoyCaps), _UINT]
        self.get_caps.restype = _UINT
        self.get_pos.argtypes = [_UINT, ct.POINTER(_JoyInfo)]
        self.get_pos.restype = _UINT

    def identify(self, slot: int) -> Controller:
        caps = _JoyCaps()
        code = self.get_caps(slot, ct.byref(caps), ct.sizeof(caps))
        if code:
            raise _WinMMError("joyGetDevCapsW", slot, code)
        axes = []
        ranges = []
        if 0 < caps.num_axes <= len(_AXES):
            for index, (axis, _, flag) in enumerate(_AXES):
                advertised = bool(caps.caps & flag) if flag else caps.num_axes > index
                low, high = getattr(caps, f"{axis}_min"), getattr(caps, f"{axis}_max")
                if advertised and low < high:
                    axes.append(axis)
                    ranges.append((low, high))
        if not axes or len(axes) > caps.num_axes:
            raise ControllerError(f"Controller {slot} has no verifiable axis capabilities")
        oem_id, oem_name = _oem_identity(caps.registry_key, slot)
        return Controller(
            slot, oem_name or caps.name, tuple(axes), caps.name, caps.mid, caps.pid, oem_id,
            tuple(ranges),
        )

    def positions(self, identity: Controller) -> dict[str, float]:
        info = _JoyInfo(size=ct.sizeof(_JoyInfo))
        info.flags = sum(flag for axis, flag, _ in _AXES if axis in identity.axes)
        code = self.get_pos(identity.id, ct.byref(info))
        if code:
            raise _WinMMError("joyGetPosEx", identity.id, code)
        values = {}
        for axis, (low, high) in zip(identity.axes, identity.axis_ranges, strict=True):
            raw = getattr(info, axis)
            if not low <= raw <= high:
                raise ControllerError(f"Controller {identity.id} reported {axis} outside its range")
            values[axis] = (raw - low) / (high - low)
        return values


class PhysicalController:
    def __init__(self, backend: _WinMM, identity: Controller):
        self._backend = backend
        self.identity = identity

    def _check_identity(self) -> None:
        if self._backend.identify(self.identity.id) != self.identity:
            raise ControllerError(f"Controller {self.identity.id} identity changed")

    def sample(self) -> dict[str, float]:
        """Read all advertised axes in one call and scale the declared ranges to [0, 1]."""
        self._check_identity()
        sample = self._backend.positions(self.identity)
        self._check_identity()
        return sample


def discover_controllers() -> tuple[Controller, ...]:
    """Return readable controllers. Raise if all candidates are unverifiable."""
    backend = _WinMM()
    connected = []
    first_error = None
    for slot in range(backend.get_count()):
        try:
            identity = backend.identify(slot)
            controller = PhysicalController(backend, identity)
            controller.sample()
        except ControllerError as exc:
            if isinstance(exc, _WinMMError) and exc.code in _UNAVAILABLE_SLOT:
                continue
            if first_error is None:
                first_error = exc
            continue
        connected.append(identity)
    if not connected and first_error is not None:
        raise first_error
    return tuple(connected)


def open_controller(id: int) -> PhysicalController:
    """Open the explicitly selected WinMM slot for read-only sampling."""
    if type(id) is not int or id < 0:
        raise ControllerError("Controller ID must be a nonnegative Windows joystick slot")
    backend = _WinMM()
    if id >= backend.get_count():
        raise ControllerError(f"Controller {id} is outside the Windows joystick slot range")
    controller = PhysicalController(backend, backend.identify(id))
    controller.sample()
    return controller
