"""Synthetic worker integration; never opens a simulator mapping or declares presence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import yaml

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.visit_outcome import build_visit_outcome
from tests.test_adapters import make_snapshot


def test_live_worker_policy_preserves_gap_and_resumes_in_same_owner(tmp_path, monkeypatch):
    from simcoach.adapters import ams2
    from simcoach.ingest import recorder
    from simcoach.live import visit_worker
    from simcoach.utils import llm_logger

    monkeypatch.setattr(llm_logger, "configure_logging", lambda *args, **kwargs: None)
    elapsed = [0.0]

    def advance(seconds):
        elapsed[0] += seconds
        time.sleep(0.0001)  # Let the worker poll its real cooperative stop/lease.

    monotonic = SimpleNamespace(perf_counter=lambda: elapsed[0],
                                monotonic=lambda: elapsed[0], sleep=advance)
    monkeypatch.setattr(recorder, "time", monotonic)
    monkeypatch.setattr(ams2, "time", monotonic)
    run = tmp_path / "worker"
    run.mkdir()
    # This is a synthetic stimulus of the live policy, not real presence or AMS2 evidence.
    write_status(tmp_path / "heartbeat.json", dict(schema_version=1, at=time.time()))
    original_config = recorder.RecorderConfig
    configs = []

    def fast_config(**kwargs):
        configs.append(kwargs.copy())
        return original_config(**(kwargs | dict(metadata_stable_s=0, tick_rate_hz=500,
                                                tick_missing_grace_s=0.05,
                                                metadata_missing_grace_s=0.05)))

    monkeypatch.setattr(recorder, "RecorderConfig", fast_config)
    adapters = []

    class SyntheticAMS2(ams2.AMS2Adapter):
        def connect(self):
            adapters.append(self)
            self._capture_started_at = 0.0

        def disconnect(self):
            self.disconnected = True

        def _read_snapshot(self):
            source_elapsed = elapsed[0]
            snapshot = make_snapshot()
            if source_elapsed < 0.25:
                sequence = 2 + 2 * int(source_elapsed * 100)
            elif source_elapsed < 0.55:
                sequence = 50
            else:
                sequence = 52 + 2 * int((source_elapsed - 0.55) * 100)
            snapshot.mSequenceNumber = sequence
            snapshot.mParticipantInfo[0].mCurrentLapDistance += sequence
            if source_elapsed >= 0.7:
                (tmp_path / "stop").touch()
            return snapshot

    monkeypatch.setattr(ams2, "AMS2Adapter", SyntheticAMS2)
    assert visit_worker.work(run, tmp_path / "locks") == 0
    assert len(adapters) == 1 and adapters[0].disconnected
    assert configs[0]["continuous_source_gaps"] is True
    status = json.loads((run / "status.json").read_text())
    assert status["status"] == "complete"
    assert status["source_gap_count"] == 1
    assert status["stop_reason"] == "visit_stop_requested"
    assert status["intake_active"] is False
    closure = json.loads((run / "closure.json").read_text())
    assert len(closure["recordings"]) == 2
    frames = []
    for row in closure["recordings"]:
        assert row["validation"] == row["analysis"] == "PASS"
        frames.append(pd.read_parquet(visit_worker.local_path(
            Path(row["path"]) / "ticks.parquet")))
    assert frames[0].t.max() < 0.4
    assert frames[1].t.min() >= 0.55
    assert frames[0].t.max() < frames[1].t.min()
    assert all(frame.t.is_monotonic_increasing for frame in frames)
    # Report this test as replay; the mode flag cannot turn injected snapshots into live proof.
    write_status(tmp_path / "status.json", dict(schema_version=1, mode="replay",
                                                status="incomplete", worker_exit=0))
    outcome = build_visit_outcome(tmp_path)
    assert outcome["worker_capture"]["source_gap_count"] == 1
    assert outcome["unmeasured"]["capture_coverage"] is None


def test_legacy_worker_outcome_does_not_invent_zero_source_gaps(tmp_path):
    (tmp_path / "worker").mkdir()
    write_status(tmp_path / "worker/status.json", dict(schema_version=1, status="complete",
                                                       sessions=[]))
    outcome = build_visit_outcome(tmp_path)
    assert outcome["worker_capture"]["source_gap_count"] is None


def test_worker_preserves_predeparture_and_mode_changes(tmp_path, monkeypatch):
    """Real adapter normalization/recorder/closure with synthetic source pages."""
    from simcoach.adapters import ams2
    from simcoach.ingest import recorder
    from simcoach.live import visit_worker
    from simcoach.utils import llm_logger

    monkeypatch.setattr(llm_logger, "configure_logging", lambda *args, **kwargs: None)
    elapsed = [0.0]

    def advance(seconds):
        elapsed[0] += seconds
        time.sleep(0.0001)

    clock = SimpleNamespace(perf_counter=lambda: elapsed[0],
                            monotonic=lambda: elapsed[0], sleep=advance)
    monkeypatch.setattr(recorder, "time", clock)
    monkeypatch.setattr(ams2, "time", clock)
    config = recorder.RecorderConfig
    monkeypatch.setattr(recorder, "RecorderConfig", lambda **kw: config(
        **(kw | dict(metadata_stable_s=0, tick_rate_hz=100))))
    phases = [(1, 4, 1), (1, 2, 2), (5, 2, 1), (5, 2, 2), (6, 2, 2), (6, 6, 2)]
    adapters = []

    class SyntheticAMS2(ams2.AMS2Adapter):
        def connect(self):
            adapters.append(self)
            self._capture_started_at = 0.0

        def disconnect(self):
            self.disconnected = True

        def _read_snapshot(self):
            snapshot = make_snapshot()
            phase = min(int(elapsed[0] / .25), len(phases) - 1)
            snapshot.mSessionState, snapshot.mGameState, snapshot.mRaceState = phases[phase]
            snapshot.mSequenceNumber = 2 + 2 * int(elapsed[0] * 100)
            snapshot.mSpeed = 0  # No departure needed to retain the source.
            if elapsed[0] >= 1.5:
                (tmp_path / "stop").touch()
            return snapshot

    monkeypatch.setattr(ams2, "AMS2Adapter", SyntheticAMS2)
    write_status(tmp_path / "heartbeat.json", dict(schema_version=1, at=time.time()))
    run = tmp_path / "worker"
    run.mkdir()
    assert visit_worker.work(run, tmp_path / "locks") == 0
    assert len(adapters) == 1 and adapters[0].disconnected
    closure = json.loads((run / "closure.json").read_text())
    assert len(closure["recordings"]) == 3
    observed = []
    for row in closure["recordings"]:
        assert row["validation"] == row["analysis"] == "PASS"
        path = visit_worker.local_path(Path(row["path"]))
        metadata = yaml.safe_load((path / "session.yaml").read_text())
        frame = pd.read_parquet(path / "ticks.parquet")
        observed.append((metadata["session_type"], set(frame.vehicle_state)))
        assert (frame.speed == 0).all()
        assert (frame.t.diff().dropna() > 0).all()
        laps = [json.loads(line) for line in (path / "laps.jsonl").read_text().splitlines()]
        assert all(not lap["valid"] for lap in laps)
    assert observed == [("practice", {"menu", "running"}), ("race", {"running"}),
                        ("hotlap", {"running", "replay"})]
