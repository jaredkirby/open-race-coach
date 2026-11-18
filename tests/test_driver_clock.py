import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simcoach.adapters.ams2 import (
    GAME_INGAME_PAUSED,
    GAME_INGAME_PLAYING,
    SHARED_MEMORY_VERSION,
    SharedMemory,
)
from simcoach.cli.driver_clock import activity, interval_activity, save_output, track


@pytest.mark.parametrize(
    ("game", "pit", "speed", "stale", "expected"),
    [
        (GAME_INGAME_PLAYING, 0, 25, False, "driving"),
        (GAME_INGAME_PLAYING, 3, 15, False, "pit_moving"),
        (GAME_INGAME_PLAYING, 0, 0, False, "not_driving"),
        (GAME_INGAME_PAUSED, 0, 25, False, "not_driving"),
        (GAME_INGAME_PLAYING, 0, 25, True, "unknown"),
        (GAME_INGAME_PLAYING, 0, float("nan"), False, "unknown"),
    ],
)
def test_classify_ams2_activity(game, pit, speed, stale, expected):
    snapshot = SharedMemory()
    snapshot.mVersion = SHARED_MEMORY_VERSION
    snapshot.mGameState = game
    snapshot.mPitMode = pit
    snapshot.mSpeed = speed
    assert activity(snapshot, stale=stale) == expected


def test_count_gaps_as_unknown():
    assert interval_activity(5, "driving", "driving") == "unknown"
    assert interval_activity(0.2, "driving", "unknown") == "unknown"
    assert interval_activity(0.2, "driving", "not_driving") == "transition"
    assert interval_activity(0.2, "driving", "driving") == "driving"


def test_preserve_failed_output_when_source_is_overwritten(tmp_path):
    source = tmp_path / "output.md"
    source.write_text("Bad result.\n", encoding="utf-8")
    first = save_output(tmp_path, source, "fail", "Incorrect lap")
    source.write_text("Improved result.\n", encoding="utf-8")
    second = save_output(tmp_path, source, "pass", "")
    assert first != second
    assert "Bad result." in first.read_text(encoding="utf-8")
    assert "Improved result." in second.read_text(encoding="utf-8")


def test_visit_lifecycle_counts_every_interval_and_stops(tmp_path):
    def snapshot(game, speed, sequence):
        value = SharedMemory()
        value.mVersion = SHARED_MEMORY_VERSION
        value.mGameState = game
        value.mSpeed = speed
        value.mSequenceNumber = sequence
        return value

    class Adapter:
        def __init__(self):
            self.samples = iter([
                snapshot(GAME_INGAME_PAUSED, 0, 2), snapshot(GAME_INGAME_PAUSED, 0, 4),
                snapshot(GAME_INGAME_PLAYING, 20, 6), snapshot(GAME_INGAME_PLAYING, 25, 8),
                None,
            ])
            self.connected = self.disconnected = False

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.disconnected = True

        def _read_snapshot(self):
            return next(self.samples)

    now = 0.0
    intervals = 0

    def sleep(seconds):
        nonlocal now, intervals
        now += seconds
        intervals += 1
        if intervals == 5:
            (tmp_path / "stop").touch()

    adapter = Adapter()
    assert track(tmp_path, adapter=adapter, monotonic=lambda: now, sleep=sleep) == 0
    result = json.loads((tmp_path / "clock.json").read_text())
    timeline = [json.loads(row) for row in (tmp_path / "timeline.jsonl").read_text().splitlines()]
    assert adapter.connected and adapter.disconnected
    assert result["status"] == "complete"
    assert result["elapsed_s"] == 1.0
    assert result["activity_s"] == {
        "unknown": 0.4, "not_driving": 0.2, "transition": 0.2, "driving": 0.2,
    }
    assert sum(sum(row["activity_s"].values()) for row in timeline) == pytest.approx(1.0)
    assert result["controls_min_max"]["speed"] == [20, 25]


def test_failed_clock_preserves_terminal_checkpoint(tmp_path):
    class Adapter:
        def connect(self):
            raise OSError("synthetic unavailable source")

        def disconnect(self):
            pass

    with pytest.raises(OSError, match="synthetic unavailable"):
        track(tmp_path, adapter=Adapter())
    assert json.loads((tmp_path / "clock.json").read_text())["status"] == "failed"
    timeline = (tmp_path / "timeline.jsonl").read_text().splitlines()
    assert json.loads(timeline[-1])["status"] == "failed"


def test_fresh_clock_process_uses_run_local_logs(tmp_path):
    run = tmp_path / "visit"
    run.mkdir()
    (run / "stop").touch()
    source_root = Path(__file__).resolve().parents[1]
    code = """
import json, sys
from pathlib import Path
from simcoach.cli import driver_clock
assert 'simcoach.adapters.ams2' not in sys.modules
assert not Path('logs').exists()
class Adapter:
    def connect(self): pass
    def disconnect(self): pass
run = Path(sys.argv[1])
assert driver_clock.track(run, adapter=Adapter()) == 0
assert json.loads((run / 'clock.json').read_text())['status'] == 'complete'
assert (run / 'logs' / 'simcoach.log').is_file()
assert not Path('logs').exists()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(run)], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(source_root)), capture_output=True, text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
