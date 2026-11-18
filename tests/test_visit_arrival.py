"""Exercise explicit presence before any usable simulator source."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcoach.cli import visit
from simcoach.cli.race_watch import SourceLock
from simcoach.cli.visit import run_visit
from simcoach.live import visit_arrival
from simcoach.live.supervisor import FuelPlan
from simcoach.live.visit_coverage import _envelope
from simcoach.live.visit_outcome import build_visit_outcome
from tests.test_race_watch import CONTEXT
from tests.test_visit import ROOT, execute


def read(path):
    return json.loads(path.read_bytes())


def declare(root):
    result = visit_arrival.declare_arrival(root, present=True)
    path = Path(result["arrival_path"])
    value = read(path)
    return result["arrival_id"], path, datetime.fromisoformat(value["at"]), value["elapsed"]


def test_arrival_needs_explicit_presence_and_respects_host_lock(tmp_path):
    with pytest.raises(visit_arrival.ArrivalError, match="explicit_presence_required"):
        visit_arrival.declare_arrival(tmp_path, present=False)
    lock = SourceLock(tmp_path / "visit.lock")
    try:
        with pytest.raises(RuntimeError, match="archive_already_observed"):
            declare(tmp_path)
    finally:
        lock.close()
    assert not (tmp_path / "arrivals").exists()


@pytest.mark.parametrize(("wall", "monotonic"), [(-1, -1), (2, 1), (7201, 7201)])
def test_stale_or_incoherent_arrival_cannot_start_host(tmp_path, wall, monotonic):
    identity, path, at, elapsed = declare(tmp_path)
    with pytest.raises(visit_arrival.ArrivalError, match="clock_discontinuous_or_stale"):
        visit_arrival.claim_arrival(tmp_path, identity, at=at + timedelta(seconds=wall),
                                   elapsed=elapsed + monotonic, run=tmp_path / "visit")
    assert not (path.parent / "resolution.json").exists()


def test_arrival_cannot_move_to_another_root_or_fixture(tmp_path):
    root = tmp_path / "original"
    identity, path, at, elapsed = declare(root)
    copied = tmp_path / "other" / path.relative_to(root)
    copied.parent.mkdir(parents=True)
    copied.write_bytes(path.read_bytes())
    with pytest.raises(visit_arrival.ArrivalError, match="unbound"):
        visit_arrival.claim_arrival(tmp_path / "other", identity, at=at, elapsed=elapsed,
                                   run=tmp_path / "other/visit")
    with pytest.raises(visit_arrival.ArrivalError, match="requires_live_archive"):
        run_visit(root, intent="synthetic", present=True, fixture=tmp_path / "fixture",
                  arrival=identity)
    assert not (path.parent / "resolution.json").exists()


def test_departure_without_host_is_explicit_and_single_use(tmp_path):
    identity, path, at, elapsed = declare(tmp_path)
    with pytest.raises(visit_arrival.ArrivalError, match="explicit_departure_required"):
        visit_arrival.depart_before_host(tmp_path, identity, left=False)
    departure = visit_arrival.depart_before_host(tmp_path, identity, left=True)
    assert departure["resolution"] == "driver_left_before_host" and departure["run"] is None
    assert departure["elapsed_s"] == departure["unknown_s"] >= 0
    assert read(path.parent / "resolution.json") == departure
    with pytest.raises(visit_arrival.ArrivalError, match="already_resolved"):
        visit_arrival.claim_arrival(tmp_path, identity, at=at, elapsed=elapsed,
                                   run=tmp_path / "visit")


def test_incoherent_counter_does_not_erase_explicit_departure(tmp_path, monkeypatch):
    identity, path, _, elapsed = declare(tmp_path)
    monkeypatch.setattr(visit_arrival, "time", SimpleNamespace(perf_counter=lambda: elapsed + 5))
    departure = visit_arrival.depart_before_host(tmp_path, identity, left=True)
    assert departure["resolution"] == "driver_left_before_host"
    assert departure["elapsed_s"] is departure["unknown_s"] is None
    assert read(path.parent / "resolution.json") == departure


def test_partial_claim_blocks_second_attempt_without_destroying_evidence(tmp_path):
    identity, path, at, elapsed = declare(tmp_path)
    partial = path.with_name("resolution.partial")
    partial.write_bytes(b"synthetic incomplete claim")
    with pytest.raises(visit_arrival.ArrivalError, match="already_resolved_or_uncertain"):
        visit_arrival.claim_arrival(tmp_path, identity, at=at, elapsed=elapsed,
                                   run=tmp_path / "visit")
    assert partial.read_bytes() == b"synthetic incomplete claim"


def test_host_initialization_failure_keeps_claim_but_releases_owner(tmp_path, monkeypatch):
    identity, path, _, _ = declare(tmp_path)

    def unavailable(*args, **kwargs):
        raise OSError("synthetic clock initialization failure")

    monkeypatch.setattr(visit, "VisitClock", unavailable)
    with pytest.raises(OSError, match="synthetic clock initialization"):
        run_visit(tmp_path, intent="synthetic", present=True, archive=tmp_path / "archive",
                  arrival=identity)
    resolution = read(path.with_name("resolution.json"))
    assert resolution["resolution"] == "claimed"
    lock = SourceLock(tmp_path / "visit.lock")
    lock.close()
    with pytest.raises(visit_arrival.ArrivalError, match="already_resolved"):
        run_visit(tmp_path, intent="synthetic", present=True, archive=tmp_path / "archive",
                  arrival=identity)


@pytest.mark.parametrize(("with_note", "explicit"), [(False, True), (True, True), (True, False)],
                         ids=["legacy", "declared-intent", "automatic-claim"])
def test_native_cli_handoff_preserves_prehost_unknown_and_binding(tmp_path, with_note, explicit):
    root = tmp_path / "v"
    archive = tmp_path / "archive"
    archive.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(dict(schema_version=1, **asdict(
        FuelPlan(CONTEXT, 0.5, 6, 2, 2.0, "synthetic arrival test")))), encoding="utf-8")
    visit_args = ["-B", "-m", "simcoach.cli.visit"]
    arrival = execute([*visit_args, "arrive", "--present", "--out", str(root)])
    assert arrival.returncode == 0, arrival.stdout + arrival.stderr
    declaration = json.loads(arrival.stdout)
    note_path = None
    if with_note:
        declared = execute([*visit_args, "note-before-host", declaration["arrival_id"],
                            "--reason", "assistant_wait", "--out", str(root)])
        assert declared.returncode == 0, declared.stdout + declared.stderr
        note_path = Path(json.loads(declared.stdout)["note_path"])
    arrival_args = ["--arrival", declaration["arrival_id"]]
    start = [*visit_args, "start", "--present", *(arrival_args if explicit else []),
             "--intent", "synthetic arrival test", "--archive", str(archive), "--plan",
             str(plan), "--out", str(root), "--max-seconds", "15"]
    with (tmp_path / "host.log").open("w", encoding="utf-8") as log:
        host = subprocess.Popen(
            [sys.executable, *start], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.perf_counter() + 12
            while host.poll() is None and time.perf_counter() < deadline:
                statuses = list(root.glob("*/status.json"))
                if statuses and read(statuses[0])["status"] == "running":
                    break
                time.sleep(.05)
            else:
                pytest.fail("Synthetic host never entered waiting_for_source")
            run = statuses[0].parent
            running = read(statuses[0])
            assert running["phase"] == "waiting_for_source" and not running["ready_to_drive"]
            stop = execute([*visit_args, "stop", str(run)])
            assert stop.returncode == 0, stop.stdout + stop.stderr
            assert host.wait(timeout=10) == 1  # No synthetic archive source or recording.
        finally:
            if host.poll() is None:
                host.terminate()
                host.wait(timeout=5)

    terminal = read(run / "status.json")
    claim = terminal["arrival"]
    original = Path(declaration["arrival_path"])
    assert claim == read(original.with_name("resolution.json"))
    assert claim["run"] == run.name and claim["elapsed_s"] > 0
    clock = read(run / "clock/summary.json")
    assert clock["started_at"] == declaration["at"] and clock["presence"] == "declared_absent"
    assert clock["activity_s"]["unknown"] == pytest.approx(clock["elapsed_s"])
    assert sum(clock["not_driving_reason_s"].values()) == 0
    prehost = build_visit_outcome(run)["clock"]["prehost"]
    assert prehost["unknown_s"] == claim["elapsed_s"]
    assert prehost["scope"] == "reported_host_binding"
    if with_note:
        intent = prehost["declared_intent"]
        assert intent["state"] == "verified"
        assert intent["reason_s"]["assistant_wait"] > 0
        assert sum(intent["reason_s"].values()) == pytest.approx(claim["unknown_s"])
        outcome_receipt = read(run / "outcome-result.json")
        report_md = Path(outcome_receipt["report_path"]).with_name("report.md").read_text()
        assert "declared as waiting for the Chief" in report_md
        assert "seconds of unknown activity" in report_md
        assert "During the host run, 0.0 seconds of non-driving time" in report_md
        note_bytes = note_path.read_bytes()
        note_path.write_bytes(note_bytes + b"\n")
        try:
            changed = build_visit_outcome(run)["clock"]
            assert changed["prehost"]["declared_intent"] == {"state": "unavailable"}
            assert changed["activity_s"] == clock["activity_s"]
            partial_review = _envelope(run, archive, None)
            assert partial_review["arrival"]["declared_intent"] == {"state": "unavailable"}
            assert partial_review["prehost_declared_s"] > 0
        finally:
            note_path.write_bytes(note_bytes)
    else:
        assert "declared_intent" not in prehost

    envelope = _envelope(run, archive, None)
    assert envelope["scope"] == "declared_arrival_to_departure"
    assert envelope["prehost_declared_s"] > 0
    assert envelope["host_elapsed_s"] + claim["elapsed_s"] == pytest.approx(clock["elapsed_s"])
    with pytest.raises(ValueError, match="cannot_be_replaced"):
        _envelope(run, archive, original)
    replay = execute(start if explicit else [*start, *arrival_args])
    assert replay.returncode == 2 and "already_resolved" in replay.stderr
    original_bytes = original.read_bytes()
    changed = original_bytes + b"\n"
    (tmp_path / "tampered-declaration.json").write_bytes(changed)
    original.write_bytes(changed)
    try:
        with pytest.raises(visit_arrival.ArrivalError, match="claim_invalid"):
            _envelope(run, archive, None)
    finally:
        original.write_bytes(original_bytes)

def test_prehost_reason_changes_partition_declared_purpose_only(tmp_path, monkeypatch):
    identity, path, at, elapsed = declare(tmp_path)

    def note(reason, offset):
        monkeypatch.setattr(visit_arrival, "_now", lambda: (at + timedelta(seconds=offset),
                                                            elapsed + offset))
        return visit_arrival.note_before_host(tmp_path, identity, reason=reason)

    note("assistant_wait", 1)
    note("intentional_rest", 3)
    note("unspecified", 6)
    run = tmp_path / "visit"
    _, _, claim = visit_arrival.claim_arrival(
        tmp_path, identity, at=at + timedelta(seconds=7), elapsed=elapsed + 7, run=run)
    assert claim["elapsed_s"] == claim["unknown_s"] == 7
    intent = claim["declared_intent"]
    assert intent["state"] == "verified"
    assert intent["note_count"] == 3
    assert intent["reason_s"] == {
        "unspecified": 2, "assistant_wait": 2, "intentional_rest": 3, "setup": 0, "other_wait": 0,
    }
    assert visit_arrival.verify_arrival_claim(run, claim)["declared_intent"] == intent
    with pytest.raises(visit_arrival.ArrivalError, match="already_resolved"):
        note("other_wait", 8)
    assert read(path.parent / "resolution.json") == claim


def test_failed_note_preserves_host_claim_and_unknown_activity(tmp_path, monkeypatch):
    identity, path, at, elapsed = declare(tmp_path)
    monkeypatch.setattr(visit_arrival, "_now", lambda: (at + timedelta(seconds=1), elapsed + 1))

    def fail(path, _value):
        path.with_suffix(".partial").write_bytes(b"synthetic interrupted note")
        raise OSError("synthetic write failure")

    with monkeypatch.context() as broken:
        broken.setattr(visit_arrival, "_publish", fail)
        with pytest.raises(OSError, match="synthetic write failure"):
            visit_arrival.note_before_host(tmp_path, identity, reason="assistant_wait")
    partial, = path.parent.glob("reason-*.partial")
    run = tmp_path / "visit"
    _, _, claim = visit_arrival.claim_arrival(
        tmp_path, identity, at=at + timedelta(seconds=2), elapsed=elapsed + 2, run=run)
    assert claim["unknown_s"] == 2
    assert claim["declared_intent"]["state"] == "unavailable"
    assert "reason_s" not in claim["declared_intent"]
    assert partial.read_bytes() == b"synthetic interrupted note"


def test_declared_prehost_intent_requires_coherent_note_clocks(tmp_path, monkeypatch):
    identity, path, at, elapsed = declare(tmp_path)
    monkeypatch.setattr(visit_arrival, "_now", lambda: (at + timedelta(seconds=5), elapsed + 1))
    with pytest.raises(visit_arrival.ArrivalError, match="clock_discontinuous"):
        visit_arrival.note_before_host(tmp_path, identity, reason="assistant_wait")
    assert not list(path.parent.glob("reason-*"))

def test_retried_arrive_reuses_exact_open_receipt(tmp_path):
    identity, path, at, _ = declare(tmp_path)
    original = path.read_bytes()
    retried = visit_arrival.declare_arrival(tmp_path, present=True)
    assert retried["arrival_id"] == identity and retried["at"] == at.isoformat()
    assert retried["reused"] is True
    assert path.read_bytes() == original
    assert list(path.parent.parent.iterdir()) == [path.parent]


def test_stale_arrival_can_be_abandoned_without_claiming_departure(tmp_path, monkeypatch):
    identity, path, at, elapsed = declare(tmp_path)
    stamp = at + timedelta(seconds=visit_arrival.MAX_PREHOST_SECONDS + 1)
    monkeypatch.setattr(visit_arrival, "_now", lambda: (stamp, elapsed + 7201))
    with pytest.raises(visit_arrival.ArrivalError, match="stale.*" + identity):
        visit_arrival.declare_arrival(tmp_path, present=True)
    with pytest.raises(visit_arrival.ArrivalError, match="explicit_abandonment_required"):
        visit_arrival.abandon_before_host(tmp_path, identity, confirmed=False)
    abandoned = visit_arrival.abandon_before_host(tmp_path, identity, confirmed=True)
    assert abandoned["disposition"] == "abandoned"
    assert abandoned["presence"] == "unknown" and abandoned["elapsed_s"] is None
    assert not (path.parent / "resolution.json").exists()
    newer = visit_arrival.declare_arrival(tmp_path, present=True)
    assert newer["arrival_id"] != identity
    with pytest.raises(visit_arrival.ArrivalError, match="already_resolved"):
        visit_arrival.claim_arrival(tmp_path, identity, at=stamp, elapsed=elapsed + 7201,
                                   run=tmp_path / "visit")

def test_partial_resolution_has_explicit_abandonment_recovery(tmp_path):
    identity, declaration, _, _ = declare(tmp_path)
    partial = declaration.with_name("resolution.partial")
    partial.write_bytes(b"synthetic partial claim")
    with pytest.raises(visit_arrival.ArrivalError, match="resolution_uncertain:" + identity):
        visit_arrival.declare_arrival(tmp_path, present=True)
    receipt = visit_arrival.abandon_before_host(tmp_path, identity, confirmed=True)
    assert read(Path(receipt["abandonment_path"]))["presence"] == "unknown"
    assert partial.read_bytes() == b"synthetic partial claim"
    assert visit_arrival.declare_arrival(tmp_path, present=True)["arrival_id"] != identity


def test_multiple_legacy_arrivals_require_explicit_selection_by_retiring_the_other(tmp_path):
    original, path, at, elapsed = declare(tmp_path)
    other = "f" * 32
    legacy = path.parent.with_name(other) / path.name
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({**read(path), "arrival_id": other}), encoding="utf-8")
    with pytest.raises(visit_arrival.ArrivalError, match="multiple_pending_arrivals"):
        visit_arrival.declare_arrival(tmp_path, present=True)
    run = tmp_path / "visit"
    with pytest.raises(visit_arrival.ArrivalError, match="multiple_pending_arrivals"):
        visit_arrival.claim_arrival(tmp_path, original, at=at, elapsed=elapsed, run=run)
    visit_arrival.abandon_before_host(tmp_path, other, confirmed=True)
    assert visit_arrival.declare_arrival(tmp_path, present=True)["arrival_id"] == original
    assert visit_arrival.claim_arrival(tmp_path, original, at=at, elapsed=elapsed, run=run)[2][
        "arrival_id"] == original


@pytest.mark.parametrize("published", [False, True], ids=["pre-publication", "post-publication"])
def test_native_arrival_process_death_recovery(tmp_path, published):
    root = tmp_path / "visits"
    code = """
import os, sys
from pathlib import Path
from simcoach.live import visit_arrival

publish = visit_arrival._publish
def crash(path, value):
    if sys.argv[2] == 'True':
        publish(path, value)
    else:
        path.with_suffix('.partial').write_text('synthetic interrupted declaration')
    os._exit(71)
visit_arrival._publish = crash
visit_arrival.declare_arrival(Path(sys.argv[1]), present=True)
"""
    crash = execute(["-B", "-c", code, str(root), str(published)])
    assert crash.returncode == 71, crash.stdout + crash.stderr
    [original] = (root / "arrivals").iterdir()
    persisted_name = "declaration.json" if published else "declaration.partial"
    source = original / persisted_name
    previous = source.read_bytes()
    retried = execute(["-B", "-m", "simcoach.cli.visit", "arrive", "--present", "--out", str(root)])
    assert retried.returncode == 0, retried.stdout + retried.stderr
    reply = json.loads(retried.stdout)
    assert reply["reused"] is published
    assert (reply["arrival_id"] == original.name) is published
    assert source.read_bytes() == previous
