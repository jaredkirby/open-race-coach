from __future__ import annotations

import ctypes
from datetime import UTC, datetime
from pathlib import Path

from scripts.capture_ams2_fixture import build_metadata
from simcoach.adapters.ams2 import SHARED_MEMORY_VERSION, SharedMemory


def test_build_metadata_summarizes_raw_ams2_fixture_bytes() -> None:
    snapshot = SharedMemory()
    snapshot.mVersion = SHARED_MEMORY_VERSION
    snapshot.mBuildVersionNumber = 1234
    snapshot.mSequenceNumber = 2
    snapshot.mGameState = 2
    snapshot.mSessionState = 1
    snapshot.mRaceState = 1
    snapshot.mViewedParticipantIndex = 0
    snapshot.mNumParticipants = 1
    snapshot.mTrackLocation = b"Interlagos"
    snapshot.mTrackVariation = b"GP"
    snapshot.mCarName = b"Formula Inter"
    snapshot.mTrackLength = 4309.0
    snapshot.mLapInvalidated = True
    snapshot.mSpeed = 12.5
    snapshot.mRpm = 4500.0
    snapshot.mBrake = 0.2
    snapshot.mThrottle = 0.7
    snapshot.mSteering = -0.1
    snapshot.mGear = 3
    participant = snapshot.mParticipantInfo[0]
    participant.mIsActive = True
    participant.mCurrentLap = 4
    participant.mCurrentLapDistance = 1234.5
    participant.mRacePosition = 2
    participant.mWorldPosition[0] = 1.0
    participant.mWorldPosition[1] = 2.0
    participant.mWorldPosition[2] = 3.0

    raw = bytes(snapshot)
    metadata = build_metadata(
        raw,
        Path("sample.bin"),
        datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )

    assert metadata["fixture_schema_version"] == 1
    assert metadata["raw_path"] == "sample.bin"
    assert metadata["struct_size_bytes"] == ctypes.sizeof(SharedMemory)
    assert metadata["snapshot"]["mVersion"] == SHARED_MEMORY_VERSION
    assert metadata["snapshot"]["sequence_stable"] is True
    assert metadata["snapshot"]["mTrackLocation"] == "Interlagos"
    assert metadata["snapshot"]["mTrackVariation"] == "GP"
    assert metadata["snapshot"]["mCarName"] == "Formula Inter"
    assert metadata["snapshot"]["mLapInvalidated"] is True
    assert metadata["snapshot"]["participant"]["mCurrentLapDistance"] == 1234.5
    assert len(metadata["sha256"]) == 64
