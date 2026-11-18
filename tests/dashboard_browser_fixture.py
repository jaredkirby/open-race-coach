"""Synthetic loopback fixture controlled by a private file, never a real archive."""
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simcoach.cli.dashboard import DashboardServer
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.dashboard import DashboardFeed
from simcoach.live.dashboard_feedback import reviewed_feedback, select_feedback
from tests.test_activity_evidence import SOURCE, status
from tests.test_dashboard_feedback import accept, prepared

out = Path(sys.argv[1])
docs = Path(__file__).resolve().parents[1] / "docs"
feed = DashboardFeed()
visit, _, card_path = prepared(out)
selection = select_feedback(visit, accept(visit, card_path))
selections = {"available": (visit, selection)}
for timing in ("simulator", "mixed"):
    other_visit, _, other_card = prepared(out / timing, timing=timing)
    selections[timing] = (other_visit, select_feedback(
        other_visit, accept(other_visit, other_card)))
assets = {"/": ((docs / "live-dashboard.html").read_bytes(), "text/html; charset=utf-8"),
          "/live-dashboard.mjs": ((docs / "live-dashboard.mjs").read_bytes(), "text/javascript")}
token = "synthetic-browser-test-only"
with DashboardServer(("127.0.0.1", 0), feed, token, assets) as server:
    write_status(out / "fixture.json", dict(port=server.server_port, token=token,
                                           input="synthetic", simulator_access=False))
    start = time.perf_counter()
    while time.perf_counter() - start < 90:
        control = (out / "control").read_text() if (out / "control").exists() else "available"
        if control == "stop":
            break
        now, elapsed = time.time(), time.perf_counter() - start
        car = "Alternate Formula" if control == "transition" else "Formula Inter"
        value = status(elapsed, mCarName=car, mTrackLocation="Interlagos",
            mTrackVariation="GP",
            mSpeed=40, mGear=3, mRpm=5500, mThrottle=.75, mBrake=0,
            mFuelCapacity=10, mFuelLevel=.42, mCurrentLap=5, mLapInvalidated=False,
            mLastLapTime=50.1, mBestLapTime=49.7)
        value["updated_at"] = datetime.fromtimestamp(now, UTC).isoformat()
        if control == "stale":
            value.update(coverage="stale_mapping", latest_is_current=False)
        feed.sample(value, source_id=SOURCE, now=now, elapsed=time.perf_counter())
        if control == "transition":
            observed = feed.response("internal-test-observation", elapsed=time.perf_counter())
            if observed["reason"] == "context_changed":
                # Advance into the new context before the next client request.
                write_status(out / "skipped-context.json", observed)
                time.sleep(.05)
                continue
        selected_visit, selected_card = selections.get(control, selections["available"])
        feed.panels(reviewed_feedback(selected_visit, selected_card),
                    dict(state="armed", valid_for_ms=1500, reason=None,
                         service_id="c" * 32, updated_at=now), elapsed=time.perf_counter())
        server.handle_request()
(out / "fixture-exit.json").write_text(json.dumps(dict(exit_code=0, raw_capture=False)))
