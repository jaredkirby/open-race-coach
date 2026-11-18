from __future__ import annotations

import ctypes
import json
from dataclasses import fields
from pathlib import Path

import pytest

import simcoach.adapters.ac as ac_adapter
import simcoach.adapters.acc as acc_adapter
import simcoach.adapters.ams2 as ams2_adapter
from simcoach.adapters.acc import (
    ACC_LIVE,
    ACC_PRACTICE,
    ACCAdapter,
    ACCGraphics,
    ACCPhysics,
    ACCStatic,
)
from simcoach.adapters.acc import (
    important_offsets as acc_important_offsets,
)
from simcoach.adapters.ams2 import (
    GAME_INGAME_PLAYING,
    PIT_MODE_NONE,
    SESSION_PRACTICE,
    SHARED_MEMORY_VERSION,
    STORED_PARTICIPANTS_MAX,
    AMS2Adapter,
    ParticipantInfo,
    SharedMemory,
    important_offsets,
    normalize_matching_key,
)
from simcoach.adapters.base import NormalizedTick, SimAdapter

REST_CARS_FIXTURE = Path(__file__).parent / "fixtures" / "pcars2" / "rest-cars_example.json"
CREST_FIXTURE = Path(__file__).parent / "fixtures" / "pcars2" / "crest_example.json"


@pytest.mark.parametrize("origin", [0.0, 1000.0])
def test_ams2_capture_time_advances_when_coarse_clock_is_frozen(
    monkeypatch: pytest.MonkeyPatch, origin: float,
) -> None:
    snapshot = make_snapshot()
    page = SyntheticAMS2Page(bytes(snapshot))
    counter = iter([origin, origin + 0.001, origin + 0.002])
    monkeypatch.setattr(ams2_adapter.sys, "platform", "win32")
    monkeypatch.setattr(ams2_adapter, "ExistingMapping", lambda *args, **kwargs: page)
    monkeypatch.setattr(ams2_adapter.time, "monotonic", lambda: 17.0)
    monkeypatch.setattr(ams2_adapter.time, "perf_counter", lambda: next(counter))
    adapter = AMS2Adapter()
    adapter.connect()
    try:
        assert adapter.read_tick() is None
        snapshot.mSequenceNumber = 4
        page.replace(snapshot)
        first = adapter.read_tick()
        snapshot.mSequenceNumber = 6
        page.replace(snapshot)
        second = adapter.read_tick()
        assert first is not None and second is not None
        assert first.t == pytest.approx(0.001)
        assert second.t == pytest.approx(0.002)
        assert second.t > first.t
    finally:
        adapter.disconnect()


def test_ams2_struct_key_offsets() -> None:
    assert ctypes.sizeof(ParticipantInfo) == 100
    assert ctypes.sizeof(SharedMemory) == 20700
    assert important_offsets() == {
        "mVersion": 0,
        "mParticipantInfo": 28,
        "mTrackLocation": 6576,
        "mLapInvalidated": 6712,
        "mSpeed": 6848,
        "mRpm": 6852,
        "mBrake": 6860,
        "mThrottle": 6864,
        "mSteering": 6872,
        "mGear": 6876,
        "mSequenceNumber": 7320,
    }


def test_ams2_snapshot_to_normalized_tick_and_session_info() -> None:
    snapshot = make_snapshot()
    tick = AMS2Adapter.tick_from_snapshot(snapshot, capture_t=12.5)
    assert tick is not None
    assert tick.t == 12.5
    assert tick.lap == 4
    assert tick.lap_dist_pct == 0.5
    assert tick.lap_dist_m == 2154.5
    assert tick.speed == 55.0
    assert tick.throttle == pytest.approx(0.7)
    assert tick.brake == pytest.approx(0.2)
    assert tick.steering == pytest.approx(-0.1)
    assert tick.gear == 3
    assert tick.rpm == 9000.0
    assert tick.vehicle_state == "running"
    assert tick.pos_x == 10.0
    assert tick.pos_y == 30.0
    assert tick.pos_z == 20.0

    info = AMS2Adapter.session_info_from_snapshot(snapshot)
    assert info is not None
    assert info["sim"] == "ams2"
    assert info["track_raw"] == "Interlagos GP"
    assert info["track"] == "interlagos_gp"
    assert info["car_raw"] == "Formula Inter"
    assert info["car"] == "formula_inter"
    assert info["session_type"] == "practice"
    assert info["track_length_m"] == 4309.0
    assert info["lap_dist_m_source"] == "sim"
    assert info["validity_method"] == "sim_flag_plus_inferred"
    assert info["lap_invalidated"] is True


def test_ams2_paired_observation_never_combines_adjacent_snapshot_lap_flags(monkeypatch):
    old, new = make_snapshot(), make_snapshot()
    baseline = make_snapshot()
    baseline.mSequenceNumber = 0
    new.mSequenceNumber = 4
    old.mParticipantInfo[0].mCurrentLap = 2
    old.mLapInvalidated = True
    new.mParticipantInfo[0].mCurrentLap = 3
    new.mLapInvalidated = False
    new.mCarName = b"Other Car"
    snapshots = [baseline, old, new]
    reads = []
    adapter = AMS2Adapter()
    adapter._capture_started_at = 10

    def read():
        reads.append(True)
        return snapshots.pop(0)

    monkeypatch.setattr(adapter, "_read_snapshot", read)
    counter = iter([10.1, 10.2])
    monkeypatch.setattr(ams2_adapter.time, "perf_counter", lambda: next(counter))
    assert adapter.read_observation()[1] is None
    old_info, old_tick = adapter.read_observation()
    assert len(reads) == 2
    assert old_info["lap_invalidated"] is True and old_tick.lap == 3
    assert old_info["car"] == "formula_inter"
    new_info, new_tick = adapter.read_observation()
    assert len(reads) == 3
    assert new_info["lap_invalidated"] is False and new_tick.lap == 4
    assert new_info["car"] == "other_car"
    assert old_tick.t < new_tick.t


class SyntheticAMS2Page:
    """Synthetic bytes after the native reader's stability check."""

    def __init__(self, raw):
        self.raw = raw
        self.fail_reads = False
        self.releases = 0

    def close(self):
        self.releases += 1

    def read(self):
        if self.fail_reads:
            raise OSError("synthetic read failure")
        return self.raw

    def replace(self, snapshot):
        self.raw = bytes(snapshot) if snapshot.mSequenceNumber % 2 == 0 else None

def fresh_adapter():
    snapshot = make_snapshot()
    page = SyntheticAMS2Page(bytes(snapshot))
    adapter = AMS2Adapter()
    adapter._source = page
    adapter._capture_started_at = 0
    return adapter, page, snapshot


def test_ams2_inspection_does_not_consume_tick_sequence_and_apis_share_cursor():
    adapter, page, snapshot = fresh_adapter()
    assert adapter._read_snapshot() is not None
    assert adapter.read_session_info()["source_epoch"] == 0
    info, tick = adapter.read_observation()
    assert info is not None and tick is None  # first page only establishes baseline
    snapshot.mSequenceNumber = 4
    page.replace(snapshot)
    assert adapter.read_session_info() is not None
    assert adapter._read_snapshot().mSequenceNumber == 4
    first = adapter.read_tick()
    assert first is not None
    assert adapter.read_observation() == (info, None)
    snapshot.mSequenceNumber = 6
    page.replace(snapshot)
    second = adapter.read_observation()[1]
    assert second is not None and second.t > first.t
    assert first.speed == second.speed  # equal values are valid when source advances
    assert adapter.read_tick() is None


@pytest.mark.parametrize("missing", ["short", "unsupported", "odd", "error"])
def test_ams2_same_sequence_remains_stale_across_failed_reads(missing):
    adapter, page, snapshot = fresh_adapter()
    assert adapter.read_tick() is None
    snapshot.mSequenceNumber = 4
    page.replace(snapshot)
    assert adapter.read_observation()[1] is not None
    if missing == "short":
        page.raw = b""
    elif missing == "unsupported":
        snapshot.mVersion = 15
        page.replace(snapshot)
    elif missing == "odd":
        snapshot.mSequenceNumber = 5
        page.replace(snapshot)
    else:
        page.fail_reads = True
    if missing == "error":
        with pytest.raises(OSError, match="synthetic"):
            adapter.read_observation()
        page.fail_reads = False
    else:
        assert adapter.read_observation() == (None, None)
    snapshot.mVersion = 14
    snapshot.mSequenceNumber = 4
    page.replace(snapshot)
    assert adapter.read_tick() is None
    assert adapter.read_observation()[0]["source_epoch"] == 0
    snapshot.mSequenceNumber = 6
    page.replace(snapshot)
    assert adapter.read_tick() is not None


@pytest.mark.parametrize("old,new", [(100, 2), (4294967294, 0)])
def test_ams2_regression_or_possible_wrap_starts_epoch_without_inventing_tick(old, new):
    adapter, page, snapshot = fresh_adapter()
    snapshot.mSequenceNumber = old - 2
    page.replace(snapshot)
    assert adapter.read_tick() is None
    snapshot.mSequenceNumber = old
    page.replace(snapshot)
    assert adapter.read_observation()[1] is not None
    snapshot.mSequenceNumber = new
    page.replace(snapshot)
    assert adapter.read_session_info()["source_epoch"] == 0  # inspection only
    info, tick = adapter.read_observation()
    assert tick is None and info["source_epoch"] == 1
    assert adapter.read_tick() is None
    snapshot.mSequenceNumber = new + 2
    page.replace(snapshot)
    info, tick = adapter.read_observation()
    assert tick is not None and info["source_epoch"] == 1


def test_ams2_reconnect_requires_new_baseline_and_advance(monkeypatch):
    snapshot = make_snapshot()
    pages = []

    def mapping(*args, **kwargs):
        page = SyntheticAMS2Page(bytes(snapshot))
        pages.append(page)
        return page

    monkeypatch.setattr(ams2_adapter.sys, "platform", "win32")
    monkeypatch.setattr(ams2_adapter, "ExistingMapping", mapping)
    adapter = AMS2Adapter()
    adapter.connect()
    assert adapter.read_tick() is None
    snapshot.mSequenceNumber = 4
    pages[-1].replace(snapshot)
    assert adapter.read_observation()[1] is not None
    adapter.disconnect()
    assert pages[-1].releases == 3 and adapter._capture_started_at is None
    adapter.connect()
    info, tick = adapter.read_observation()
    assert info["source_epoch"] == 2 and tick is None
    assert adapter.read_tick() is None
    snapshot.mSequenceNumber = 6
    pages[-1].replace(snapshot)
    assert adapter.read_tick() is not None
    adapter.disconnect()


def test_ams2_missing_metadata_consumes_progress_without_resurrecting_page():
    adapter, page, snapshot = fresh_adapter()
    assert adapter.read_observation()[1] is None
    snapshot.mSequenceNumber = 4
    snapshot.mCarName = b""
    page.replace(snapshot)
    assert adapter.read_observation() == (None, None)
    snapshot.mCarName = b"Formula Inter"
    page.replace(snapshot)
    assert adapter.read_observation()[1] is None
    snapshot.mSequenceNumber = 6
    page.replace(snapshot)
    assert adapter.read_observation()[1] is not None


@pytest.mark.parametrize("case", ["stable", "unstable", "unsupported", "short_copy"])
def test_ams2_snapshot_releases_mapping_and_requires_supported_copy(case):
    snapshot = make_snapshot()
    if case == "unsupported":
        snapshot.mVersion = 15
    raw = bytes(snapshot)
    if case == "short_copy":
        raw = raw[:-1]
    page = SyntheticAMS2Page(None if case == "unstable" else raw)
    adapter = AMS2Adapter()
    adapter._source = page
    result = adapter._read_snapshot()
    assert (result is not None) == (case == "stable")
    assert page.releases == 1

def test_ams2_paired_observation_does_not_bind_tick_without_metadata(monkeypatch):
    snapshot = make_snapshot()
    snapshot.mCarName = b""
    adapter = AMS2Adapter()
    adapter._capture_started_at = 0
    monkeypatch.setattr(adapter, "_read_snapshot", lambda: snapshot)
    assert adapter.read_observation() == (None, None)


def test_pcars2_rest_cars_shared_memory_sample_maps_to_ams2_adapter() -> None:
    sample = json.loads(REST_CARS_FIXTURE.read_text(encoding="utf-8-sig"))
    snapshot = make_rest_cars_snapshot(sample)

    info = AMS2Adapter.session_info_from_snapshot(snapshot)
    assert info is not None
    assert info["sim"] == "ams2"
    assert info["track_raw"] == "Zolder GP"
    assert info["track"] == "zolder_gp"
    assert info["car_raw"] == "Porsche 911 GT3 R Endurance"
    assert info["car"] == "porsche_911_gt3_r_endurance"
    assert info["session_type"] == "practice"
    assert info["track_length_m"] == pytest.approx(4146.73)
    assert info["lap_dist_m_source"] == "sim"
    assert info["validity_method"] == "sim_flag_plus_inferred"
    assert info["lap_invalidated"] is True

    tick = AMS2Adapter.tick_from_snapshot(snapshot, capture_t=1.25)
    assert tick is not None
    assert tick.t == 1.25
    assert tick.lap == 2
    assert tick.lap_dist_m == 0.0
    assert tick.lap_dist_pct == 0.0
    assert tick.speed == pytest.approx(6.49477)
    assert tick.throttle == 0.0
    assert tick.brake == 0.0
    assert tick.steering == pytest.approx(-4.64916e-06)
    assert tick.gear == 1
    assert tick.rpm == pytest.approx(2110.09)
    assert tick.vehicle_state == "paused"
    assert tick.pos_x == pytest.approx(472.099)
    assert tick.pos_y == pytest.approx(179.017)
    assert tick.pos_z == pytest.approx(8.84461)


def test_pcars2_crest_shared_memory_sample_maps_lap_distance_to_ams2_adapter() -> None:
    sample = json.loads(CREST_FIXTURE.read_text(encoding="utf-8-sig"))
    snapshot = make_crest_snapshot(sample)

    assert snapshot.mVersion == 5
    assert snapshot.mBuildVersionNumber == 917
    assert snapshot.mNumParticipants == 18

    info = AMS2Adapter.session_info_from_snapshot(snapshot)
    assert info is not None
    assert info["sim"] == "ams2"
    assert info["track_raw"] == "Azure Circuit Grand Prix"
    assert info["track"] == "azure_circuit_grand_prix"
    assert info["car_raw"] == "Renault Megane R.S. 265"
    assert info["car"] == "renault_megane_r_s_265"
    assert info["session_type"] == "race"
    assert info["track_length_m"] == pytest.approx(3325.76)
    assert info["lap_dist_m_source"] == "sim"
    assert info["validity_method"] == "sim_flag_plus_inferred"
    assert info["lap_invalidated"] is False

    tick = AMS2Adapter.tick_from_snapshot(snapshot, capture_t=2.5)
    assert tick is not None
    assert tick.t == 2.5
    assert tick.lap == 2
    assert tick.lap_dist_m == pytest.approx(400.639)
    assert tick.lap_dist_pct == pytest.approx(400.639 / 3325.76)
    assert tick.speed == pytest.approx(32.9303)
    assert tick.throttle == 1.0
    assert tick.brake == 0.0
    assert tick.steering == pytest.approx(-0.0000142459)
    assert tick.gear == 3
    assert tick.rpm == pytest.approx(5871.44)
    assert tick.vehicle_state == "paused"
    assert tick.pos_x == pytest.approx(197.58)
    assert tick.pos_y == pytest.approx(226.268)
    assert tick.pos_z == pytest.approx(20.4255)


def test_matching_key_normalization() -> None:
    assert normalize_matching_key("  Formula Inter / Gen 2  ") == "formula_inter_gen_2"
    assert normalize_matching_key("!!!") == ""


def test_ac_adapter_remains_deferred_stub_only() -> None:
    adapter_classes = [
        value
        for value in vars(ac_adapter).values()
        if isinstance(value, type) and issubclass(value, SimAdapter) and value is not SimAdapter
    ]

    assert adapter_classes == []


def test_normalized_tick_excludes_v1_deferred_telemetry_fields() -> None:
    field_names = {field.name for field in fields(NormalizedTick)}

    assert field_names.isdisjoint(
        {
            "tire_temps",
            "tyre_temps",
            "slip_angles",
            "slip_ratio",
            "ride_height",
            "suspension_travel",
            "fuel",
            "weather",
        }
    )


def test_acc_struct_key_offsets() -> None:
    assert ctypes.sizeof(ACCPhysics) == 532
    assert ctypes.sizeof(ACCGraphics) == 1532
    assert ctypes.sizeof(ACCStatic) == 580
    assert acc_important_offsets() == {
        "physics": {
            "gas": 4,
            "brake": 8,
            "gear": 16,
            "rpm": 20,
            "speed_kmh": 28,
            "tyre_contact_point": 236,
        },
        "graphics": {
            "status": 4,
            "session_type": 8,
            "completed_laps": 132,
            "normalized_car_position": 248,
            "is_valid_lap": 1408,
        },
        "static": {
            "car_model": 68,
            "track": 134,
        },
    }


def test_acc_snapshots_to_normalized_tick_and_session_info() -> None:
    physics, graphics, static = make_acc_snapshots()
    tick = ACCAdapter.tick_from_snapshots(physics, graphics, capture_t=4.5)

    assert tick is not None
    assert tick.t == 4.5
    assert tick.lap == 3
    assert tick.lap_dist_pct == pytest.approx(0.25)
    assert tick.lap_dist_m is None
    assert tick.speed == pytest.approx(50.0)
    assert tick.throttle == pytest.approx(0.8)
    assert tick.brake == pytest.approx(0.1)
    assert tick.steering == pytest.approx(-0.25)
    assert tick.gear == 3
    assert tick.rpm == 7200
    assert tick.vehicle_state == "running"
    assert tick.pos_x == pytest.approx(11.5)
    assert tick.pos_y == pytest.approx(21.5)
    assert tick.pos_z == pytest.approx(1.0)

    info = ACCAdapter.session_info_from_snapshots(graphics, static)
    assert info is not None
    assert info["sim"] == "acc"
    assert info["track_raw"] == "spa"
    assert info["track"] == "spa"
    assert info["car_raw"] == "ferrari_488_gt3_evo"
    assert info["car"] == "ferrari_488_gt3_evo"
    assert info["session_type"] == "practice"
    assert info["track_length_m"] is None
    assert info["lap_dist_m_source"] == "unavailable"
    assert info["validity_method"] == "sim_flag_plus_inferred"
    assert info["lap_invalidated"] is False


def test_acc_connect_closes_pages_after_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    physics_page = FakePage()

    def open_page(name: str, _struct_type: type[ctypes.Structure]) -> FakePage:
        if name == acc_adapter.PHYSICS_PAGE:
            return physics_page
        raise OSError("mapping unavailable")

    monkeypatch.setattr(acc_adapter.sys, "platform", "win32")
    monkeypatch.setattr(acc_adapter, "_open_page", open_page)
    adapter = ACCAdapter()

    with pytest.raises(OSError, match="mapping unavailable"):
        adapter.connect()

    assert physics_page.closed is True
    assert adapter._physics_mmap is None
    assert adapter._graphics_mmap is None
    assert adapter._static_mmap is None


def make_snapshot() -> SharedMemory:
    snapshot = SharedMemory()
    snapshot.mVersion = SHARED_MEMORY_VERSION
    snapshot.mSequenceNumber = 2
    snapshot.mGameState = GAME_INGAME_PLAYING
    snapshot.mSessionState = SESSION_PRACTICE
    snapshot.mViewedParticipantIndex = 0
    snapshot.mNumParticipants = 1
    snapshot.mTrackLocation = b"Interlagos"
    snapshot.mTrackVariation = b"GP"
    snapshot.mTrackLength = 4309.0
    snapshot.mCarName = b"Formula Inter"
    snapshot.mLapInvalidated = True
    snapshot.mSpeed = 55.0
    snapshot.mRpm = 9000.0
    snapshot.mBrake = 0.2
    snapshot.mThrottle = 0.7
    snapshot.mSteering = -0.1
    snapshot.mGear = 3
    snapshot.mPitMode = PIT_MODE_NONE
    participant = snapshot.mParticipantInfo[0]
    participant.mIsActive = True
    participant.mCurrentLapDistance = 2154.5
    participant.mCurrentLap = 3
    participant.mWorldPosition[0] = 10.0
    participant.mWorldPosition[1] = 20.0
    participant.mWorldPosition[2] = 30.0
    assert len(snapshot.mParticipantInfo) == STORED_PARTICIPANTS_MAX
    return snapshot


def make_rest_cars_snapshot(sample: dict[str, object]) -> SharedMemory:
    snapshot = SharedMemory()
    event = sample["event"]
    car_state = sample["carState"]
    game_states = sample["gameStates"]
    pit_info = sample["pitInfo"]
    timings = sample["timings"]
    participant_source = sample["participiants"]["mParticipiantInfo"][0]

    snapshot.mVersion = SHARED_MEMORY_VERSION
    snapshot.mSequenceNumber = 2
    snapshot.mGameState = game_states["mGameState"]
    snapshot.mSessionState = game_states["mSessionState"]
    snapshot.mRaceState = game_states["mRaceState"]
    snapshot.mViewedParticipantIndex = 0
    snapshot.mNumParticipants = sample["participiants"]["mNumParticipiants"]
    snapshot.mTrackLocation = event["mTrackLocation"].encode("utf-8")
    snapshot.mTrackVariation = event["mTrackVariation"].encode("utf-8")
    snapshot.mTrackLength = event["mTrackLength"]
    snapshot.mCarName = car_state["mCarName"].encode("utf-8")
    snapshot.mCarClassName = car_state["mCarClassName"].encode("utf-8")
    snapshot.mLapInvalidated = timings["mLapInvalidated"]
    snapshot.mSpeed = car_state["mSpeed"]
    snapshot.mRpm = car_state["mRPM"]
    snapshot.mBrake = car_state["mBrake"]
    snapshot.mThrottle = car_state["mThrottle"]
    snapshot.mSteering = car_state["mSteering"]
    snapshot.mGear = car_state["mGear"]
    snapshot.mPitMode = pit_info["mPitMode"]

    participant = snapshot.mParticipantInfo[0]
    participant.mIsActive = participant_source["mIsActive"]
    participant.mName = participant_source["mName"].encode("utf-8")
    participant.mCurrentLap = participant_source["mCurrentLap"]
    participant.mRacePosition = participant_source["mRacePosition"]
    participant.mWorldPosition[0] = participant_source["position"]["x"]
    participant.mWorldPosition[1] = participant_source["position"]["y"]
    participant.mWorldPosition[2] = participant_source["position"]["z"]
    return snapshot


def make_crest_snapshot(sample: dict[str, object]) -> SharedMemory:
    snapshot = SharedMemory()
    buildinfo = sample["buildinfo"]
    game_states = sample["gameStates"]
    participants = sample["participants"]
    vehicle = sample["vehicleInformation"]
    event = sample["eventInformation"]
    timings = sample["timings"]
    pit_info = sample["pitInfo"]
    car_state = sample["carState"]

    snapshot.mVersion = buildinfo["mVersion"]
    snapshot.mBuildVersionNumber = buildinfo["mBuildVersionNumber"]
    snapshot.mGameState = game_states["mGameState"]
    snapshot.mSessionState = game_states["mSessionState"]
    snapshot.mRaceState = game_states["mRaceState"]
    snapshot.mViewedParticipantIndex = 0
    snapshot.mNumParticipants = participants["mNumParticipants"]
    snapshot.mCarName = vehicle["mCarName"].encode("utf-8")
    snapshot.mCarClassName = vehicle["mCarClassName"].encode("utf-8")
    snapshot.mLapsInEvent = event["mLapsInEvent"]
    snapshot.mTrackLocation = event["mTrackLocation"].encode("utf-8")
    snapshot.mTrackVariation = event["mTrackVariation"].encode("utf-8")
    snapshot.mTrackLength = event["mTrackLength"]
    snapshot.mLapInvalidated = timings["mLapInvalidated"]
    snapshot.mPitMode = pit_info["mPitMode"]
    snapshot.mPitSchedule = pit_info["mPitSchedule"]
    snapshot.mCarFlags = car_state["mCarFlags"]
    snapshot.mSpeed = car_state["mSpeed"]
    snapshot.mRpm = car_state["mRpm"]
    snapshot.mBrake = car_state["mBrake"]
    snapshot.mThrottle = car_state["mThrottle"]
    snapshot.mClutch = car_state["mClutch"]
    snapshot.mSteering = car_state["mSteering"]
    snapshot.mGear = car_state["mGear"]
    snapshot.mNumGears = car_state["mNumGears"]

    for index, participant_source in enumerate(participants["mParticipantInfo"]):
        participant = snapshot.mParticipantInfo[index]
        participant.mIsActive = participant_source["mIsActive"]
        participant.mName = participant_source["mName"].encode("utf-8")
        participant.mRacePosition = participant_source["mRacePosition"]
        participant.mLapsCompleted = participant_source["mLapsCompleted"]
        participant.mCurrentLap = participant_source["mCurrentLap"]
        participant.mCurrentSector = participant_source["mCurrentSector"]
        participant.mCurrentLapDistance = participant_source["mCurrentLapDistance"]
        for axis, value in enumerate(participant_source["mWorldPosition"]):
            participant.mWorldPosition[axis] = value

    return snapshot


def make_acc_snapshots() -> tuple[ACCPhysics, ACCGraphics, ACCStatic]:
    physics = ACCPhysics()
    physics.gas = 0.8
    physics.brake = 0.1
    physics.gear = 4
    physics.rpm = 7200
    physics.steer_angle = -0.25
    physics.speed_kmh = 180.0
    for index, point in enumerate(physics.tyre_contact_point):
        point[0] = 10.0 + index
        point[1] = 1.0
        point[2] = 20.0 + index

    graphics = ACCGraphics()
    graphics.status = ACC_LIVE
    graphics.session_type = ACC_PRACTICE
    graphics.completed_laps = 2
    graphics.normalized_car_position = 0.25
    graphics.is_valid_lap = 1

    static = ACCStatic()
    write_utf16(static.track, "spa")
    write_utf16(static.car_model, "ferrari_488_gt3_evo")
    return physics, graphics, static


def write_utf16(target: ctypes.Array[ctypes.c_uint16], value: str) -> None:
    encoded = value.encode("utf-16-le")
    for index in range(0, min(len(encoded), (len(target) - 1) * 2), 2):
        target[index // 2] = int.from_bytes(encoded[index : index + 2], "little")

@pytest.mark.skipif(ams2_adapter.sys.platform != "win32", reason="Windows named mappings")
def test_ams2_starts_empty_and_reopens_new_mapping_without_pinning(monkeypatch):
    import mmap
    from uuid import uuid4

    name = "Local\\ORC.AMS2MappingTest." + uuid4().hex
    monkeypatch.setattr(ams2_adapter, "SHARED_MEMORY_NAME", name)
    adapter = AMS2Adapter()
    adapter.connect()
    try:
        assert adapter.read_observation() == (None, None)
        first = make_snapshot()
        with mmap.mmap(-1, ctypes.sizeof(SharedMemory), tagname=name) as writer:
            writer[:] = bytes(first)
            old_info, tick = adapter.read_observation()
            assert old_info is not None and tick is None
            first.mSequenceNumber = 4
            writer[:] = bytes(first)
            old_tick = adapter.read_observation()[1]
            assert old_tick is not None

        # The adapter is still connected. Its reader must not keep that object alive.
        assert adapter.read_observation() == (None, None)
        assert adapter.read_session_info() is None
        with mmap.mmap(-1, ctypes.sizeof(SharedMemory), tagname=name) as writer:
            first.mParticipantInfo[0].mWorldPosition[0] = 101
            writer[:] = bytes(first)  # The replacement starts at the same even sequence.
            info, tick = adapter.read_observation()
            assert tick is None and info["source_epoch"] == old_info["source_epoch"] + 1
            assert adapter.read_tick() is None
            first.mSequenceNumber = 6
            writer[:] = bytes(first)
            replacement = adapter.read_observation()[1]
            assert replacement is not None and replacement.pos_x == 101
            assert replacement.t > old_tick.t
    finally:
        adapter.disconnect()
