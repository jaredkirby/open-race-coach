"""Automobilista 2 adapter using Project CARS 2 shared memory.

Source struct: CREST2-AMS2 `SharedMemory.h`, derived from the Project CARS 2
shared-memory API. Live field fidelity still must be validated with
`scripts/validate_ams2.py` after AMS2 updates.
"""

from __future__ import annotations

import ctypes
import mmap
import sys
import time
from typing import Any

from simcoach.adapters.base import NormalizedTick, SessionInfo, SimAdapter, VehicleState
from simcoach.utils.llm_logger import get_logger

LOGGER = get_logger(__name__)

ADAPTER_VERSION = "0.1.0"
SHARED_MEMORY_NAME = "$pcars2$"
SHARED_MEMORY_VERSION = 14
STRING_LENGTH_MAX = 64
STORED_PARTICIPANTS_MAX = 64
TYRE_MAX = 4
VEC_MAX = 3
TYRE_COMPOUND_NAME_LENGTH_MAX = 40

GAME_FRONT_END = 1
GAME_INGAME_PLAYING = 2
GAME_INGAME_PAUSED = 3
GAME_INGAME_INMENU_TIME_TICKING = 4
GAME_INGAME_REPLAY = 6
GAME_FRONT_END_REPLAY = 7

SESSION_PRACTICE = 1
SESSION_TEST = 2
SESSION_QUALIFY = 3
SESSION_FORMATION_LAP = 4
SESSION_RACE = 5
SESSION_TIME_ATTACK = 6

PIT_MODE_NONE = 0


class ParticipantInfo(ctypes.Structure):
    _fields_ = [
        ("mIsActive", ctypes.c_bool),
        ("mName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mWorldPosition", ctypes.c_float * VEC_MAX),
        ("mCurrentLapDistance", ctypes.c_float),
        ("mRacePosition", ctypes.c_uint),
        ("mLapsCompleted", ctypes.c_uint),
        ("mCurrentLap", ctypes.c_uint),
        ("mCurrentSector", ctypes.c_int),
    ]


class SharedMemory(ctypes.Structure):
    """ctypes mirror of the AMS2/PC2 shared-memory struct.

    Important offsets are asserted in tests and exposed by `important_offsets()`.
    """

    _fields_ = [
        ("mVersion", ctypes.c_uint),
        ("mBuildVersionNumber", ctypes.c_uint),
        ("mGameState", ctypes.c_uint),
        ("mSessionState", ctypes.c_uint),
        ("mRaceState", ctypes.c_uint),
        ("mViewedParticipantIndex", ctypes.c_int),
        ("mNumParticipants", ctypes.c_int),
        ("mParticipantInfo", ParticipantInfo * STORED_PARTICIPANTS_MAX),
        ("mUnfilteredThrottle", ctypes.c_float),
        ("mUnfilteredBrake", ctypes.c_float),
        ("mUnfilteredSteering", ctypes.c_float),
        ("mUnfilteredClutch", ctypes.c_float),
        ("mCarName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mCarClassName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mLapsInEvent", ctypes.c_uint),
        ("mTrackLocation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTrackVariation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTrackLength", ctypes.c_float),
        ("mNumSectors", ctypes.c_int),
        ("mLapInvalidated", ctypes.c_bool),
        ("mBestLapTime", ctypes.c_float),
        ("mLastLapTime", ctypes.c_float),
        ("mCurrentTime", ctypes.c_float),
        ("mSplitTimeAhead", ctypes.c_float),
        ("mSplitTimeBehind", ctypes.c_float),
        ("mSplitTime", ctypes.c_float),
        ("mEventTimeRemaining", ctypes.c_float),
        ("mPersonalFastestLapTime", ctypes.c_float),
        ("mWorldFastestLapTime", ctypes.c_float),
        ("mCurrentSector1Time", ctypes.c_float),
        ("mCurrentSector2Time", ctypes.c_float),
        ("mCurrentSector3Time", ctypes.c_float),
        ("mFastestSector1Time", ctypes.c_float),
        ("mFastestSector2Time", ctypes.c_float),
        ("mFastestSector3Time", ctypes.c_float),
        ("mPersonalFastestSector1Time", ctypes.c_float),
        ("mPersonalFastestSector2Time", ctypes.c_float),
        ("mPersonalFastestSector3Time", ctypes.c_float),
        ("mWorldFastestSector1Time", ctypes.c_float),
        ("mWorldFastestSector2Time", ctypes.c_float),
        ("mWorldFastestSector3Time", ctypes.c_float),
        ("mHighestFlagColour", ctypes.c_uint),
        ("mHighestFlagReason", ctypes.c_uint),
        ("mPitMode", ctypes.c_uint),
        ("mPitSchedule", ctypes.c_uint),
        ("mCarFlags", ctypes.c_uint),
        ("mOilTempCelsius", ctypes.c_float),
        ("mOilPressureKPa", ctypes.c_float),
        ("mWaterTempCelsius", ctypes.c_float),
        ("mWaterPressureKPa", ctypes.c_float),
        ("mFuelPressureKPa", ctypes.c_float),
        ("mFuelLevel", ctypes.c_float),
        ("mFuelCapacity", ctypes.c_float),
        ("mSpeed", ctypes.c_float),
        ("mRpm", ctypes.c_float),
        ("mMaxRPM", ctypes.c_float),
        ("mBrake", ctypes.c_float),
        ("mThrottle", ctypes.c_float),
        ("mClutch", ctypes.c_float),
        ("mSteering", ctypes.c_float),
        ("mGear", ctypes.c_int),
        ("mNumGears", ctypes.c_int),
        ("mOdometerKM", ctypes.c_float),
        ("mAntiLockActive", ctypes.c_bool),
        ("mLastOpponentCollisionIndex", ctypes.c_int),
        ("mLastOpponentCollisionMagnitude", ctypes.c_float),
        ("mBoostActive", ctypes.c_bool),
        ("mBoostAmount", ctypes.c_float),
        ("mOrientation", ctypes.c_float * VEC_MAX),
        ("mLocalVelocity", ctypes.c_float * VEC_MAX),
        ("mWorldVelocity", ctypes.c_float * VEC_MAX),
        ("mAngularVelocity", ctypes.c_float * VEC_MAX),
        ("mLocalAcceleration", ctypes.c_float * VEC_MAX),
        ("mWorldAcceleration", ctypes.c_float * VEC_MAX),
        ("mExtentsCentre", ctypes.c_float * VEC_MAX),
        ("mTyreFlags", ctypes.c_uint * TYRE_MAX),
        ("mTerrain", ctypes.c_uint * TYRE_MAX),
        ("mTyreY", ctypes.c_float * TYRE_MAX),
        ("mTyreRPS", ctypes.c_float * TYRE_MAX),
        ("mTyreSlipSpeed", ctypes.c_float * TYRE_MAX),
        ("mTyreTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreGrip", ctypes.c_float * TYRE_MAX),
        ("mTyreHeightAboveGround", ctypes.c_float * TYRE_MAX),
        ("mTyreLateralStiffness", ctypes.c_float * TYRE_MAX),
        ("mTyreWear", ctypes.c_float * TYRE_MAX),
        ("mBrakeDamage", ctypes.c_float * TYRE_MAX),
        ("mSuspensionDamage", ctypes.c_float * TYRE_MAX),
        ("mBrakeTempCelsius", ctypes.c_float * TYRE_MAX),
        ("mTyreTreadTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreLayerTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreCarcassTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreRimTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreInternalAirTemp", ctypes.c_float * TYRE_MAX),
        ("mCrashState", ctypes.c_uint),
        ("mAeroDamage", ctypes.c_float),
        ("mEngineDamage", ctypes.c_float),
        ("mAmbientTemperature", ctypes.c_float),
        ("mTrackTemperature", ctypes.c_float),
        ("mRainDensity", ctypes.c_float),
        ("mWindSpeed", ctypes.c_float),
        ("mWindDirectionX", ctypes.c_float),
        ("mWindDirectionY", ctypes.c_float),
        ("mCloudBrightness", ctypes.c_float),
        ("mSequenceNumber", ctypes.c_uint),
        ("mWheelLocalPositionY", ctypes.c_float * TYRE_MAX),
        ("mSuspensionTravel", ctypes.c_float * TYRE_MAX),
        ("mSuspensionVelocity", ctypes.c_float * TYRE_MAX),
        ("mAirPressure", ctypes.c_float * TYRE_MAX),
        ("mEngineSpeed", ctypes.c_float),
        ("mEngineTorque", ctypes.c_float),
        ("mWings", ctypes.c_float * 2),
        ("mHandBrake", ctypes.c_float),
        ("mCurrentSector1Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCurrentSector2Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCurrentSector3Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector1Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector2Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector3Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestLapTimes", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mLastLapTimes", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mLapsInvalidated", ctypes.c_bool * STORED_PARTICIPANTS_MAX),
        ("mRaceStates", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mPitModes", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mOrientations", (ctypes.c_float * VEC_MAX) * STORED_PARTICIPANTS_MAX),
        ("mSpeeds", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCarNames", (ctypes.c_char * STRING_LENGTH_MAX) * STORED_PARTICIPANTS_MAX),
        ("mCarClassNames", (ctypes.c_char * STRING_LENGTH_MAX) * STORED_PARTICIPANTS_MAX),
        ("mEnforcedPitStopLap", ctypes.c_int),
        ("mTranslatedTrackLocation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTranslatedTrackVariation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mBrakeBias", ctypes.c_float),
        ("mTurboBoostPressure", ctypes.c_float),
        ("mTyreCompound", (ctypes.c_char * TYRE_COMPOUND_NAME_LENGTH_MAX) * TYRE_MAX),
        ("mPitSchedules", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mHighestFlagColours", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mHighestFlagReasons", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mNationalities", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mSnowDensity", ctypes.c_float),
        ("mSessionDuration", ctypes.c_float),
        ("mSessionAdditionalLaps", ctypes.c_int),
        ("mTyreTempLeft", ctypes.c_float * TYRE_MAX),
        ("mTyreTempCenter", ctypes.c_float * TYRE_MAX),
        ("mTyreTempRight", ctypes.c_float * TYRE_MAX),
        ("mDrsState", ctypes.c_uint),
        ("mRideHeight", ctypes.c_float * TYRE_MAX),
        ("mJoyPad0", ctypes.c_uint),
        ("mDPad", ctypes.c_uint),
        ("mAntiLockSetting", ctypes.c_int),
        ("mTractionControlSetting", ctypes.c_int),
        ("mErsDeploymentMode", ctypes.c_int),
        ("mErsAutoModeEnabled", ctypes.c_bool),
        ("mClutchTemp", ctypes.c_float),
        ("mClutchWear", ctypes.c_float),
        ("mClutchOverheated", ctypes.c_bool),
        ("mClutchSlipping", ctypes.c_bool),
        ("mYellowFlagState", ctypes.c_int),
        ("mSessionIsPrivate", ctypes.c_bool),
        ("mLaunchStage", ctypes.c_int),
    ]


def important_offsets() -> dict[str, int]:
    return {
        "mVersion": SharedMemory.mVersion.offset,
        "mParticipantInfo": SharedMemory.mParticipantInfo.offset,
        "mTrackLocation": SharedMemory.mTrackLocation.offset,
        "mLapInvalidated": SharedMemory.mLapInvalidated.offset,
        "mSpeed": SharedMemory.mSpeed.offset,
        "mRpm": SharedMemory.mRpm.offset,
        "mBrake": SharedMemory.mBrake.offset,
        "mThrottle": SharedMemory.mThrottle.offset,
        "mSteering": SharedMemory.mSteering.offset,
        "mGear": SharedMemory.mGear.offset,
        "mSequenceNumber": SharedMemory.mSequenceNumber.offset,
    }


def normalize_matching_key(label: str) -> str:
    chars: list[str] = []
    previous_was_sep = False
    for char in label.strip().lower():
        if char.isalnum():
            chars.append(char)
            previous_was_sep = False
        elif not previous_was_sep:
            chars.append("_")
            previous_was_sep = True
    return "".join(chars).strip("_")


def decode_c_string(value: Any) -> str:
    raw = bytes(value)
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def _session_type(value: int) -> str:
    return {
        SESSION_PRACTICE: "practice",
        SESSION_TEST: "practice",
        SESSION_QUALIFY: "qualifying",
        SESSION_FORMATION_LAP: "race",
        SESSION_RACE: "race",
        SESSION_TIME_ATTACK: "hotlap",
    }.get(value, "practice")


def _vehicle_state(game_state: int, pit_mode: int) -> VehicleState:
    if game_state in {GAME_FRONT_END, GAME_INGAME_INMENU_TIME_TICKING}:
        return "menu"
    if game_state == GAME_INGAME_PAUSED:
        return "paused"
    if game_state in {GAME_INGAME_REPLAY, GAME_FRONT_END_REPLAY}:
        return "replay"
    if game_state == GAME_INGAME_PLAYING and pit_mode != PIT_MODE_NONE:
        return "pit"
    if game_state == GAME_INGAME_PLAYING:
        return "running"
    return "unknown"


class AMS2Adapter(SimAdapter):
    sim_name = "ams2"
    adapter_version = ADAPTER_VERSION

    def __init__(self) -> None:
        self._mmap: mmap.mmap | None = None
        self._capture_started_at: float | None = None

    def connect(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("AMS2 shared-memory capture requires Windows")
        LOGGER.info("[START] ams2.connect | name=%s", SHARED_MEMORY_NAME)
        self._mmap = mmap.mmap(
            -1,
            ctypes.sizeof(SharedMemory),
            tagname=SHARED_MEMORY_NAME,
            access=mmap.ACCESS_READ,
        )
        self._capture_started_at = time.monotonic()
        LOGGER.info("[END] ams2.connect | size=%s", ctypes.sizeof(SharedMemory))

    def disconnect(self) -> None:
        LOGGER.info("[START] ams2.disconnect")
        if self._mmap is not None:
            self._mmap.close()
        self._mmap = None
        LOGGER.info("[END] ams2.disconnect")

    def read_tick(self) -> NormalizedTick | None:
        snapshot = self._read_snapshot()
        if snapshot is None:
            return None
        capture_t = time.monotonic() - (self._capture_started_at or time.monotonic())
        return self.tick_from_snapshot(snapshot, capture_t)

    def read_session_info(self) -> SessionInfo | None:
        snapshot = self._read_snapshot()
        if snapshot is None:
            return None
        return self.session_info_from_snapshot(snapshot)

    def _read_snapshot(self) -> SharedMemory | None:
        if self._mmap is None:
            raise RuntimeError("AMS2 adapter is not connected")
        self._mmap.seek(0)
        raw = self._mmap.read(ctypes.sizeof(SharedMemory))
        if len(raw) != ctypes.sizeof(SharedMemory):
            LOGGER.error("[ERROR] ams2.read_snapshot | short_read size=%s", len(raw))
            return None
        snapshot = SharedMemory.from_buffer_copy(raw)
        if snapshot.mVersion != SHARED_MEMORY_VERSION:
            LOGGER.warning(
                "[WARN] ams2.read_snapshot | unexpected_version expected=%s actual=%s",
                SHARED_MEMORY_VERSION,
                snapshot.mVersion,
            )
        if snapshot.mSequenceNumber % 2 == 1:
            LOGGER.info("[SKIP] ams2.read_snapshot | sequence write in progress")
            return None
        return snapshot

    @staticmethod
    def tick_from_snapshot(snapshot: SharedMemory, capture_t: float) -> NormalizedTick | None:
        participant = _viewed_participant(snapshot)
        if participant is None:
            return None
        track_length_m = float(snapshot.mTrackLength)
        current_dist_m = float(participant.mCurrentLapDistance)
        if track_length_m <= 0.0 or current_dist_m < 0.0:
            LOGGER.info(
                "[SKIP] ams2.tick_from_snapshot | missing_lap_progress track_length=%s dist=%s",
                track_length_m,
                current_dist_m,
            )
            return None

        lap_dist_pct = max(0.0, min(current_dist_m / track_length_m, 1.0))
        world = participant.mWorldPosition
        return NormalizedTick(
            t=capture_t,
            lap=int(participant.mCurrentLap) + 1,
            lap_dist_pct=lap_dist_pct,
            lap_dist_m=current_dist_m,
            speed=max(0.0, float(snapshot.mSpeed)),
            throttle=max(0.0, min(float(snapshot.mThrottle), 1.0)),
            brake=max(0.0, min(float(snapshot.mBrake), 1.0)),
            steering=max(-1.0, min(float(snapshot.mSteering), 1.0)),
            gear=int(snapshot.mGear),
            rpm=max(0.0, float(snapshot.mRpm)),
            vehicle_state=_vehicle_state(int(snapshot.mGameState), int(snapshot.mPitMode)),
            pos_x=float(world[0]),
            pos_y=float(world[2]),
            pos_z=float(world[1]),
        )

    @staticmethod
    def session_info_from_snapshot(snapshot: SharedMemory) -> SessionInfo | None:
        track_raw = " ".join(
            part
            for part in [
                decode_c_string(snapshot.mTrackLocation),
                decode_c_string(snapshot.mTrackVariation),
            ]
            if part
        ).strip()
        car_raw = decode_c_string(snapshot.mCarName)
        track = normalize_matching_key(track_raw)
        car = normalize_matching_key(car_raw)
        if not track or not car:
            LOGGER.info(
                "[SKIP] ams2.session_info_from_snapshot | metadata_unavailable track=%r car=%r",
                track_raw,
                car_raw,
            )
            return None

        track_length_m = float(snapshot.mTrackLength) if snapshot.mTrackLength > 0 else None
        lap_dist_source = "sim" if track_length_m is not None else "unavailable"
        return {
            "sim": "ams2",
            "track_raw": track_raw,
            "track": track,
            "car_raw": car_raw,
            "car": car,
            "session_type": _session_type(int(snapshot.mSessionState)),  # type: ignore[typeddict-item]
            "track_length_m": track_length_m,
            "lap_dist_m_source": lap_dist_source,  # type: ignore[typeddict-item]
            "adapter_version": ADAPTER_VERSION,
            "validity_method": "sim_flag_plus_inferred",
            "session_schema_version": 1,
            "tick_schema_version": 1,
            "lap_schema_version": 1,
            "lap_invalidated": bool(snapshot.mLapInvalidated),
        }


def _viewed_participant(snapshot: SharedMemory) -> ParticipantInfo | None:
    index = int(snapshot.mViewedParticipantIndex)
    if index < 0 or index >= STORED_PARTICIPANTS_MAX:
        LOGGER.info("[SKIP] ams2.viewed_participant | bad_index=%s", index)
        return None
    if snapshot.mNumParticipants <= 0 or index >= snapshot.mNumParticipants:
        LOGGER.info(
            "[SKIP] ams2.viewed_participant | index=%s participants=%s",
            index,
            int(snapshot.mNumParticipants),
        )
        return None
    participant = snapshot.mParticipantInfo[index]
    if not participant.mIsActive:
        LOGGER.info("[SKIP] ams2.viewed_participant | inactive index=%s", index)
        return None
    return participant
