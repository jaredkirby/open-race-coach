from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import yaml

from simcoach.adapters.base import NormalizedTick, SessionInfo, SimAdapter
from simcoach.ingest.recorder import Recorder, RecorderConfig, session_boundary_reason
from tests.test_session import session_info


def test_cooperative_stop_finalizes_and_exposes_exact_inventory(tmp_path):
    info = session_info()
    adapter = FakeAdapter(session_infos=[info] * 5,
                          ticks=[tick(lap=1, pct=0, t=0), tick(lap=1, pct=0.1, t=1)])
    recorder = Recorder(adapter, RecorderConfig(out_dir=tmp_path, metadata_stable_s=0,
                                                tick_rate_hz=1000))
    recorder.stop_requested = lambda: recorder.ticks_observed >= 2
    result = recorder.run()
    assert recorder.session_paths == (result,)
    assert recorder.active_ticks == recorder.ticks_observed == 2
    assert recorder.last_tick_at is not None
    assert yaml.safe_load((result / "session.yaml").read_text())["complete"] is True
    assert adapter.disconnected


def test_cooperative_stop_before_metadata_does_not_create_capture(tmp_path):
    adapter = FakeAdapter(session_infos=[], ticks=[])
    recorder = Recorder(adapter, RecorderConfig(out_dir=tmp_path), stop_requested=lambda: True)
    with pytest.raises(RuntimeError, match="before stable metadata"):
        recorder.run()
    assert recorder.session_paths == ()
    assert adapter.disconnected


def test_failed_successor_creation_cannot_rewrite_finalized_predecessor(tmp_path, monkeypatch):
    from simcoach.ingest import recorder as module

    info = session_info()
    other = dict(info, car="different", car_raw="Different")
    adapter = FakeAdapter(session_infos=[info, info, info, other],
                          ticks=[tick(lap=1, pct=0, t=0)])
    original = module.create_recorded_session
    captured = {}

    def fail_successor(*args):
        if captured:
            root = captured["root"]
            captured["bytes"] = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
            raise OSError("injected successor creation failure")
        created = original(*args)
        captured["root"] = created[2].root
        return created

    monkeypatch.setattr(module, "create_recorded_session", fail_successor)
    with pytest.raises(OSError, match="successor creation"):
        Recorder(adapter, RecorderConfig(out_dir=tmp_path, metadata_stable_s=0,
                                         tick_rate_hz=1000)).run()
    after = {p.name: p.read_bytes() for p in captured["root"].iterdir() if p.is_file()}
    assert after == captured["bytes"]
    assert yaml.safe_load((captured["root"] / "session.yaml").read_text())["complete"] is True


def test_recorder_starts_new_session_after_metadata_boundary(tmp_path: Path) -> None:
    info_a = session_info()
    info_b = {**info_a, "car_raw": "Formula Trainer", "car": "formula_trainer"}
    adapter = FakeAdapter(
        session_infos=[info_a, info_a, info_a, info_b, info_b, info_b],
        ticks=[
            tick(lap=1, pct=0.0, t=0.0),
            tick(lap=1, pct=0.1, t=0.1),
            KeyboardInterrupt(),
        ],
    )

    result = Recorder(
        adapter,
        RecorderConfig(out_dir=tmp_path, metadata_stable_s=0.0, tick_rate_hz=1000),
    ).run()

    session_dirs = sorted(path for path in tmp_path.iterdir() if path.is_dir())
    assert len(session_dirs) == 2
    assert result == session_dirs[-1]
    first = yaml.safe_load((session_dirs[0] / "session.yaml").read_text())
    second = yaml.safe_load((session_dirs[1] / "session.yaml").read_text())
    assert first["car"] == "formula_inter"
    assert second["car"] == "formula_trainer"
    assert first["complete"] is True
    assert second["complete"] is True
    assert adapter.disconnected is True


def test_session_boundary_reason_names_first_changed_boundary_field() -> None:
    info = session_info()

    assert session_boundary_reason(info, {**info, "track": "brands_hatch"}) == "track_changed"
    assert (
        session_boundary_reason(info, {**info, "adapter_version": "0.2.0"})
        == "adapter_version_changed"
    )
    assert session_boundary_reason(info, {**info, "track_raw": "Display-only label"}) is None


def test_recorder_finalizes_current_session_before_metadata_loss_error(
    tmp_path: Path,
) -> None:
    info = session_info()
    adapter = FakeAdapter(
        session_infos=[info, info, info, None],
        ticks=[tick(lap=1, pct=0.0, t=0.0)],
    )

    with pytest.raises(RuntimeError, match="required metadata became unavailable"):
        Recorder(
            adapter,
            RecorderConfig(
                out_dir=tmp_path,
                metadata_stable_s=0.0,
                metadata_missing_grace_s=0.0,
                tick_rate_hz=1000,
            ),
        ).run()

    session_dirs = sorted(path for path in tmp_path.iterdir() if path.is_dir())
    assert len(session_dirs) == 1
    final_yaml = yaml.safe_load((session_dirs[0] / "session.yaml").read_text())
    assert final_yaml["complete"] is False
    assert final_yaml["failure_reason"] == "metadata_unavailable"
    assert (session_dirs[0] / "ticks.parquet").exists()
    assert (session_dirs[0] / "laps.jsonl").exists()
    assert adapter.disconnected is True


def test_recorder_finalizes_complete_session_when_tick_stream_ends(tmp_path: Path) -> None:
    info = session_info()
    adapter = FakeAdapter(
        session_infos=[info] * 23,
        ticks=[tick(lap=1, pct=index / 20, t=index / 60) for index in range(20)],
    )

    session_dir = Recorder(
        adapter,
        RecorderConfig(
            out_dir=tmp_path,
            metadata_stable_s=0.0,
            tick_missing_grace_s=0.0,
            tick_rate_hz=1000,
        ),
    ).run()

    final_yaml = yaml.safe_load((session_dir / "session.yaml").read_text())
    assert final_yaml["complete"] is True
    assert final_yaml["failure_reason"] is None
    assert (session_dir / "ticks.parquet").exists()
    assert (session_dir / "laps.jsonl").exists()
    laps = [json.loads(line) for line in (session_dir / "laps.jsonl").read_text().splitlines()]
    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "metadata_boundary"
    assert adapter.disconnected is True


def test_recorder_marks_sim_invalidated_lap(tmp_path: Path) -> None:
    info: SessionInfo = {
        **session_info(),
        "validity_method": "sim_flag_plus_inferred",
        "lap_invalidated": False,
    }
    invalid_info: SessionInfo = {**info, "lap_invalidated": True}
    adapter = FakeAdapter(
        session_infos=[info, info, *([invalid_info] * 21)],
        ticks=[
            *(tick(lap=1, pct=index / 20, t=index / 60) for index in range(20)),
            KeyboardInterrupt(),
        ],
    )

    session_dir = Recorder(
        adapter,
        RecorderConfig(out_dir=tmp_path, metadata_stable_s=0.0, tick_rate_hz=1000),
    ).run()

    laps = [
        json.loads(line)
        for line in (session_dir / "laps.jsonl").read_text().splitlines()
        if line.strip()
    ]
    final_yaml = yaml.safe_load((session_dir / "session.yaml").read_text())
    assert final_yaml["validity_method"] == "sim_flag_plus_inferred"
    assert laps[0]["valid"] is False
    assert laps[0]["invalid_reason"] == "sim_invalidated"


def test_recorder_prints_provisional_lap_time_on_lap_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    info = session_info()
    adapter = FakeAdapter(
        session_infos=[info] * 6,
        ticks=[
            tick(lap=1, pct=0.0, t=0.0),
            tick(lap=1, pct=0.9, t=1.0),
            tick(lap=2, pct=0.0, t=1.2),
            KeyboardInterrupt(),
        ],
    )

    Recorder(
        adapter,
        RecorderConfig(out_dir=tmp_path, metadata_stable_s=0.0, tick_rate_hz=1000),
    ).run()

    output = capsys.readouterr().out
    assert "lap 1 provisional_time=1.200s" in output


def test_recorder_stops_after_boundary_finalization_failure(tmp_path: Path) -> None:
    info_a = session_info()
    info_b = {**info_a, "car_raw": "Formula Trainer", "car": "formula_trainer"}
    invalid_tick_values = tick(lap=1, pct=0.0, t=0.0).as_record()
    invalid_tick_values["throttle"] = 1.1
    adapter = FakeAdapter(
        session_infos=[info_a, info_a, info_a, info_b],
        ticks=[NormalizedTick(**invalid_tick_values)],
    )

    with pytest.raises(RuntimeError, match="Recorded Session Boundary finalization failed"):
        Recorder(
            adapter,
            RecorderConfig(out_dir=tmp_path, metadata_stable_s=0.0, tick_rate_hz=1000),
        ).run()

    session_dirs = list(tmp_path.iterdir())
    assert len(session_dirs) == 1
    final_yaml = yaml.safe_load((session_dirs[0] / "session.yaml").read_text())
    assert final_yaml["failure_reason"] == "session_boundary_finalization_failed"
    assert adapter.disconnected is True


def test_recorder_marks_unexpected_capture_error_incomplete(tmp_path: Path) -> None:
    info = session_info()
    adapter = FakeAdapter(
        session_infos=[info] * 4,
        ticks=[tick(lap=1, pct=0.0, t=0.0), RuntimeError("tick read failed")],
    )

    with pytest.raises(RuntimeError, match="tick read failed"):
        Recorder(
            adapter,
            RecorderConfig(out_dir=tmp_path, metadata_stable_s=0.0, tick_rate_hz=1000),
        ).run()

    session_dir = next(tmp_path.iterdir())
    final_yaml = yaml.safe_load((session_dir / "session.yaml").read_text())
    assert final_yaml["complete"] is False
    assert final_yaml["failure_reason"] == "unknown_failure"


def test_disconnect_error_does_not_preempt_finalization(tmp_path: Path) -> None:
    info = session_info()
    adapter = FakeAdapter(
        session_infos=[info] * 4,
        ticks=[tick(lap=1, pct=0.0, t=0.0), KeyboardInterrupt()],
        disconnect_error=RuntimeError("disconnect failed"),
    )

    with pytest.raises(RuntimeError, match="disconnect failed"):
        Recorder(
            adapter,
            RecorderConfig(out_dir=tmp_path, metadata_stable_s=0.0, tick_rate_hz=1000),
        ).run()

    session_dir = next(tmp_path.iterdir())
    final_yaml = yaml.safe_load((session_dir / "session.yaml").read_text())
    assert final_yaml["complete"] is True
    assert final_yaml["ended_at"] is not None


def test_recorder_cleans_up_after_connect_failure(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        session_infos=[],
        ticks=[],
        connect_error=RuntimeError("connect failed"),
    )

    with pytest.raises(RuntimeError, match="connect failed"):
        Recorder(adapter, RecorderConfig(out_dir=tmp_path)).run()

    assert adapter.disconnected is True


class PairedAdapter(SimAdapter):
    sim_name = "fake_paired"
    adapter_version = "test"

    def __init__(self, observations):
        self.initial = observations[0][0]
        self.observations = list(observations)
        self.disconnected = False

    def connect(self):
        pass

    def disconnect(self):
        self.disconnected = True

    def read_session_info(self):
        return self.initial

    def read_tick(self):
        raise AssertionError("Recorder must consume the paired observation")

    def read_observation(self):
        return self.observations.pop(0)


def paired_recording(tmp_path, observations):
    adapter = PairedAdapter(observations)
    recorder = Recorder(adapter, RecorderConfig(out_dir=tmp_path, metadata_stable_s=0,
                                                tick_rate_hz=1000),
                        stop_requested=lambda: not adapter.observations)
    recorder.run()
    return recorder


def test_paired_lap_flags_cannot_invalidate_adjacent_lap(tmp_path):
    info = session_info()
    recorder = paired_recording(tmp_path, [
        (dict(info, lap_invalidated=True), tick(lap=2, pct=.9, t=0)),
        (dict(info, lap_invalidated=True), tick(lap=2, pct=.95, t=.1)),
        (dict(info, lap_invalidated=False), tick(lap=3, pct=0, t=1)),
        (dict(info, lap_invalidated=False), tick(lap=3, pct=.05, t=2)),
    ])
    assert len(recorder.session_paths) == 1
    laps = [json.loads(line) for line in
            (recorder.session_paths[0] / "laps.jsonl").read_text().splitlines()]
    assert laps[0]["invalid_reason"] == "sim_invalidated"
    assert laps[1]["invalid_reason"] != "sim_invalidated"


def test_paired_context_boundary_preserves_incoming_tick_in_successor(tmp_path):
    old = session_info()
    new = dict(old, car="different_car", car_raw="Different Car")
    recorder = paired_recording(tmp_path, [(old, tick(lap=2, pct=.9, t=0)),
                                          (new, tick(lap=1, pct=.1, t=1))])
    assert len(recorder.session_paths) == 2 and recorder.ticks_observed == 2
    first, second = recorder.session_paths
    assert pd.read_parquet(first / "ticks.parquet")["t"].tolist() == [0]
    assert pd.read_parquet(second / "ticks.parquet")["t"].tolist() == [1]
    assert yaml.safe_load((second / "session.yaml").read_text())["car"] == "different_car"


def test_counter_regression_preserves_prefix_and_incoming_tick_with_new_identity(tmp_path,
                                                                              monkeypatch):
    from simcoach.ingest import recorder as module

    captured = {}
    original = module.create_recorded_session

    def create(*args):
        if captured:
            captured["bytes"] = {p.name: p.read_bytes() for p in captured["path"].iterdir()}
        created = original(*args)
        captured.setdefault("path", created[2].root)
        return created

    monkeypatch.setattr(module, "create_recorded_session", create)
    info = session_info()
    recorder = paired_recording(tmp_path, [
        (dict(info, lap_invalidated=True), tick(lap=1, pct=.8, t=9)),
        (dict(info, lap_invalidated=True), tick(lap=1, pct=.85, t=9.1)),
        (dict(info, lap_invalidated=True), tick(lap=3, pct=.8, t=10)),
        (dict(info, lap_invalidated=True), tick(lap=3, pct=.85, t=10.1)),
        (dict(info, lap_invalidated=False), tick(lap=1, pct=0, t=11)),
        (dict(info, lap_invalidated=False), tick(lap=1, pct=.05, t=12)),
    ])
    first, second = recorder.session_paths
    assert {p.name: p.read_bytes() for p in first.iterdir()} == captured["bytes"]
    old_yaml, new_yaml = [yaml.safe_load((p / "session.yaml").read_text())
                         for p in (first, second)]
    assert old_yaml["complete"] and new_yaml["complete"]
    assert old_yaml["recorded_session_id"] != new_yaml["recorded_session_id"]
    old_ticks, new_ticks = [pd.read_parquet(p / "ticks.parquet") for p in (first, second)]
    assert old_ticks["lap"].tolist() == [1, 1, 3, 3]
    assert old_ticks["t"].tolist() == [9, 9.1, 10, 10.1]
    assert new_ticks["lap"].tolist() == [1, 1] and new_ticks["t"].tolist() == [11, 12]
    old_laps = [json.loads(line) for line in (first / "laps.jsonl").read_text().splitlines()]
    new_lap = json.loads((second / "laps.jsonl").read_text())
    assert all(lap["invalid_reason"] == "sim_invalidated" for lap in old_laps)
    assert new_lap["invalid_reason"] != "sim_invalidated"
    assert recorder.active_ticks == 2 and recorder.ticks_observed == 6


def test_missing_metadata_pair_is_not_appended_under_stale_context(tmp_path):
    info = session_info()
    recorder = paired_recording(tmp_path, [
        (info, tick(lap=1, pct=0, t=0)),
        (None, tick(lap=99, pct=.2, t=1)),
        (info, tick(lap=1, pct=.1, t=2)),
    ])
    assert len(recorder.session_paths) == 1 and recorder.ticks_observed == 2
    frame = pd.read_parquet(recorder.session_paths[0] / "ticks.parquet")
    assert frame["t"].tolist() == [0, 2]


def test_counter_boundary_successor_failure_preserves_closed_prefix(tmp_path, monkeypatch):
    from simcoach.ingest import recorder as module

    original = module.create_recorded_session
    saved = {}

    def create(*args):
        if saved:
            saved["bytes"] = {p.name: p.read_bytes() for p in saved["path"].iterdir()}
            raise OSError("counter successor failure")
        value = original(*args)
        saved["path"] = value[2].root
        return value

    monkeypatch.setattr(module, "create_recorded_session", create)
    info = session_info()
    with pytest.raises(OSError, match="counter successor failure"):
        paired_recording(tmp_path, [(info, tick(lap=3, pct=.8, t=0)),
                                    (info, tick(lap=1, pct=.1, t=1))])
    assert {p.name: p.read_bytes() for p in saved["path"].iterdir()} == saved["bytes"]
    assert yaml.safe_load((saved["path"] / "session.yaml").read_text())["complete"]


def test_counter_boundary_finalization_failure_does_not_allocate_successor(tmp_path):
    info = session_info()
    invalid = replace(tick(lap=3, pct=.8, t=0), throttle=1.1)
    with pytest.raises(RuntimeError, match="Boundary finalization failed"):
        paired_recording(tmp_path, [(info, invalid), (info, tick(lap=1, pct=.1, t=1))])
    paths = list(tmp_path.iterdir())
    assert len(paths) == 1
    data = yaml.safe_load((paths[0] / "session.yaml").read_text())
    assert not data["complete"] and data["failure_reason"] == "session_boundary_finalization_failed"


def test_pause_pit_and_same_lap_distance_reversal_are_not_restart_claims(tmp_path):
    info = session_info()
    values = [tick(lap=1, pct=.8, t=0),
              replace(tick(lap=1, pct=.8, t=1), vehicle_state="paused"),
              replace(tick(lap=1, pct=.2, t=2), vehicle_state="pit"),
              tick(lap=2, pct=0, t=3)]
    recorder = paired_recording(tmp_path, [(info, value) for value in values])
    assert len(recorder.session_paths) == 1 and recorder.ticks_observed == 4


class FakeAdapter(SimAdapter):
    sim_name = "fake"
    adapter_version = "test"

    def __init__(
        self,
        *,
        session_infos: list[SessionInfo | None],
        ticks: list[NormalizedTick | BaseException],
        connect_error: Exception | None = None,
        disconnect_error: Exception | None = None,
    ) -> None:
        self.session_infos = session_infos
        self.ticks = ticks
        self.disconnected = False
        self.connect_error = connect_error
        self.disconnect_error = disconnect_error

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def read_session_info(self) -> SessionInfo | None:
        if self.session_infos:
            return self.session_infos.pop(0)
        return None

    def read_tick(self) -> NormalizedTick | None:
        if not self.ticks:
            return None
        item = self.ticks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def disconnect(self) -> None:
        self.disconnected = True
        if self.disconnect_error is not None:
            raise self.disconnect_error


def tick(*, lap: int, pct: float, t: float) -> NormalizedTick:
    return NormalizedTick(
        t=t,
        lap=lap,
        lap_dist_pct=pct,
        lap_dist_m=pct * 4309.0,
        speed=40.0,
        throttle=0.5,
        brake=0.0,
        steering=0.0,
        gear=3,
        rpm=8000.0,
        vehicle_state="running",
        pos_x=pct * 100.0,
        pos_y=0.0,
        pos_z=0.0,
    )
