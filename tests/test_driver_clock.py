import pytest

from simcoach.adapters.ams2 import (
    GAME_INGAME_PAUSED,
    GAME_INGAME_PLAYING,
    SHARED_MEMORY_VERSION,
    SharedMemory,
)
from simcoach.cli.driver_clock import activity, interval_activity, save_output


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
