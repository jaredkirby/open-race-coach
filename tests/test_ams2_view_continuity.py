"""Synthetic selected-participant changes; no simulator or actual driver identity."""
import json
import time

import pandas as pd
import pytest
import yaml

from simcoach.adapters.ams2 import AMS2Adapter
from simcoach.ingest.recorder import Recorder, RecorderConfig
from simcoach.validation import validate_recorded_session
from tests.test_adapters import SyntheticAMS2Page, fresh_adapter, make_snapshot


def select(snapshot, index, sequence, *, x=1000):
    snapshot.mNumParticipants = 2
    snapshot.mViewedParticipantIndex = index
    snapshot.mSequenceNumber = sequence
    slot = snapshot.mParticipantInfo[index]
    slot.mIsActive = True
    slot.mCurrentLap = 3  # Neither car/track nor lap-counter change can split this.
    slot.mCurrentLapDistance = 100
    slot.mWorldPosition[0] = x
    return snapshot


@pytest.mark.parametrize('api', ['read_tick', 'read_observation'])
def test_view_switch_separates_epoch_without_discarding_first_fresh_tick(api):
    adapter, page, snapshot = fresh_adapter()
    assert adapter.read_observation()[1] is None
    snapshot.mSequenceNumber = 4
    page.replace(snapshot)
    assert adapter.read_observation()[1] is not None
    page.replace(select(snapshot, 1, 6))
    assert adapter.read_session_info()['source_epoch'] == 0  # Inspection only.
    value = getattr(adapter, api)()
    tick = value[1] if api == 'read_observation' else value
    assert tick is not None and tick.pos_x == 1000
    assert adapter.read_session_info()['source_epoch'] == 1
    assert adapter.read_observation()[1] is None  # No duplicate tick.
    page.replace(select(snapshot, 0, 8, x=10))
    info, tick = adapter.read_observation()
    assert info['source_epoch'] == 2 and tick.pos_x == 10


def test_view_switch_keeps_three_separate_recordings(tmp_path):
    pages = []
    for sequence, view, x, invalid in (
        (2, 0, 10, False), (4, 0, 10, False), (6, 0, 10, False),
        (8, 1, 1000, True), (10, 1, 1000, True),
        (12, 0, 20, False), (14, 0, 20, False),
    ):
        page = select(make_snapshot(), view, sequence, x=x)
        page.mLapInvalidated = invalid
        pages.append(page)

    class SyntheticAdapter(AMS2Adapter):
        index = 0

        def connect(self):
            self._source = SyntheticAMS2Page(bytes(pages[0]))
            self._capture_started_at = time.perf_counter()

        def read_observation(self):
            self._source.replace(pages[self.index])
            self.index += 1
            return super().read_observation()

    adapter = SyntheticAdapter()
    recorder = Recorder(adapter, RecorderConfig(tmp_path / 'sessions', metadata_stable_s=0,
                        tick_rate_hz=1000, continuous_source_gaps=True),
                        stop_requested=lambda: adapter.index == len(pages))
    recorder.run()
    (tmp_path / 'provenance.json').write_text(json.dumps(dict(
        source='synthetic source pages through actual stable-copy reader and recorder',
        live_validation='NOT RUN', actual_driver_identity='NOT ESTABLISHED')))
    assert len(recorder.session_paths) == 3
    assert recorder.ticks_observed == 6
    ends = []
    for path, x, invalid in zip(
        recorder.session_paths, (10, 1000, 20), (False, True, False), strict=True,
    ):
        metadata = yaml.safe_load((path / 'session.yaml').read_text())
        assert metadata['complete'] is True
        validate_recorded_session(path)
        frame = pd.read_parquet(path / 'ticks.parquet')
        assert frame.pos_x.tolist() == [x, x]
        assert frame.lap.tolist() == [4, 4]
        assert frame.t.is_monotonic_increasing
        ends.append((frame.t.min(), frame.t.max()))
        laps = [json.loads(row) for row in (path / 'laps.jsonl').read_text().splitlines()]
        assert all((lap['invalid_reason'] == 'sim_invalidated') is invalid for lap in laps)
    assert ends[0][1] < ends[1][0] < ends[1][1] < ends[2][0]


@pytest.mark.parametrize('interruption', ['same_sequence', 'inactive', 'out_of_range'])
def test_unusable_view_does_not_erase_previous_view(interruption):
    adapter, page, snapshot = fresh_adapter()
    adapter.read_observation()
    snapshot.mSequenceNumber = 4
    page.replace(snapshot)
    assert adapter.read_tick() is not None
    select(snapshot, 1, 4 if interruption == 'same_sequence' else 6)
    if interruption == 'inactive':
        snapshot.mParticipantInfo[1].mIsActive = False
    elif interruption == 'out_of_range':
        snapshot.mViewedParticipantIndex = 64
    page.replace(snapshot)
    assert adapter.read_observation()[1] is None
    assert adapter.read_session_info()['source_epoch'] == 0
    page.replace(select(snapshot, 1, 8))
    info, tick = adapter.read_observation()
    assert info['source_epoch'] == 1 and tick is not None


def test_participant_count_change_keeps_same_view_history():
    adapter, page, snapshot = fresh_adapter()
    adapter.read_observation()
    for count, sequence in ((2, 4), (1, 6), (3, 8)):
        snapshot.mNumParticipants, snapshot.mSequenceNumber = count, sequence
        page.replace(snapshot)
        info, tick = adapter.read_observation()
        assert info['source_epoch'] == 0 and tick is not None


def test_sequence_reset_and_view_switch_share_new_baseline():
    adapter, page, snapshot = fresh_adapter()
    adapter.read_observation()
    snapshot.mSequenceNumber = 100
    page.replace(snapshot)
    assert adapter.read_tick() is not None
    page.replace(select(snapshot, 1, 2))
    info, tick = adapter.read_observation()
    assert info['source_epoch'] == 1 and tick is None
    page.replace(select(snapshot, 1, 4))
    info, tick = adapter.read_observation()
    assert info['source_epoch'] == 1 and tick.pos_x == 1000
