"""Stint boundaries, async finalization and failure evidence without simulator I/O."""
import json
import os
import threading
import time
from dataclasses import replace

import pandas as pd
import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.ingest.recorder import Recorder, RecorderConfig
from simcoach.live.stint_checkpoint import StintCheckpoints, consistency_debrief
from simcoach.live.visit_worker import close_recordings
from tests.test_session import build_ticks, session_info


@pytest.mark.skipif(os.name != "nt", reason="Windows extended checkpoint paths")
def test_checkpoint_receipt_survives_long_worker_path(tmp_path):
    from simcoach.live.visit_worker import local_path

    # Root creation works ordinarily, but a request ID and atomic temporary name
    # extend checkpoint I/O beyond legacy MAX_PATH. Preserve the real boundary.
    run = tmp_path / ("w" * (220 - len(str(tmp_path)) - 1))
    local_path(run).mkdir()
    write_status(run / "worker-process.json", dict(schema_version=1, process={"pid": 1}))

    class RecorderStub:
        intake_active = False

    manager = StintCheckpoints(run, RecorderStub())
    identity = "d" * 32
    at = time.time()
    write_status(local_path(manager.requests / f"{identity}.json"), dict(
        schema_version=1, request_id=identity, action="checkpoint", at=at, capture_expires_at=at+3,
        worker={"pid": 1}))
    manager.poll()
    receipt = local_path(manager.root / identity / "status.json")
    result = json.loads(receipt.read_text())
    assert result["state"] == "refused"
    assert result["reason"] == "capture_unavailable_or_departing"
    assert not manager.errors and manager.thread is None
    assert (receipt.parent / "intent.json").is_file()
    manager.poll()  # Consumed request cannot create another intent or job.
    assert len(list(manager.root.iterdir())) == 1


def test_worker_storage_refusal_consumes_checkpoint_without_handoff(tmp_path):
    write_status(tmp_path / "worker-process.json", dict(schema_version=1, process={"pid": 1}))

    class RecorderStub:
        intake_active = True

        def request_checkpoint(self, request):
            pytest.fail(f"Storage refusal handed off {request}")

    manager = StintCheckpoints(tmp_path, RecorderStub())
    identity = "d" * 32
    at = time.time()
    write_status(manager.requests / f"{identity}.json", dict(
        schema_version=1, request_id=identity, action="checkpoint", at=at, capture_expires_at=at+3,
        worker={"pid": 1}))
    reason = "capture_unavailable_or_recording_storage_unverified"
    manager.poll(admission_block=reason)
    manager.poll()  # A later good observation cannot replay the refused request.
    receipt = json.loads((manager.root / identity / "status.json").read_text())
    assert receipt["state"] == "refused" and receipt["reason"] == reason
    assert manager.active is None and not manager.errors


@pytest.mark.parametrize("review_failure", [False, True])
def test_detached_prefix_keeps_intake_and_does_not_repeat_analysis(
        tmp_path, monkeypatch, review_failure):
    from simcoach.live import stint_checkpoint as module

    run = tmp_path / "worker"
    run.mkdir()
    write_status(run / "worker-process.json", dict(schema_version=1, process={"pid": 1}))
    stop = threading.Event()
    held = threading.Event()
    finalizing = threading.Event()
    original = module.finalize_recorded_session

    def slow(*args, **kwargs):
        finalizing.set()
        assert held.wait(5), "test failed to release its finalizer"
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "finalize_recorded_session", slow)
    if review_failure:
        from simcoach.live import visit_worker

        def ambiguous_review(*args, **kwargs):
            close_recordings(*args, **kwargs)
            raise OSError("synthetic failure after review publication")

        monkeypatch.setattr(visit_worker, "close_recordings", ambiguous_review)

    class Adapter:
        sim_name = "ams2"
        def connect(self):
            self.index = 0
        def disconnect(self):
            pass
        def read_session_info(self):
            return session_info()
        def read_tick(self):
            tick = build_ticks()[self.index % 20]
            result = replace(tick, t=self.index / 60, lap=self.index // 20 + 1)
            self.index += 1
            return result

    recorder = Recorder(Adapter(), RecorderConfig(run / "sessions", tick_rate_hz=500,
                                                  metadata_stable_s=0), stop_requested=stop.is_set)
    manager = StintCheckpoints(run, recorder)
    errors = []

    def capture():
        try:
            recorder.run()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=capture)
    thread.start()

    def wait(predicate, timeout=5):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            manager.tick()
            if predicate():
                return
            time.sleep(.005)
        pytest.fail("checkpoint observation timeout")

    def send(identity):
        at = time.time()
        write_status(manager.requests / f"{identity}.json", dict(
            schema_version=1, request_id=identity, action="checkpoint", at=at,
            capture_expires_at=at+3,
            worker={"pid": 1}))

    try:
        wait(lambda: recorder.ticks_observed >= 50)
        send("a" * 32)
        wait(finalizing.is_set)
        before = recorder.ticks_observed
        wait(lambda: recorder.ticks_observed > before + 40)
        assert thread.is_alive() and recorder.intake_active
        send("b" * 32)
        wait(lambda: (manager.root / ("b" * 32) / "status.json").exists())
        refused = json.loads((manager.root / ("b" * 32) / "status.json").read_text())
        assert refused["state"] == "refused" and refused["reason"] == "checkpoint_busy"
        held.set()
        wait(lambda: manager.active is None)
        first = manager.root / ("a" * 32)
        result = json.loads((first / "status.json").read_text())
        assert result["state"] == ("failed" if review_failure else "complete")
        assert (first / "debrief.md").is_file()
        assert thread.is_alive() and not (run.parent / "stop").exists()
        initial = dict(manager.rows)
        assert len(initial) == 1
        if not review_failure:
            # A second checkpoint owns only new recordings, not the first again.
            send("c" * 32)
            wait(lambda: (manager.root / ("c" * 32) / "closure.json").exists())
            wait(lambda: manager.active is None)
            assert len(manager.rows) == 2
        else:
            assert (first / "failure.txt").exists()
            assert next(iter(manager.rows.values()))["analysis"] == "NOT RUN"
    finally:
        held.set()
        stop.set()
        thread.join(timeout=10)
        manager.finish()
    assert not thread.is_alive() and not errors
    assert bool(manager.errors) is review_failure
    closure = close_recordings(run, recorder.session_paths, previous=manager.rows)
    assert all(r["validation"] == r["analysis"] == "PASS"
               for r in closure["recordings"][1:])
    recorded = []
    for path in recorder.session_paths:
        frame = pd.read_parquet(path / "ticks.parquet")
        recorded.extend(frame.t.tolist())
        assert len(list((path / "analysis").iterdir())) == 1
    assert len(recorded) == recorder.ticks_observed
    assert recorded == sorted(set(recorded))  # No missing/duplicated observed ticks at handoff.
    assert closure["recordings"][0] == next(iter(initial.values()))


def test_invalid_stale_or_wrong_worker_request_cannot_rotate(tmp_path):
    class Recorder:
        intake_active = True
        def request_checkpoint(self, request_id):
            raise AssertionError("Invalid request must not reach the recorder")
    run = tmp_path / "worker"
    run.mkdir()
    write_status(run / "worker-process.json", dict(schema_version=1, process={"pid": 1}))
    manager = StintCheckpoints(run, Recorder())
    for i, (at, worker) in enumerate(((time.time()-4, {"pid": 1}), (time.time(), {"pid": 2}))):
        identity = str(i) * 32
        write_status(manager.requests / f"{identity}.json", dict(
            schema_version=1, request_id=identity, action="checkpoint", at=at, worker=worker))
    manager.tick()
    assert manager.active is None
    assert all(json.loads(p.read_text())["state"] == "refused"
               for p in manager.root.glob("*/status.json"))


def test_checkpoint_uses_capture_expiry_even_for_fresh_request(tmp_path, monkeypatch):
    from simcoach.live import stint_checkpoint as module

    class Recorder:
        intake_active = True

        def request_checkpoint(self, request_id):
            pytest.fail("Expired capture evidence cannot schedule a rotation")

    run = tmp_path / "worker"
    run.mkdir()
    worker = {"pid": 1}
    write_status(run / "worker-process.json", dict(schema_version=1, process=worker))
    manager = StintCheckpoints(run, Recorder())
    identity = "a" * 32
    write_status(manager.requests / f"{identity}.json", dict(
        schema_version=1, request_id=identity, action="checkpoint", at=100.0,
        capture_expires_at=100.4, worker=worker))
    monkeypatch.setattr(module.time, "time", lambda: 100.5)
    manager.poll()
    receipt = json.loads((manager.root / identity / "status.json").read_text())
    assert receipt["state"] == "refused"
    assert receipt["reason"] == "capture_evidence_expired_or_unbound"


def test_consistency_keeps_slow_laps_and_exclusions_separate(tmp_path):
    capture = tmp_path / "capture"
    capture.mkdir()
    laps = [dict(lap_id=f"session-{i}", lap=i, lap_time_s=value, valid=valid,
                 lap_time_source="derived_from_ticks",
                 invalid_reason=None if valid else "sim_invalidated")
            for i, (value, valid) in enumerate(
                ((49, True), (59, True), (50, True), (30, False)), 1)]
    (capture / "laps.jsonl").write_text("\n".join(json.dumps(row) for row in laps))
    (tmp_path / "debrief.md").write_text("Existing deterministic result; no new instruction")
    consistency_debrief(tmp_path, [dict(path=str(capture), validation="PASS")])
    report = json.loads((tmp_path / "consistency.json").read_text())
    group = report["recordings"][0]
    assert group["statistics"]["valid_timed_laps"] == 3
    assert group["statistics"]["median_s"] == 50 and group["statistics"]["spread_s"] == 10
    assert group["invalid_reasons"] == {"sim_invalidated": 1}
    assert group["laps"] == laps
    assert report["cross_context_aggregation"] is False
    assert report["objective"] == "chief_review_required"


@pytest.mark.parametrize("source", ["sim", None])
def test_consistency_never_combines_timing_sources_or_infers_missing_source(tmp_path, source):
    capture = tmp_path / "capture"
    capture.mkdir()
    laps = [dict(lap_id=f"example:lap:{i}", lap=i, lap_time_s=seconds, valid=True,
                 invalid_reason=None, lap_time_source=kind)
            for i, (seconds, kind) in enumerate(
                [(49, "derived_from_ticks"), (59, "derived_from_ticks"), (1, source)], 1)]
    (capture / "laps.jsonl").write_text("\n".join(json.dumps(row) for row in laps))
    (tmp_path / "debrief.md").write_text("Synthetic analysis result")
    consistency_debrief(tmp_path, [dict(path=str(capture), validation="PASS")])
    report = json.loads((tmp_path / "consistency.json").read_text())
    assert report["schema_version"] == 2
    group = report["recordings"][0]
    assert group["laps"] == laps
    assert group["timing_basis"] == ("mixed" if source else "unknown")
    assert group["statistics"]["valid_timed_laps"] == 3
    assert all(group["statistics"][key] is None for key in (
        "best_s", "median_s", "spread_s", "population_stddev_s"))
    assert group["timing_groups"]["derived_from_ticks"]["spread_s"] == 10
    assert "Combined pace and consistency withheld" in (tmp_path / "stint-debrief.md").read_text()


def test_uncertain_intent_publication_never_resubmits_to_recorder(tmp_path, monkeypatch):
    from simcoach.live import stint_checkpoint as module

    class Recorder:
        intake_active = True
        def request_checkpoint(self, request_id):
            raise AssertionError("Unconfirmed durable intent cannot schedule a handoff")

    run = tmp_path / "worker"
    run.mkdir()
    write_status(run / "worker-process.json", dict(schema_version=1, process={"pid": 1}))
    manager = StintCheckpoints(run, Recorder())
    identity = "d" * 32
    write_status(manager.requests / f"{identity}.json", dict(
        schema_version=1, request_id=identity, action="checkpoint",
        at=time.time(), worker={"pid": 1}))
    original = module.publish
    calls = []

    def uncertain(path, value):
        original(path, value)
        if path.name == "intent.json":
            calls.append(1)
            raise OSError("synthetic post-publication failure")

    monkeypatch.setattr(module, "publish", uncertain)
    manager.tick()
    manager.tick()
    assert calls == [1] and manager.active is None
    assert (manager.root / identity / "intent.json").exists()
    assert manager.errors[0]["effect"] == "checkpoint_control"
