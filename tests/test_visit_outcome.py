import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from simcoach.live import visit_outcome
from simcoach.live.visit_clock import ACTIVITIES, REASONS
from simcoach.live.visit_outcome import build_visit_outcome, write_visit_outcome


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def visit(tmp_path):
    run = tmp_path / "visit"
    put(
        run / "status.json", dict(schema_version=1, status="complete", mode="replay", worker_exit=0)
    )
    put(
        run / "worker/status.json",
        dict(
            schema_version=1,
            status="complete",
            sessions=[],
            error_type=None,
            stop_reason="visit_stop_requested",
        ),
    )
    activity = dict.fromkeys(ACTIVITIES, 0)
    activity.update(driving=5, not_driving=3, unknown=2)
    reasons = dict.fromkeys(REASONS, 0)
    reasons.update(intentional_rest=1, assistant_wait=1, unspecified=1)
    clock = dict(
        schema_version=1,
        visit_id="synthetic",
        status="complete",
        publication_complete=True,
        publication_error=None,
        presence="declared_absent",
        elapsed_s=10,
        activity_s=activity,
        not_driving_reason_s=reasons,
        started_at=datetime.fromtimestamp(1000, UTC).isoformat(),
        updated_at=datetime.fromtimestamp(1010, UTC).isoformat(),
    )
    put(run / "clock/status.json", clock)
    put(run / "clock/summary.json", clock)
    put(run / "worker/closure-intent.json", dict(schema_version=1, at=1009, sessions=[]))
    put(
        run / "worker/closure.json",
        dict(schema_version=1, recordings=[], closed_at=1012, unregistered_artifacts=[]),
    )
    return run


@pytest.mark.parametrize("version", [1, 2, 3, True])
def test_checkpoint_consistency_version_is_scoped_and_retained(tmp_path, version):
    run = visit(tmp_path)
    checkpoint = run / "worker/checkpoints" / ("a" * 32)
    consistency = dict(schema_version=version, timing_basis="per_recorded_session", recordings=[])
    put(checkpoint / "consistency.json", consistency)
    put(checkpoint / "status.json", dict(schema_version=2, state="complete"))
    report = build_visit_outcome(run)
    files = report["worker_attempts"][0]["checkpoints"][0]["files"]
    expected = (f"reported_version_{version}" if type(version) is int and version in {1, 2}
                else "unknown_or_unsupported")
    assert files["consistency.json"] == dict(state=expected, reported=consistency)
    assert files["status.json"]["state"] == "unknown_or_unsupported"


def capture(run, name, complete=True, worker="worker"):
    path = run / worker / "sessions" / name
    path.mkdir(parents=True)
    (path / "session.yaml").write_text(
        yaml.safe_dump(
            dict(
                session_schema_version=1,
                recorded_session_id=name,
                complete=complete,
                failure_reason=None if complete else "no_ticks_collected",
            )
        )
    )
    return path


def timed_capture(run):
    session = capture(run, "synthetic")
    metadata = session / "session.yaml"
    value = yaml.safe_load(metadata.read_text())
    metadata.write_text(yaml.safe_dump(dict(
        value, lap_schema_version=1, car="Synthetic car", track="Synthetic track",
        session_type="practice")), encoding="utf-8")
    laps = [
        dict(lap_id=f"synthetic:lap:{index}", lap=index, lap_time_s=seconds,
             lap_time_source=source, valid=reason is None, invalid_reason=reason)
        for index, (seconds, source, reason) in enumerate([
            (60.0, "derived_from_ticks", None),
            (64.0, "derived_from_ticks", None),
            (59.0, "sim", None),
            (58.0, "derived_from_ticks", "sim_invalidated"),
            (2.0, "derived_from_ticks", "metadata_boundary"),
        ], 1)
    ]
    lap_file = session / "laps.jsonl"
    lap_file.write_text("".join(json.dumps(lap) + "\n" for lap in laps), encoding="utf-8")
    put(run / "worker/closure.json", dict(schema_version=1, closed_at=1012, recordings=[
        dict(path=str(session), validation="PASS", analysis="FAIL", summary=dict(
            recorded_session_id="synthetic", lap_count=5, valid_lap_count=3)),
    ]))
    return lap_file


def test_outcome_reports_every_lap_without_pooling_timing_sources(tmp_path):
    run = visit(tmp_path)
    artifact = timed_capture(run)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    outcome = write_visit_outcome(run)
    report = outcome["report"]
    recording = report["recordings"][0]
    assert recording["reported_analysis"] == "FAIL"
    review = recording["lap_review"]
    assert review["timing_basis"] == "mixed"
    assert review["statistics"]["best_s"] is None
    assert review["timing_groups"]["derived_from_ticks"]["spread_s"] == 4
    assert review["timing_groups"]["derived_from_ticks"]["median_s"] == 62
    assert review["timing_groups"]["sim"]["best_s"] == 59
    assert review["timing_groups"]["sim"]["spread_s"] is None
    assert [lap["lap"] for lap in review["laps"]] == [1, 2, 3, 4, 5]
    assert review["exclusions"] == dict(sim_invalidated=1, metadata_boundary=1)
    evidence = next(row for row in report["sources"] if Path(row["path"]) == artifact)
    assert evidence["sha256"] == digest
    text = (Path(outcome["directory"]) / "report.md").read_text(encoding="utf-8")
    assert "Captured time (2 valid). Best 60.000 s, median 62.000 s" in text
    assert "full valid lap spread 4.000 s" in text
    assert "Simulator time (1 valid). Best 59.000 s" in text
    assert "The Analysis Run failed." in text
    assert "1 metadata boundary, 1 sim invalidated" in text
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("problem", [
    "counts", "partial", "oversize", "progress", "duplicate", "closure_duplicate",
])
def test_outcome_withholds_incomplete_or_inconsistent_lap_evidence(tmp_path, problem):
    run = visit(tmp_path)
    lap_file = timed_capture(run)
    closure_path = run / "worker/closure.json"
    closure = json.loads(closure_path.read_bytes())
    if problem == "counts":
        closure["recordings"][0]["summary"]["valid_lap_count"] = 4
        put(closure_path, closure)
    elif problem == "partial":
        lap_file.write_bytes(lap_file.read_bytes() + b'{"lap":')
    elif problem == "oversize":
        lap_file.write_bytes(b" " * (visit_outcome.MAX_LAP_BYTES + 1))
    elif problem == "progress":
        closure_path.rename(run / "worker/closure-progress.json")
    elif problem == "closure_duplicate":
        closure["recordings"].append(dict(closure["recordings"][0], validation="FAIL"))
        put(closure_path, closure)
    else:
        line = lap_file.read_bytes().splitlines()[0]
        lap_file.write_bytes(lap_file.read_bytes() + line + b"\n")
    reported = build_visit_outcome(run)["recordings"][0]
    assert reported["lap_review"] is None
    assert reported["lap_review_reason"] == {
        "counts": "lap_counts_disagree_with_validation",
        "partial": "lap_records_missing_malformed_or_over_limit",
        "oversize": "lap_records_missing_malformed_or_over_limit",
        "progress": "validated_closure_unavailable",
        "duplicate": "invalid_lap_record",
        "closure_duplicate": "duplicate_closure_record",
    }[problem]


def test_lap_change_during_snapshot_withholds_results(tmp_path, monkeypatch):
    run = visit(tmp_path)
    lap_file = timed_capture(run)
    verify = visit_outcome._Reader.verify

    def changed(reader):
        lap_file.write_bytes(lap_file.read_bytes() + b"\n")
        return verify(reader)

    monkeypatch.setattr(visit_outcome._Reader, "verify", changed)
    result = build_visit_outcome(run)
    row = result["recordings"][0]
    assert result["source_snapshot"] == "changed"
    assert row["lap_review"] is None and row["lap_review_reason"] == "source_changed_during_summary"
    assert row["reported_validation"] == "PASS"


def analysis(path, name, status):
    directory = path / "analysis" / name
    directory.mkdir(parents=True)
    failed = status == "failed"
    (directory / "analysis.yaml").write_text(
        yaml.safe_dump(
            dict(
                analysis_schema_version=1,
                recorded_session_id=path.name,
                run_status="failed" if failed else "complete",
                analysis_status=None if failed else status,
                run_error="analysis_exception" if failed else None,
            )
        )
    )
    if not failed:
        (directory / "coach_report.md").write_text("No data-supported Coaching Instruction.\n")
    return directory


def radio(run, histories):
    lines = []
    for identity, states in histories.items():
        for index, state in enumerate(states):
            event = dict(
                schema_version=1,
                at=1020 + len(lines),
                transmission_id=identity,
                state=state,
                reason="explicit_driver_confirmation" if state == "acknowledged" else "synthetic",
            )
            if index == 0:
                event["spec"] = dict(
                    transmission_id=identity,
                    dedup_key=identity,
                    evidence_id=identity,
                    wav_path=str((run / "not-opened.wav").resolve()),
                    priority=100,
                    created_at=1000,
                    expires_at=2000,
                    optional=False,
                    wav_sha256="a" * 64,
                )
            lines.append(event)
    (run / "radio.jsonl").write_text("".join(json.dumps(row) + "\n" for row in lines))


@pytest.mark.parametrize("clock_fault", [False, True])
def test_radio_report_keeps_queue_storage_and_transport_timing_distinct(tmp_path, clock_fault):
    from simcoach.live.radio import Dispatcher, Transmission
    from tests.test_radio import Clock, FakeTransport, wav

    run = visit(tmp_path)
    clock = Clock()
    transport = FakeTransport()
    path = run / "radio.jsonl"

    def elapsed():
        return clock.now - 900

    owner = Dispatcher(path, transport, clock=clock, monotonic=elapsed,
                       evidence_valid=lambda _: True)
    write, connect = owner._event, transport.start

    def storage(identity, state, reason, **extra):
        write(identity, state, reason, **extra)
        if state == "playing":
            clock.now += .05  # Delay finishing the durable output intent.

    def start(path, *, can_start):
        clock.now += .15
        return connect(path, can_start=can_start)

    owner._event, transport.start = storage, start
    try:
        owner.submit(Transmission("fuel-1", "fuel-1", "obs", wav(run / "voice.wav"), 100,
                                  999.95, 1003), source_at=999.75)
        clock.now += .25
        owner.tick()
        clock.now += .6
        transport.result = "played"
        owner.tick()
    finally:
        owner.close()
    if clock_fault:
        # An edited synthetic observation cannot corroborate transport confirmation.
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]["transport_start"]["confirmed"]["timing_epoch"] = "other-process"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    report = build_visit_outcome(run)["radio"]
    timing = report["transmissions"][0]["software_timing"]
    assert report["transport_completed_count"] == 1 and report["driver_heard_count"] is None
    assert timing["source_observation_to_decision_utc_s"] == pytest.approx(.2)
    assert timing["dispatcher_queue_s"] == pytest.approx(.25)
    assert timing["output_intent_to_transport_call_s"] == pytest.approx(.05)
    assert timing["audible_latency_s"] is None
    if clock_fault:
        assert timing["transport_start_call_s"] is None
        assert timing["transport_confirmed_before_deadline"] is None
    else:
        assert timing["transport_start_call_s"] == pytest.approx(.15)
        assert timing["confirmed_to_terminal_observed_s"] == pytest.approx(.6)
        assert timing["source_observation_to_transport_confirmed_utc_s"] == pytest.approx(.7)
        assert timing["transport_confirmed_before_deadline"] is True


def test_recovery_outcome_preserves_failed_and_successful_attempts(tmp_path):
    run = visit(tmp_path)
    first = capture(run, "failed-session", complete=False)
    second = capture(run, "recovered-session", worker="worker-0002")
    analysis(second, "insufficient", "insufficient_data")
    put(run / "worker/status.json", dict(schema_version=1, status="failed", sessions=[str(first)],
                                          error_type="RuntimeError",
                                          stop_reason="first_tick_timeout"))
    put(run / "worker-0002/status.json", dict(schema_version=1, status="complete",
                                               sessions=[str(second)], error_type=None))
    put(run / "worker-0002/closure-intent.json",
        dict(schema_version=1, at=1011, sessions=[str(second)]))
    put(run / "worker-0002/closure.json", dict(schema_version=1, closed_at=1015,
                                               recordings=[dict(path=str(second), validation="PASS",
                                                                analysis="PASS")]))
    put(run / "status.json", dict(schema_version=1, mode="replay", status="incomplete",
                                   current_worker="worker-0002", worker_exit=0))
    for number, directory, code in ((1, "worker", 1), (2, "worker-0002", 0)):
        receipt = run / "capture-attempts" / f"{number:04d}"
        put(receipt / "intent.json", dict(schema_version=1, attempt=number,
                                          worker_directory=directory))
        put(receipt / "exit.json", dict(schema_version=1, exit_code=code, at=1015))
    ready = dict(schema_version=1, at=1014, attempt=2, worker_directory="worker-0002",
                 launch_to_ready_s=1.0, observed_exit_to_ready_s=3.0,
                 basis="host monotonic observations; not coverage or driver waiting time")
    put(run / "capture-attempts/0002/capture-ready.json", ready)
    original = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    report = build_visit_outcome(run)
    assert report["visit_status"] == "incomplete"
    assert report["current_worker"] == "worker-0002"
    assert report["worker_status"] == "complete"
    assert [a["worker_status"] for a in report["worker_attempts"]] == ["failed", "complete"]
    assert [a["worker_exit"] for a in report["worker_attempts"]] == [1, 0]
    assert report["worker_attempts"][0]["launch_receipts"]["capture-ready"] is None
    assert report["worker_attempts"][1]["launch_receipts"]["capture-ready"] == ready
    durations = [a["closure_timing"]["closure_elapsed_utc_s"] for a in report["worker_attempts"]]
    assert durations == [3, 4]
    assert report["closure_timing"]["closure_elapsed_utc_s"] == 4
    assert {r["worker_directory"] for r in report["recordings"]} == {"worker", "worker-0002"}
    assert len(report["recordings"]) == 2
    assert report["clock"]["elapsed_s"] == 10
    assert all(p.read_bytes() == content for p, content in original.items())


def test_receipt_only_attempt_and_unresolved_closure_are_not_omitted(tmp_path):
    run = visit(tmp_path)
    put(run / "capture-attempts/0002/intent.json", dict(schema_version=1, attempt=2,
                                                       worker_directory="worker-0002"))
    put(run / "worker-0003/closure-intent.json", dict(schema_version=1, at=1011))
    report = build_visit_outcome(run)
    rows = {a["worker_directory"]: a for a in report["worker_attempts"]}
    assert set(rows) == {"worker", "worker-0002", "worker-0003"}
    assert rows["worker-0002"]["worker_status"] == "unknown"
    assert "closure_intent_without_result" in rows["worker-0003"]["issues"]
    assert rows["worker-0003"]["closure_timing"]["closure_elapsed_utc_s"] is None
    assert report["snapshot_scope"] == "nonterminal_or_unknown"


def test_replacement_recordings_cannot_be_outcome_destinations(tmp_path):
    run = visit(tmp_path)
    recorded = capture(run, "replacement", worker="worker-0002")
    target = recorded / "analysis/new-report"
    with pytest.raises(ValueError, match="inside_recordings"):
        write_visit_outcome(run, target)
    assert not target.exists()


def test_unknown_current_worker_does_not_fall_back_to_successful_first(tmp_path):
    run = visit(tmp_path)
    put(run / "status.json", dict(schema_version=1, status="complete", mode="replay",
                                   current_worker="../elsewhere", worker_exit=0))
    report = build_visit_outcome(run)
    assert report["worker_status"] == "unknown"
    assert report["closure_timing"]["closure_elapsed_utc_s"] is None
    assert report["snapshot_scope"] == "nonterminal_or_unknown"


@pytest.mark.parametrize("escaped", ["worker", "sessions"])
def test_replacement_resolved_roots_cannot_inventory_external_files(tmp_path, monkeypatch, escaped):
    run = visit(tmp_path)
    replacement = run / "worker-0002"
    put(replacement / "status.json", dict(schema_version=1, status="failed", sessions=[]))
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "private.txt").write_text("Must not read or inventory this directory")
    original = visit_outcome._normal_path
    redirect = replacement if escaped == "worker" else replacement / "sessions"

    def resolve(value):
        return outside if Path(value) == redirect else original(value)

    monkeypatch.setattr(visit_outcome, "_normal_path", resolve)
    report = build_visit_outcome(run)
    assert not any(str(outside) in source["path"] for source in report["sources"])
    rows = [a for a in report["worker_attempts"] if a["worker_directory"] == "worker-0002"]
    if escaped == "worker":
        assert rows == []
    else:
        assert "sessions_root_outside_worker_not_read" in rows[0]["issues"]
        assert rows[0]["recording_count"] == 0


def test_clock_partitions_and_closure_timing_do_not_invent_waiting_or_coverage(tmp_path):
    run = visit(tmp_path)
    outcome = build_visit_outcome(run)
    assert outcome["clock"]["known"] is True
    assert outcome["clock"]["elapsed_s"] == 10
    assert outcome["clock"]["not_driving_reason_s"]["intentional_rest"] == 1
    assert outcome["closure_timing"]["closure_elapsed_utc_s"] == 3
    assert outcome["closure_timing"]["closure_vs_declared_departure_utc_s"] == 2
    assert outcome["unmeasured"]["capture_coverage"] is None
    assert outcome["unmeasured"]["false_call_count"] is None
    assert outcome["radio"]["driver_heard_count"] is None
    assert outcome["mode"] == "replay"


@pytest.mark.parametrize("policy", [None, "until_driving"])
def test_outcome_preserves_clock_reason_policy_without_reclassifying_time(tmp_path, policy):
    run = visit(tmp_path)
    original = json.loads((run / "clock/summary.json").read_text())
    if policy is not None:
        original["reason_policy"] = policy
        put(run / "clock/summary.json", original)
        put(run / "clock/status.json", original)
    clock = build_visit_outcome(run)["clock"]
    assert clock["known"] is True
    assert clock["reason_policy"] == policy
    assert clock["not_driving_reason_s"] == original["not_driving_reason_s"]
    assert clock["activity_s"] == original["activity_s"]


def test_all_capture_and_analysis_attempts_retained_without_modification(tmp_path):
    run = visit(tmp_path)
    good, bad = capture(run, "complete"), capture(run, "failed", False)
    insufficient = analysis(good, "one", "insufficient_data")
    analysis(good, "two", "failed")
    analysis(good, "three", "inconsistent")
    closure = json.loads((run / "worker/closure.json").read_text())
    closure["recordings"] = [
        dict(
            path=str(good),
            validation="PASS",
            analysis="PASS",
            analysis_path=str(insufficient),
            summary={"valid_lap_count": 1},
        ),
        dict(path=str(bad), validation="FAIL", analysis="NOT RUN", error_type="ValueError"),
    ]
    put(run / "worker/closure.json", closure)
    original = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    outcome = write_visit_outcome(run)
    rows = outcome["report"]["recordings"]
    assert len(rows) == 2
    assert [row["analysis_status"] for row in rows[0]["analyses"]] == [
        "insufficient_data",
        "inconsistent",
        None,
    ]
    assert rows[1]["capture_complete"] is False and rows[1]["reported_analysis"] == "NOT RUN"
    assert all(path.read_bytes() == content for path, content in original.items())
    report = Path(outcome["report_path"])
    assert report.is_file() and report.parent.name != "one"
    assert all(
        source["sha256"] for source in outcome["report"]["sources"] if source["state"] == "read"
    )


def test_radio_terminal_outcomes_not_confused_with_hearing(tmp_path):
    run = visit(tmp_path)
    radio(
        run,
        {
            "a": ["generated", "queued", "playing", "played"],
            "b": ["generated", "queued", "playing", "played", "acknowledged"],
            "c": ["generated", "suppressed"],
            "d": ["generated", "queued", "expired"],
            "e": ["generated", "queued", "failed"],
            "f": ["generated", "interrupted"],
        },
    )
    result = build_visit_outcome(run)["radio"]
    assert result["known"] is True
    assert result["transport_completed_count"] == 2
    assert result["explicit_acknowledgment_count"] == 1
    assert result["final_state_counts"] == dict(
        played=1, acknowledged=1, suppressed=1, expired=1, failed=1, interrupted=1
    )
    assert result["driver_heard_count"] is None and result["driver_not_heard_count"] is None
    assert not (run / "not-opened.wav").exists()


def fuel_decisions(root, count):
    from simcoach.live.supervisor import Journal

    journal = Journal(root / "supervision.sqlite3")
    decisions = []
    try:
        for index in range(count):
            notice = dict(id=f"{index:032x}", arm_id=f"{index+100:032x}",
                          evidence_id="a" * 64, kind="fuel_return", text="Pit this lap.",
                          at=1000, source_at=999.8, created_at=1000, expires_at=1003,
                          litres=1, threshold=1.5)
            journal.append(dict(event="notice", **notice))
            decisions.append(notice)
    finally:
        journal.close()
    return decisions


def fuel_row(notice, state, *, confirm=None, wrong_evidence=False):
    identity = f"fuel:{notice['arm_id']}:{notice['id']}"
    points = {
        "generated": 1000, "queued": 1000.05, "playing": 1000.1,
        "played": 1004, "expired": 1003, "suppressed": 1000.05,
    }
    order = {
        "played": ["generated", "queued", "playing", "played"],
        "expired": ["generated", "queued", "expired"],
        "suppressed": ["generated", "suppressed"],
    }

    def point(at):
        return dict(at=at, monotonic_s=at-900, timing_epoch="fixture")

    rows = [dict(schema_version=1, transmission_id=identity, state=name,
                 reason="submitted_while_muted" if state == name == "suppressed" else "fixture",
                 **point(points[name])) for name in order[state]]
    rows[0]["spec"] = dict(
        transmission_id=identity, dedup_key=f"fuel_return:{notice['arm_id']}",
        evidence_id="b" * 64 if wrong_evidence else notice["evidence_id"],
        wav_path=str(Path.cwd() / "unopened.wav"), wav_sha256="c" * 64, optional=False,
        priority=100, created_at=notice["created_at"], expires_at=notice["expires_at"],
    )
    rows[0]["source_at"] = notice["source_at"]
    if confirm is not None:
        rows[-1]["transport_start"] = dict(requested=point(1000.15), confirmed=point(confirm))
    return rows


@pytest.mark.parametrize("service", [False, True])
def test_fuel_decision_audit_keeps_expired_muted_missing_and_late_calls(tmp_path, service):
    run = visit(tmp_path)
    root = run / "operations" if service else run
    parent = json.loads((run / "status.json").read_text())
    parent.update(audio="enabled_muted", operations_mode="embedded")
    if service:
        parent.update(audio="service_enabled_muted", operations_mode="service",
                      operations=dict(service_id="fixture", binding_valid=True))
        put(root / "run.json", dict(schema_version=1, visit=str(run),
                                  service_id="fixture", audio_enabled=True))
        put(root / "status.json", dict(schema_version=1, status="complete", service_id="fixture"))
    put(run / "status.json", parent)
    notices = fuel_decisions(root, 6)
    rows = (fuel_row(notices[0], "played", confirm=1001)
            + fuel_row(notices[1], "played", confirm=1003)
            + fuel_row(notices[2], "expired")
            + fuel_row(notices[3], "suppressed")
            + fuel_row(notices[5], "played", confirm=1001, wrong_evidence=True))
    path = root / "radio.jsonl"
    path.write_text("".join(json.dumps(row) + "\n"
                            for row in sorted(rows, key=lambda row: row["at"])))
    journal = root / "supervision.sqlite3"
    before = (path.read_bytes(), journal.read_bytes())
    output = write_visit_outcome(run)
    audit = output["report"]["radio"]["fuel_return_audit"]
    assert audit["decision_count"] == 6
    assert audit["outcome_counts"] == dict(
        confirmed_before_deadline=1, confirmation_at_or_after_deadline=1,
        expired=1, muted=1, no_dispatch_record=1, binding_mismatch=1,
    )
    assert [entry["decision_id"] for entry in audit["decisions"]] == [row["id"] for row in notices]
    assert audit["unmatched_transmission_count"] == 1
    assert output["report"]["unmeasured"]["false_call_count"] is None
    assert output["report"]["radio"]["driver_heard_count"] is None
    assert "6 committed fuel-return decisions" in (
        Path(output["directory"]) / "report.md").read_text(encoding="utf-8")
    assert (path.read_bytes(), journal.read_bytes()) == before


@pytest.mark.parametrize("fault", ["missing", "partial", "live", "sidecar", "disabled", "bad_db"])
def test_fuel_decision_audit_keeps_unavailable_scopes_honest(tmp_path, fault):
    run = visit(tmp_path)
    parent = json.loads((run / "status.json").read_text())
    parent.update(audio="disabled" if fault == "disabled" else "enabled_muted",
                  operations_mode="embedded", status="running" if fault == "live" else "complete")
    put(run / "status.json", parent)
    fuel_decisions(run, 1)
    if fault == "partial":
        (run / "radio.jsonl").write_text('{"schema_version":')
    elif fault == "sidecar":
        (run / "supervision.sqlite3-journal").write_bytes(b"unfinished")
    elif fault == "bad_db":
        (run / "supervision.sqlite3").write_bytes(b"invalid")
    audit = build_visit_outcome(run)["radio"]["fuel_return_audit"]
    if fault in {"live", "sidecar", "bad_db"}:
        assert audit["decision_count"] is None and audit["outcome_counts"] is None
    else:
        assert audit["decision_count"] == 1
        expected = "audio_disabled" if fault == "disabled" else "dispatch_evidence_unavailable"
        assert audit["outcome_counts"] == {expected: 1}
    assert audit["unmatched_transmission_count"] is None


@pytest.mark.parametrize("coherent_clock", [True, False])
def test_fuel_decision_audit_joins_actual_supervisor_and_dispatcher(tmp_path, coherent_clock):
    from simcoach.live.fuel_radio import FuelPhrase, FuelRadio
    from simcoach.live.supervisor import FuelPlan, Journal, Supervisor
    from tests.test_fuel_radio import (
        CONTEXT,
        Clock,
        FakeTransport,
        approve,
        make_phrase,
        observe,
        status,
    )

    run = visit(tmp_path)
    parent = json.loads((run / "status.json").read_text())
    put(run / "status.json", dict(parent, audio="enabled_muted", operations_mode="embedded"))
    journal = Journal(run / "supervision.sqlite3")
    clock = Clock()
    engine = Supervisor(FuelPlan(CONTEXT, .5, 6, 2, 2, "fixture estimate"), journal)
    observe(engine, 0)
    observe(engine, 1)
    assert approve(engine)
    observe(engine, 2, 2)
    owner = FuelRadio(engine, run / "radio.jsonl", FuelPhrase.load(make_phrase(run / "phrase")),
                      lambda: ("source-a", status(2)), FakeTransport(clock), clock=clock)
    owner.dispatcher.monotonic = (lambda: clock.now) if coherent_clock else (lambda: 10)
    try:
        assert owner.tick()["delivery_state"] == "playing"
        clock.now += .6
        assert owner.tick()["delivery_state"] == "played"
    finally:
        owner.close()
        journal.close()
    audit = build_visit_outcome(run)["radio"]["fuel_return_audit"]
    expected = "confirmed_before_deadline" if coherent_clock else "start_unverified"
    assert audit["outcome_counts"] == {expected: 1}
    assert audit["decisions"][0]["decision_id"] == engine.return_notice["id"]
    assert audit["decisions"][0]["transmission_state"] == "played"


def test_sidecar_appearing_during_audit_invalidates_dispatch_counts(tmp_path, monkeypatch):
    run = visit(tmp_path)
    parent = json.loads((run / "status.json").read_text())
    put(run / "status.json", dict(parent, audio="enabled_muted", operations_mode="embedded"))
    fuel_decisions(run, 1)
    (run / "radio.jsonl").touch()
    verify = visit_outcome._Reader.verify

    def changed(reader):
        (run / "supervision.sqlite3-journal").write_bytes(b"new write in progress")
        return verify(reader)

    monkeypatch.setattr(visit_outcome._Reader, "verify", changed)
    report = build_visit_outcome(run)
    assert report["source_snapshot"] == "changed"
    audit = report["radio"]["fuel_return_audit"]
    assert audit["decision_count"] is None and audit["outcome_counts"] is None
    assert audit["reason"] == "source_changed_during_summary"


@pytest.mark.parametrize("bad", ["partial_line", "bad_spec", "impossible_transition"])
def test_bad_radio_evidence_preserves_prefix_without_final_counts(tmp_path, bad):
    run = visit(tmp_path)
    radio(run, {"a": ["generated", "queued"]})
    path = run / "radio.jsonl"
    if bad == "partial_line":
        path.write_text(path.read_text() + '{"schema_version":')
    else:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if bad == "bad_spec":
            rows[0]["spec"].pop("wav_sha256")
        else:
            rows[1]["state"] = "acknowledged"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = path.read_bytes()
    result = build_visit_outcome(run)["radio"]
    assert not result["known"] and result["final_state_counts"] is None
    assert result["invalid_line"] >= 1 and path.read_bytes() == before


def test_active_visit_and_worker_have_only_nonterminal_radio_observations(tmp_path):
    run = visit(tmp_path)
    radio(run, {"a": ["generated", "queued", "playing", "played"]})
    put(run / "worker/status.json", dict(schema_version=1, status="closing", sessions=[]))
    outcome = build_visit_outcome(run)
    assert outcome["snapshot_scope"] == "nonterminal_or_unknown"
    assert outcome["radio"]["known"] is False
    assert outcome["radio"]["final_state_counts"] is None
    assert outcome["radio"]["observed_transition_counts"]["played"] == 1


def test_terminal_clock_pair_mismatch_and_bad_partition_are_unknown(tmp_path):
    run = visit(tmp_path)
    summary = json.loads((run / "clock/summary.json").read_text())
    summary["elapsed_s"] = 99
    put(run / "clock/summary.json", summary)
    assert build_visit_outcome(run)["clock"]["known"] is False
    put(run / "clock/status.json", summary)
    assert build_visit_outcome(run)["clock"]["reason"] == "clock_partition_invalid"


def test_interrupted_clock_never_claims_departure(tmp_path):
    run = visit(tmp_path)
    summary = json.loads((run / "clock/summary.json").read_text())
    summary.update(status="interrupted", presence="unknown")
    put(run / "clock/summary.json", summary)
    put(run / "clock/status.json", summary)
    outcome = build_visit_outcome(run)
    assert outcome["clock"]["known"]
    assert outcome["clock"]["declared_departure_at"] is None
    assert outcome["closure_timing"]["closure_vs_declared_departure_utc_s"] is None


def test_unsupported_closure_intent_cannot_establish_known_timing(tmp_path):
    run = visit(tmp_path)
    put(run / "worker/closure-intent.json", dict(schema_version=999, at=1009))
    timing = build_visit_outcome(run)["closure_timing"]
    assert timing["closure_elapsed_utc_s"] is None
    assert timing["closure_reason"] == "closure_timestamps_unavailable_or_reversed"


def test_progress_only_failures_and_conflicting_reports_survive_final_closure(tmp_path):
    run = visit(tmp_path)
    final = capture(run, "final")
    missing = run / "worker/sessions/missing-attempt"
    put(run / "worker/closure.json", dict(schema_version=1, closed_at=1012, recordings=[
        dict(path=str(final), validation="PASS", analysis="PASS")]))
    put(run / "worker/closure-progress.json", dict(schema_version=1, recordings=[
        dict(path=str(final), validation="FAIL", analysis="NOT RUN", notes="earlier failure"),
        dict(path=str(missing), validation="FAIL", analysis="NOT RUN")]))
    rows = build_visit_outcome(run)["recordings"]
    assert len(rows) == 2
    assert rows[0]["reported_validation"] == "PASS"
    assert rows[0]["inventory"] == "closure"
    assert rows[0]["inventory_reports"][1]["reported"]["notes"] == "earlier failure"
    assert rows[1]["path"] == str(missing) and rows[1]["reported_validation"] == "FAIL"
    assert "unregistered_file_or_missing_session" in rows[1]["issues"]


def test_unsupported_progress_inventory_is_not_promoted(tmp_path):
    run = visit(tmp_path)
    put(run / "worker/closure-progress.json", dict(schema_version=999, recordings=[
        dict(path=str(run / "worker/sessions/not-established"), validation="PASS")]))
    assert build_visit_outcome(run)["recordings"] == []


def test_missing_then_appearing_source_invalidates_snapshot(tmp_path, monkeypatch):
    run = visit(tmp_path)
    verify = visit_outcome._Reader.verify

    def appear(reader):
        (run / "radio.jsonl").write_text("")
        return verify(reader)

    monkeypatch.setattr(visit_outcome._Reader, "verify", appear)
    outcome = build_visit_outcome(run)
    assert outcome["source_snapshot"] == "changed"
    assert not outcome["clock"]["known"] and not outcome["radio"]["known"]
    assert any(source["state"] == "appeared_during_summary" for source in outcome["sources"])


def test_outside_owned_paths_are_not_read(tmp_path):
    run = visit(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "session.yaml").write_text("do not read")
    put(run / "worker/closure.json", dict(schema_version=1, recordings=[dict(path=str(external))]))
    outcome = build_visit_outcome(run)
    assert outcome["recordings"][0]["issues"] == ["locator_outside_owned_sessions_not_read"]
    assert not any(source["path"].startswith(str(external)) for source in outcome["sources"])


def test_duplicate_yaml_metadata_is_unknown_and_preserved(tmp_path):
    run = visit(tmp_path)
    path = capture(run, "ambiguous")
    original = (path / "session.yaml").read_text() + "complete: false\n"
    (path / "session.yaml").write_text(original)
    outcome = build_visit_outcome(run)
    assert outcome["recordings"][0]["capture_complete"] is None
    assert "session_metadata_unknown_or_unsupported" in outcome["recordings"][0]["issues"]
    assert (path / "session.yaml").read_text() == original


def test_reader_refuses_resolved_file_outside_visit(tmp_path):
    run = visit(tmp_path)
    external = tmp_path / "private.txt"
    external.write_text("must not enter report")
    reader = visit_outcome._Reader(run)
    assert reader.read(external, format="text") is None
    assert reader.sources[str(external.resolve())]["sha256"] is None
    assert reader.verify() == []


def test_new_reports_are_immutable_and_existing_destination_refused(tmp_path):
    run = visit(tmp_path)
    destination = run / "review"
    first = write_visit_outcome(run, destination)
    path = Path(first["report_path"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        write_visit_outcome(run, destination)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert write_visit_outcome(run)["report_path"] != str(path)


@pytest.mark.parametrize("suffix", ["new-outcome", "analysis/new-outcome"])
def test_writer_cannot_add_artifacts_inside_owned_recordings(tmp_path, suffix):
    run = visit(tmp_path)
    session = capture(run, "canonical")
    original = (session / "session.yaml").read_bytes()
    destination = session / suffix
    with pytest.raises(ValueError, match="outcome_destination_inside_recordings"):
        write_visit_outcome(run, destination)
    assert not destination.exists()
    assert (session / "session.yaml").read_bytes() == original


def test_report_publication_failure_retains_partial_and_inputs(tmp_path, monkeypatch):
    run = visit(tmp_path)
    before = (run / "status.json").read_bytes()
    rename = Path.rename

    def fail_report(path, target):
        if Path(target).name == "report.json":
            raise OSError("synthetic publication failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_report)
    with pytest.raises(OSError, match="publication failure"):
        write_visit_outcome(run, run / "failed-report")
    assert (run / "failed-report/report.json.partial").exists()
    assert not (run / "failed-report/report.json").exists()
    assert (run / "status.json").read_bytes() == before
