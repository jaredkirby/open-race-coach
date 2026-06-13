from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simcoach.adapters.base import NormalizedTick, SessionInfo, SimAdapter
from simcoach.ingest.recorder import Recorder, RecorderConfig, session_boundary_reason
from tests.test_session import session_info


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
        session_infos=[info, info, info, info, info],
        ticks=[
            tick(lap=1, pct=0.0, t=0.0),
            tick(lap=1, pct=0.5, t=0.5),
            tick(lap=1, pct=0.95, t=1.0),
        ],
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
    assert adapter.disconnected is True


def test_recorder_marks_sim_invalidated_lap(tmp_path: Path) -> None:
    info: SessionInfo = {
        **session_info(),
        "validity_method": "sim_flag_plus_inferred",
        "lap_invalidated": False,
    }
    invalid_info: SessionInfo = {**info, "lap_invalidated": True}
    adapter = FakeAdapter(
        session_infos=[info, info, *([invalid_info] * 20)],
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
        session_infos=[info, info, info, info],
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


class FakeAdapter(SimAdapter):
    sim_name = "fake"
    adapter_version = "test"

    def __init__(
        self,
        *,
        session_infos: list[SessionInfo | None],
        ticks: list[NormalizedTick | BaseException],
    ) -> None:
        self.session_infos = session_infos
        self.ticks = ticks
        self.disconnected = False

    def connect(self) -> None:
        pass

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
