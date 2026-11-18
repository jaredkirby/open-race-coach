from dataclasses import asdict

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.dashboard_operations import OperationsDisplay
from tests.test_race_watch import BASE, CONTEXT


def service(tmp_path):
    root, archive = tmp_path / "operations", tmp_path / "archive"
    root.mkdir()
    archive.mkdir()
    run = dict(schema_version=1, mode="local_operations", service_id="a" * 32,
               archive=str(archive), process=dict(pid=123, created_100ns="100"))
    write_status(root / "run.json", run)
    view = OperationsDisplay(root, archive, probe=lambda pid: dict(
        state="running", pid=pid, created_100ns="100", reason=None))
    return root, view


def observe(root, view, second, *, state_change=None, source_change=None, telemetry_change=None):
    state = dict(schema_version=1, service_id="a" * 32, status="running", updated_at=BASE+second,
        supervision=dict(fuel_state="armed", supervision_health="healthy",
                         expires_at=BASE+second+2),
        observed_source=dict(source_id="b" * 64, at=BASE+second, sequence=int(second)*2,
                             participant_index=None,
                             context=asdict(CONTEXT)) | (source_change or {}),
        radio=dict(delivery_health="healthy")) | (state_change or {})
    write_status(root / "status.json", state)
    write_status(root / "heartbeat.json", dict(schema_version=1, at=BASE+second))
    telemetry = dict(state="fresh", source_id="b" * 64,
                     source_at=BASE+second, source_sequence=int(second)*2, source_epoch_at=BASE,
                     telemetry=dict(context=asdict(CONTEXT))) | (telemetry_change or {})
    return view.observe(telemetry, now=BASE+second, elapsed=second)


def test_service_needs_real_progress_and_current_source_not_just_live_pid(tmp_path):
    root, view = service(tmp_path)
    assert observe(root, view, 0)["state"] == "unknown"
    assert observe(root, view, 0)["state"] == "unknown"
    value = observe(root, view, .5)
    assert value["state"] == "armed" and value["valid_for_ms"] == 2000
    assert value["audible_delivery_verified"] is False
    assert "process" not in value and "archive" not in value
    assert observe(root, view, 1, telemetry_change={"state": "stale"})["state"] == "unknown"


@pytest.mark.parametrize("fault", ["dead", "reused_pid", "context", "source", "expired",
                                   "closed", "injected", "replaced", "clock_gap", "view",
                                   "service_behind", "sequence_changed"])
def test_faults_revoke_supervision_display(tmp_path, fault):
    root, view = service(tmp_path)
    observe(root, view, 0)
    assert observe(root, view, .5)["state"] == "armed"
    args = {}
    if fault in {"dead", "reused_pid"}:
        view.probe = lambda pid: dict(state="exited" if fault == "dead" else "running",
            pid=pid, created_100ns="100" if fault == "dead" else "101", reason=None)
    elif fault == "context":
        args["source_change"] = dict(context=asdict(CONTEXT) | {"car": "other"})
    elif fault == "source":
        args["source_change"] = dict(source_id="c" * 64)
    elif fault == "view":
        args["source_change"] = dict(participant_index=1)
    elif fault == "service_behind":
        args["source_change"] = dict(at=BASE+.5, sequence=0)
        args["telemetry_change"] = dict(source_epoch_at=BASE+.75)
    elif fault == "sequence_changed":
        args["source_change"] = dict(sequence=0)
    elif fault == "expired":
        args["source_change"] = dict(at=BASE-10)
    elif fault == "closed":
        args["state_change"] = dict(status="complete")
    elif fault in {"injected", "replaced"}:
        import json
        value = json.loads((root / "run.json").read_text())
        value["mode" if fault == "injected" else "service_id"] = "changed"
        write_status(root / "run.json", value)
    result = observe(root, view, 5 if fault == "clock_gap" else 1, **args)
    assert result["state"] == "unknown" and result["valid_for_ms"] == 0


def test_recent_service_sample_from_current_epoch_can_still_be_displayed(tmp_path):
    root, view = service(tmp_path)
    observe(root, view, .5)
    result = observe(root, view, 1, source_change=dict(at=BASE+.5, sequence=0))
    assert result["state"] == "armed"


@pytest.mark.parametrize("state", ["unarmed", "suspended", "return_due"])
def test_unarmed_suspended_and_return_due_remain_distinct(tmp_path, state):
    root, view = service(tmp_path)
    observe(root, view, 0)
    result = observe(root, view, .5, state_change=dict(supervision=dict(
        fuel_state=state, supervision_health="healthy", expires_at=BASE+2)))
    assert result["state"] == state


@pytest.mark.parametrize("filename", ["run.json", "status.json", "heartbeat.json"])
def test_malformed_service_file_cannot_take_down_dashboard(tmp_path, filename):
    root, view = service(tmp_path)
    observe(root, view, 0)
    assert observe(root, view, .5)["state"] == "armed"
    (root / filename).write_text("[]")
    result = view.observe(dict(state="fresh"), now=BASE+1, elapsed=1)
    assert result["state"] == "unknown"

def test_same_context_operations_status_does_not_cross_archive_generation(tmp_path):
    root, view = service(tmp_path)
    old = {"continuity_generation": 0}
    observe(root, view, 0, source_change=old, telemetry_change=old)
    assert observe(root, view, .5, source_change=old, telemetry_change=old)["state"] == "armed"
    changed = observe(root, view, 1, source_change=old,
                      telemetry_change={"continuity_generation": 1})
    assert changed["state"] == "unknown"
    assert changed["reason"] == "operations_source_continuity_changed"
    assert observe(root, view, 1.5, source_change={"continuity_generation": 1},
                   telemetry_change={"continuity_generation": 1})["state"] == "armed"
