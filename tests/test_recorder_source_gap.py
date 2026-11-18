"""Deterministic source-gap tests; no simulator, raw capture or real sleeping."""

from dataclasses import replace

import pandas as pd
import pytest
import yaml

from simcoach.ingest import recorder as module
from simcoach.ingest.recorder import Recorder, RecorderConfig
from tests.test_recorder import tick
from tests.test_session import session_info


class Clock:
    now = 0.0

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Adapter:
    sim_name = "synthetic_paired"

    def __init__(self, clock, observations):
        self.clock, self.observations = clock, list(observations)
        self.initial = observations[0][0]
        self.connected = self.disconnected = 0
        self.recorder = None
        self.states = []

    def connect(self):
        self.connected += 1

    def disconnect(self):
        self.disconnected += 1

    def read_session_info(self):
        return self.initial

    def read_observation(self):
        self.clock.now += .1
        if self.recorder:
            self.states.append((self.recorder.capture_phase, self.recorder.active_ticks,
                                self.recorder.last_tick_at, self.recorder.intake_active))
        if not self.observations:
            return None, None
        value = self.observations.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def setup(tmp_path, monkeypatch, observations, **fields):
    clock = Clock()
    monkeypatch.setattr(module.time, "monotonic", clock.read)
    monkeypatch.setattr(module.time, "perf_counter", clock.read)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    adapter = Adapter(clock, observations)
    options = dict(metadata_stable_s=0, tick_rate_hz=100,
                   metadata_missing_grace_s=.15, tick_missing_grace_s=.15,
                   continuous_source_gaps=True)
    options.update(fields)
    recorder = Recorder(adapter, RecorderConfig(tmp_path, **options),
                        stop_requested=lambda: not adapter.observations)
    adapter.recorder = recorder
    return recorder, adapter


def frame(path):
    return pd.read_parquet(path/"ticks.parquet")


def complete(path):
    return yaml.safe_load((path/"session.yaml").read_text())["complete"]


def prefix(info):
    return [(info, tick(lap=1, pct=0, t=10)), (info, tick(lap=1, pct=.05, t=10.1))]


@pytest.mark.parametrize("change", [False, True])
def test_gap_resume_allocates_fresh_identity_preserves_original_capture_time(tmp_path,
                                                                           monkeypatch, change):
    old = session_info()
    new = dict(old, car="changed", car_raw="Changed") if change else old
    recorder, adapter = setup(tmp_path, monkeypatch, prefix(old) + [(old, None)]*5 + [
        (new, tick(lap=1, pct=.1, t=30)), (new, tick(lap=1, pct=.15, t=30.1))])
    result = recorder.run()
    first, second = recorder.session_paths
    assert result == second and first != second
    assert frame(first).t.tolist() == [10, 10.1]
    assert frame(second).t.tolist() == [30, 30.1]
    assert complete(first) and complete(second)
    assert recorder.source_gap_count == 1 and recorder.source_gap_reason is None
    assert recorder.ticks_observed == 4 and recorder.active_ticks == 2
    assert adapter.connected == adapter.disconnected == 1
    waiting = [state for state in adapter.states if state[0] == "waiting_for_source"]
    assert waiting and all(state[1:] == (0, None, False) for state in waiting)


def test_missing_metadata_closes_supported_prefix_then_waits_without_empty_churn(tmp_path,
                                                                               monkeypatch):
    info = session_info()
    other = dict(info, car="other", car_raw="Other")
    observations = prefix(info) + [(None, None)]*5 + [(other, None), (info, None)]*4
    recorder, _ = setup(tmp_path, monkeypatch, observations)
    result = recorder.run()
    assert recorder.session_paths == (result,)
    assert complete(result) and frame(result).t.tolist() == [10, 10.1]
    assert recorder.capture_phase == "waiting_for_source"
    assert recorder.source_gap_reason == "metadata_unavailable"
    assert recorder.active_ticks == 0 and recorder.last_tick_at is None
    assert recorder.source_gap_count == 1


def test_metadata_only_context_changes_before_grace_do_not_create_empty_successors(tmp_path,
                                                                                monkeypatch):
    info = session_info()
    other = dict(info, car="other", car_raw="Other")
    recorder, _ = setup(tmp_path, monkeypatch, prefix(info) + [(other, None), (info, None)]*6)
    recorder.run()
    assert len(recorder.session_paths) == 1
    assert recorder.source_gap_count == 1


def test_advancing_paused_and_pit_ticks_remain_one_session(tmp_path, monkeypatch):
    info = session_info()
    values = [replace(tick(lap=1, pct=.1, t=index), vehicle_state=state)
              for index, state in enumerate(["running", "paused", "paused", "pit", "running"])]
    recorder, _ = setup(tmp_path, monkeypatch, [(info, value) for value in values])
    recorder.run()
    assert len(recorder.session_paths) == 1
    assert recorder.source_gap_count == 0
    assert recorder.ticks_observed == len(values)


@pytest.mark.parametrize("termination", ["stop", "timeout"])
def test_wait_stop_or_timeout_returns_prefix_without_refinalization(tmp_path, monkeypatch,
                                                                  termination):
    info = session_info()
    recorder, _ = setup(tmp_path, monkeypatch, prefix(info) + [(info, None)]*10,
                        max_seconds=.8 if termination == "timeout" else None)
    if termination == "timeout":
        recorder.stop_requested = lambda: False
    finalize = module.finalize_recorded_session
    finalized = []

    def spy(*args, **kwargs):
        result = finalize(*args, **kwargs)
        finalized.append({p.name: p.read_bytes() for p in args[0].root.iterdir() if p.is_file()})
        return result

    monkeypatch.setattr(module, "finalize_recorded_session", spy)
    result = recorder.run()
    assert len(finalized) == 1
    assert {p.name: p.read_bytes() for p in result.iterdir() if p.is_file()} == finalized[0]
    assert recorder.capture_phase == "waiting_for_source"
    assert recorder.source_gap_count == 1


def test_late_usable_observation_splits_at_return_time_and_is_not_lost(tmp_path, monkeypatch):
    info = session_info()
    recorder, adapter = setup(tmp_path, monkeypatch, prefix(info) + [
        (info, tick(lap=1, pct=.1, t=30)), (info, tick(lap=1, pct=.15, t=30.1))])
    original = adapter.read_observation
    calls = 0

    def late_return():
        nonlocal calls
        calls += 1
        if calls == 3:
            adapter.clock.now += 3
        return original()

    monkeypatch.setattr(adapter, "read_observation", late_return)
    recorder.run()
    first, second = recorder.session_paths
    assert frame(first).t.tolist() == [10, 10.1]
    assert frame(second).t.tolist() == [30, 30.1]
    assert recorder.source_gap_count == 1 and recorder.ticks_observed == 4


def test_failed_successor_allocation_never_refinalizes_prefix(tmp_path, monkeypatch):
    info = session_info()
    recorder, adapter = setup(tmp_path, monkeypatch, prefix(info) + [(info, None)]*5 + [
        (info, tick(lap=1, pct=.1, t=30))])
    create = module.create_recorded_session
    saved = {}

    def fail_successor(*args):
        if saved:
            saved["bytes"] = {p.name: p.read_bytes() for p in saved["root"].iterdir()}
            raise OSError("synthetic successor failure")
        result = create(*args)
        saved["root"] = result[2].root
        return result

    monkeypatch.setattr(module, "create_recorded_session", fail_successor)
    with pytest.raises(OSError, match="successor failure"):
        recorder.run()
    assert {p.name: p.read_bytes() for p in saved["root"].iterdir()} == saved["bytes"]
    assert complete(saved["root"])
    assert recorder.capture_phase == "failed"
    assert adapter.connected == adapter.disconnected == 1


@pytest.mark.parametrize("failure", ["exception", "invalid_prefix"])
def test_gap_finalization_failure_is_terminal_and_not_retried(tmp_path, monkeypatch, failure):
    info = session_info()
    observations = prefix(info)
    if failure == "invalid_prefix":
        observations[0] = (info, replace(observations[0][1], throttle=1.1))
    recorder, adapter = setup(tmp_path, monkeypatch, observations + [(info, None)]*10)
    finalize = module.finalize_recorded_session
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        if failure == "exception":
            raise OSError("synthetic finalizer failure")
        return finalize(*args, **kwargs)

    monkeypatch.setattr(module, "finalize_recorded_session", fail)
    with pytest.raises((OSError, RuntimeError)):
        recorder.run()
    assert len(calls) == len(recorder.session_paths) == 1
    assert not complete(recorder.session_paths[0])
    assert recorder.capture_phase == "failed"
    assert adapter.disconnected == 1


def test_adapter_failure_during_wait_does_not_reconnect_or_refinalize(tmp_path, monkeypatch):
    info = session_info()
    recorder, adapter = setup(tmp_path, monkeypatch, prefix(info) + [(info, None)]*5 + [
        RuntimeError("synthetic adapter failure")])
    with pytest.raises(RuntimeError, match="adapter failure"):
        recorder.run()
    assert len(recorder.session_paths) == 1 and complete(recorder.session_paths[0])
    assert adapter.connected == adapter.disconnected == 1
    assert recorder.capture_phase == "failed"


@pytest.mark.parametrize("continuous", [False, True])
def test_source_epoch_without_tick_is_pending_until_usable_observation(tmp_path, monkeypatch,
                                                                    continuous):
    info = dict(session_info(), source_epoch=1)
    changed = dict(info, source_epoch=2)
    recorder, _ = setup(tmp_path, monkeypatch, prefix(info) + [(changed, None),
                        (changed, tick(lap=1, pct=.1, t=11))],
                        continuous_source_gaps=continuous, tick_missing_grace_s=5)
    recorder.run()
    first, second = recorder.session_paths
    assert frame(first).t.tolist() == [10, 10.1]
    assert frame(second).t.tolist() == [11]
    assert recorder.source_gap_count == 0


def test_finite_default_still_ends_after_missing_ticks(tmp_path, monkeypatch):
    info = session_info()
    recorder, adapter = setup(tmp_path, monkeypatch, prefix(info) + [(info, None)]*10,
                              continuous_source_gaps=False)
    result = recorder.run()
    assert recorder.session_paths == (result,)
    assert adapter.observations  # Finite policy ended before all scripted wait observations.
    assert complete(result) and recorder.source_gap_count == 0


def test_finite_default_still_errors_after_metadata_loss(tmp_path, monkeypatch):
    info = session_info()
    recorder, _ = setup(tmp_path, monkeypatch, prefix(info) + [(None, None)]*10,
                        continuous_source_gaps=False)
    with pytest.raises(RuntimeError, match="metadata became unavailable"):
        recorder.run()
    assert not complete(recorder.session_paths[0])
