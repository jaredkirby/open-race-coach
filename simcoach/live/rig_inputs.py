"""Bounded AMS2 input observation without simulator or controller writes."""

from __future__ import annotations

import ctypes
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from simcoach.adapters.ams2 import (
    GAME_INGAME_PLAYING,
    SESSION_PRACTICE,
    SESSION_TEST,
    SHARED_MEMORY_VERSION,
    STORED_PARTICIPANTS_MAX,
    SharedMemory,
)
from simcoach.adapters.ams2_mapping import ExistingMapping
from simcoach.live.rig_controller import ControllerError

CONTROLS = ("steering", "throttle", "brake")
PROMPTS = {
    "steering": "Gently turn the wheel to one side, briefly hold, then return to center.",
    "throttle": "Gently press the throttle, briefly hold, then fully release.",
    "brake": "Press the brake, briefly hold, then fully release.",
}
POLL_S = 0.05
MAX_GAP_S = 0.5
MATCH_S = 0.25
MAX_NATIVE_READ_S = 0.1
MIN_RESPONSE = 0.15


def read_existing(mapping: ExistingMapping) -> SharedMemory | None:
    try:
        raw = mapping.read()
    finally:
        mapping.close()
    if raw is None or len(raw) != ctypes.sizeof(SharedMemory):
        return None
    return SharedMemory.from_buffer_copy(raw)


def ams2_reader() -> Callable[[], SharedMemory | None]:
    mapping = ExistingMapping(ctypes.sizeof(SharedMemory), SharedMemory.mSequenceNumber.offset)
    return lambda: read_existing(mapping)


@dataclass(frozen=True)
class Frame:
    at: float
    sim: dict[str, float]
    physical: dict[str, float]


def _identity(snapshot: SharedMemory) -> tuple | None:
    index = snapshot.mViewedParticipantIndex
    if not 0 <= index < min(snapshot.mNumParticipants, STORED_PARTICIPANTS_MAX):
        return None
    participant = snapshot.mParticipantInfo[index]
    if not participant.mIsActive:
        return None
    return (
        index, snapshot.mBuildVersionNumber, snapshot.mSessionState, snapshot.mNumParticipants,
        participant.mName, snapshot.mCarName, snapshot.mTrackLocation, snapshot.mTrackVariation,
    )


def _values(snapshot: SharedMemory) -> dict[str, float] | None:
    values = dict(steering=snapshot.mSteering, throttle=snapshot.mThrottle, brake=snapshot.mBrake)
    if any(not (-1 if key == "steering" else 0) <= value <= 1 for key, value in values.items()):
        return None
    return values


def _gesture(series: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Use the first sample as a baseline and require a sustained excursion and return."""
    if len(series) < 5:
        return None
    baseline = series[0][1]
    outward = None
    peak_count = 0
    prior_sign = 0
    for at, value in series[1:]:
        delta = value - baseline
        if abs(delta) >= MIN_RESPONSE:
            sign = 1 if delta > 0 else -1
            if sign != prior_sign:
                outward, peak_count = None, 0
            if outward is None:
                outward = at
            peak_count += 1
            prior_sign = sign
        elif abs(delta) <= 0.07:
            if outward is not None and peak_count >= 2:
                return outward, at
            outward, peak_count = None, 0
    return None


def _axis_gestures(frames: list[Frame]) -> dict[str, tuple[float, float]]:
    if not frames:
        return {}
    common = set(frames[0].physical).intersection(*(frame.physical for frame in frames))
    gestures = {}
    for axis in common:
        movement = _gesture([(frame.at, frame.physical[axis]) for frame in frames])
        if movement:
            gestures[axis] = movement
    return gestures


def _game_gesture(frames: list[Frame], control: str) -> tuple[float, float] | None:
    baseline_limit = 0.12 if control == "steering" else 0.08
    if frames and abs(frames[0].sim[control]) <= baseline_limit:
        return _gesture([(frame.at, frame.sim[control]) for frame in frames])
    return None


def result_template(mode: str) -> dict:
    controls = {
        control: {"in_game": "not_observed", "physical": "not_observed", "axis": None}
        for control in CONTROLS
    }
    if mode == "parked":
        for entry in controls.values():
            entry.update(windows_response="unavailable", other_game_controls=[])
    return {
        "schema_version": 1,
        "source": "ams2_shared_memory",
        "mode": mode,
        "status": "unavailable",
        "reason": "mapping_unavailable",
        "participant_index": None,
        "player_attribution": "unconfirmed",
        "physical_attribution": "not_observed",
        "controller_status": "not_requested",
        "frame_count": 0,
        "controls": controls,
    }


def observe(
    read: Callable[[], SharedMemory | None],
    *,
    mode: str,
    duration_s: float,
    player_confirmed: bool,
    participant_index: int | None = None,
    physical_confirmed: bool = False,
    physical_read: Callable[[], dict[str, float]] | None = None,
    pedal_read: Callable[[], dict[str, float]] | None = None,
    separate_pedals: bool = False,
    preparation_s: float = 0,
    prompt: Callable[[str], None] = lambda _message: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    result = result_template(mode)
    parked = mode == "parked"
    if parked and not (player_confirmed and physical_confirmed):
        result["reason"] = "driver_confirmation_required"
        return result
    absent_deadline = clock() + min(duration_s, 2)
    deadline = absent_deadline + (10 if parked and preparation_s else 0)
    sequence = None
    last_fresh = None
    scope = None
    frames = {control: [] for control in CONTROLS}
    started = None
    ready_since = None
    mapping_seen = False
    current_stage = None
    controller_failed = False
    physical_requested = bool(physical_read or separate_pedals)
    physical_sampled = False
    split_sources = bool(separate_pedals and pedal_read is not physical_read)
    if physical_requested:
        result["controller_status"] = "unavailable"

    while (now := clock()) < deadline:
        try:
            snapshot = read()
        except FileNotFoundError:
            result["reason"] = "mapping_unavailable"
            if started is not None:
                result["reason"] = "source_lost"
                return result
            if not mapping_seen and clock() >= absent_deadline:
                return result
            sequence, scope = None, None
            last_fresh, ready_since = None, None
            sleep(POLL_S)
            continue
        except OSError:
            result["reason"] = "mapping_unreadable"
            return result

        mapping_seen = True
        now = clock()
        if now >= deadline:
            break
        if last_fresh is not None and now - last_fresh > MAX_GAP_S:
            result["reason"] = "source_stalled"
            if started is not None:
                return result
            sequence, scope = None, None
            last_fresh, ready_since = None, None
        reason = _disqualify(snapshot, parked)
        if reason:
            result["reason"] = reason
            if started is not None and snapshot is not None:
                return result
            if started is None and snapshot is not None:
                sequence, scope = None, None
                last_fresh, ready_since = None, None
            sleep(POLL_S)
            continue
        assert snapshot is not None
        identity = _identity(snapshot)
        if identity is None or (participant_index is not None and identity[0] != participant_index):
            result["reason"] = "view_unavailable_or_mismatched"
            if started is not None:
                return result
            sequence, scope = None, None
            last_fresh, ready_since = None, None
            sleep(POLL_S)
            continue

        value = snapshot.mSequenceNumber
        if sequence is None or (started is None and (identity != scope or value < sequence)):
            sequence, scope = value, identity
            last_fresh, ready_since = None, None
            result["reason"] = "source_not_advancing"
        elif identity != scope or value < sequence:
            result["reason"] = "source_or_view_changed"
            return result
        elif value > sequence:
            sequence, last_fresh = value, now
            if started is None:
                if ready_since is None:
                    ready_since = now
                if now - ready_since < preparation_s:
                    result["reason"] = "stationary_preparation_incomplete"
                    sleep(POLL_S)
                    continue
                started = now
                deadline = started + duration_s
            if parked:
                stage = min(int((now - started) / (duration_s / len(CONTROLS))), len(CONTROLS) - 1)
                control = CONTROLS[stage]
                targets = (control,)
            else:
                targets = CONTROLS
            values = _values(snapshot)
            assert values is not None
            physical = {}
            device = (
                pedal_read if parked and separate_pedals and control != "steering"
                else physical_read
            )
            if device and not controller_failed:
                try:
                    candidate = device()
                    if clock() - now <= MAX_NATIVE_READ_S:
                        physical = candidate
                        physical_sampled |= bool(physical)
                    else:
                        controller_failed = True
                        result["controller_status"] = "lost_or_changed"
                except (ControllerError, OSError, ValueError):
                    controller_failed = True
                    result["controller_status"] = "lost_or_changed"
            frame = Frame(now, values, physical)
            for target in targets:
                frames[target].append(frame)
            if parked and current_stage != stage:
                current_stage = stage
                prompt(PROMPTS[control])
            result["frame_count"] += 1
        sleep(POLL_S)

    if started is None:
        return result
    if clock() - last_fresh > MAX_GAP_S:
        result["reason"] = "source_stalled"
        return result
    result["participant_index"] = scope[0]
    if player_confirmed:
        result["player_attribution"] = "driver_confirmed_view"
    if physical_sampled or controller_failed:
        result["controller_status"] = "lost_or_changed" if controller_failed else "sampled"
    _summarize(
        result, frames, parked=parked, correlate=physical_sampled and not controller_failed,
        split_sources=split_sources,
    )
    return result


def _disqualify(snapshot: SharedMemory | None, parked: bool) -> str | None:
    if snapshot is None:
        return "coherent_frame_unavailable"
    if snapshot.mVersion != SHARED_MEMORY_VERSION:
        return "unsupported_shared_memory_version"
    if snapshot.mGameState != GAME_INGAME_PLAYING:
        return "not_in_live_cockpit"
    if _values(snapshot) is None:
        return "invalid_control_values"
    if parked and (
        snapshot.mSessionState not in {SESSION_PRACTICE, SESSION_TEST}
        or snapshot.mGear != 0
        # Neutral plus a low speed bound is checked every qualifying frame.
        or not math.isfinite(snapshot.mSpeed)
        or not 0 <= snapshot.mSpeed <= 0.2
    ):
        return "parked_check_requires_stationary_neutral_practice_or_test"
    return None


def _summarize(
    result: dict, frames: dict[str, list[Frame]], *, parked: bool, correlate: bool,
    split_sources: bool,
) -> None:
    candidates = {}
    for control, samples in frames.items():
        entry = result["controls"][control]
        if parked:
            gesture = _game_gesture(samples, control)
            entry["other_game_controls"] = [
                other for other in CONTROLS if other != control and _game_gesture(samples, other)
            ]
            if gesture:
                entry.update(in_game="observed", physical="driver_confirmed_gesture")
            if correlate and samples and all(frame.physical for frame in samples):
                movements = _axis_gestures(samples)
                if len(movements) > 1:
                    entry["windows_response"] = "ambiguous"
                elif len(movements) == 1:
                    entry["windows_response"] = "observed"
                    axis, movement = next(iter(movements.items()))
                    if gesture and all(
                        abs(left - right) <= MATCH_S
                        for left, right in zip(gesture, movement, strict=True)
                    ):
                        source = None
                        if split_sources:
                            source = "wheel" if control == "steering" else "pedals"
                        candidates[control] = (source, axis)
                else:
                    entry["windows_response"] = "not_observed"
        else:
            values = [frame.sim[control] for frame in samples]
            if len(values) >= 3 and max(values) - min(values) >= MIN_RESPONSE:
                entry["in_game"] = "observed"

    for control, candidate in candidates.items():
        if sum(candidate == other for other in candidates.values()) == 1:
            source, axis = candidate
            entry = result["controls"][control]
            entry.update(physical="timing_correlated", axis=axis)
            if source:
                entry["axis_source"] = source

    controls = result["controls"].values()
    if all(item["physical"] == "timing_correlated" for item in controls):
        result["physical_attribution"] = "timing_correlated"
    elif any(item["physical"] != "not_observed" for item in controls):
        result["physical_attribution"] = "driver_confirmed_gesture"
    if parked and any(item["other_game_controls"] for item in controls):
        result.update(status="incomplete", reason="unexpected_control_response")
    elif not all(item["in_game"] == "observed" for item in controls):
        result.update(status="incomplete", reason="control_response_incomplete")
    elif result["player_attribution"] != "driver_confirmed_view":
        result.update(status="incomplete", reason="local_player_unconfirmed")
    else:
        result.update(status="observed", reason="control_responses_observed")
