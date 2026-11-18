"""Independent operations against synthetic telemetry and fake audio only."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.fuel_radio import CHIEF_LOSS_PHRASE
from simcoach.live.operations_service import run_service
from simcoach.live.output_owner import WindowsOutputClaim
from simcoach.live.supervisor import FuelPlan
from simcoach.live.visit_recovery import inspect_visit
from tests.test_fuel_radio import BASE, CONTEXT, FakeTransport, make_phrase, status
from tests.test_visit_recovery import process
from tests.test_visit_watch import make_visit


class Rig:
    def __init__(self, tmp_path):
        self.visit = make_visit(tmp_path / "visit")
        write_status(self.visit / "status.json", dict(schema_version=1, status="running",
                                                       declared_presence=True))
        self.out = tmp_path / "service"
        self.seconds = 0.0
        self.dead = False
        self.deny = False
        self.litres = 8.0
        self.callback = lambda: None
        self.transport = FakeTransport(self.clock)
        self.heartbeat()
        self.phrase = make_phrase(tmp_path / "fuel")
        self.chief_phrase = make_phrase(tmp_path / "chief", text=CHIEF_LOSS_PHRASE)

    def clock(self):
        return BASE + self.seconds

    def heartbeat(self):
        write_status(self.visit / "heartbeat.json", dict(schema_version=1, at=self.clock()))

    def inspect(self, path, **kwargs):
        def probe(pid):
            if self.deny:
                return dict(process(pid), state="unknown", reason="open_process_error_5")
            return process(pid, "absent" if pid == 10 and self.dead else "running")
        return inspect_visit(path, probe=probe, **kwargs)

    def source(self):
        value = status(self.seconds, self.litres)
        sample = round(self.seconds * 60) + 1
        value.update(samples=sample)
        value["latest"]["mSequenceNumber"] = sample * 2
        return "source-a", value

    def sleep(self, seconds):
        self.seconds = round(self.seconds + seconds, 6)
        if not self.dead:
            self.heartbeat()
        self.callback()

    def request(self, name="a" * 32, *, service_id=None, at=None):
        identity = json.loads((self.out / "run.json").read_text())["service_id"]
        write_status(self.out / "requests" / f"{name}.json", dict(
            schema_version=1, service_id=identity if service_id is None else service_id,
            action="fuel", at=self.clock() if at is None else at,
            approval=dict(cockpit_litres=8, verified_at=self.clock(), source_id="source-a",
                          solo_player_confirmed=True, note="Synthetic approval only")))

    def mute(self, muted, identity="b" * 32):
        write_status(self.out / "mute.json", dict(schema_version=1, command_id=identity,
                                                 at=self.clock(), muted=muted))

    def run(self, **changes):
        options = dict(archive=self.visit, plan=FuelPlan(CONTEXT, .5, 6, 2, 2, "synthetic"),
                       present=True, duration=3, enable_audio=True, phrase=self.phrase,
                       selection=self.visit, chief_phrase=self.chief_phrase,
                       transport_factory=lambda: self.transport, source=self.source,
                       inspect=self.inspect, clock=self.clock, elapsed=lambda: self.seconds,
                       sleep=self.sleep)
        options.update(changes)
        return run_service(self.visit, self.out, **options)

    def events(self):
        with sqlite3.connect(self.out / "supervision.sqlite3") as connection:
            return [json.loads(r[0]) for r in connection.execute("SELECT payload FROM events")]

    def radio_events(self):
        return [json.loads(line) for line in (self.out / "radio.jsonl").read_text().splitlines()]


def test_approved_fuel_and_single_radio_continue_after_chief_exit(tmp_path):
    rig = Rig(tmp_path)
    def drive():
        if rig.seconds == .2:
            rig.request()
            rig.mute(False)
        if rig.seconds == .5:
            rig.dead = True
        if rig.seconds == 1.7:
            rig.litres = 2
    rig.callback = drive
    result = rig.run()
    rows = rig.radio_events()
    texts = [r["spec"]["transmission_id"] for r in rows if r["state"] == "generated"]
    assert len([t for t in texts if t.startswith("chief_loss:")]) == 1
    assert len([t for t in texts if t.startswith("fuel:")]) == 1
    assert result["chief_link"]["state"] == "lost"
    assert result["radio"]["chief_loss_delivery_state"] == "played"
    assert result["radio"]["delivery_state"] == "played"
    assert result["declared_presence"] is True and not result["driver_departure_inferred"]
    assert result["stop_reason"] == "duration_elapsed" and result["radio"]["closed"]


def test_contested_practice_read_defers_original_request_until_a_verified_pair(tmp_path):
    rig = Rig(tmp_path)
    receipt = rig.out / "receipts" / ("a" * 32 + ".json")

    def garage_source():
        identity, value = rig.source()
        value["continuity_generation"] = 3
        value["latest"].update(
            mRaceState=1, mPitMode=5, mSpeed=0,
            participant_observation=dict(schema_version=1, active=True, car=CONTEXT.car,
                                         index=0, count=1, race_state=1, pit_mode=5))
        if .3 <= rig.seconds < .6:
            value.update(coverage="unstable_copy", latest_is_current=False)
        return identity, value

    def confirm():
        if rig.seconds == .3:
            rig.request()
        if .4 <= rig.seconds <= .7:
            assert not receipt.exists()

    rig.callback = confirm
    result = rig.run(duration=1.1, source=garage_source)
    assert json.loads(receipt.read_bytes())["applied"] is True
    assert result["supervision"]["fuel_state"] == "armed"
    armed, = [e for e in rig.events() if e["event"] == "fuel_armed"]
    assert armed["verified_at"] == BASE+.3 and armed["at"] >= BASE+.7


@pytest.mark.parametrize("clocks", ["both", "wall_only", "monotonic_only"])
def test_observed_interruption_requires_new_fuel_approval(tmp_path, clocks):
    """Injected clock faults; this does not suspend or resume the Windows host."""
    rig = Rig(tmp_path)
    interrupted = [False]

    def elapsed():
        if not interrupted[0]:
            return rig.seconds
        return rig.seconds + (20 if clocks == "monotonic_only" else
                              -20 if clocks == "wall_only" else 0)

    def commands():
        if rig.seconds == .2:
            rig.request()
        if rig.seconds == .4 and not interrupted[0]:
            interrupted[0] = True
            if clocks != "monotonic_only":
                rig.seconds += 20
            rig.heartbeat()
        if rig.seconds == (.9 if clocks == "monotonic_only" else 20.9):
            write_status(rig.out / "stop", dict(at=rig.clock(), declaration="driver_left"))

    rig.callback = commands
    result = rig.run(duration=30, elapsed=elapsed)
    events = rig.events()
    assert sum(e["event"] == "fuel_armed" for e in events) == 1
    disarmed = [e for e in events if e["event"] == "fuel_disarmed"]
    assert len(disarmed) == 1
    assert disarmed[0]["reason"] == (
        "observation_clock_changed" if clocks == "wall_only" else "observation_gap")
    assert result["supervision"]["fuel_state"] == "unarmed"
    assert result["supervision"]["supervision_health"] == "healthy"
    assert not rig.transport.started


def test_disconnected_chief_cannot_approve_fuel_or_unmute(tmp_path):
    rig = Rig(tmp_path)
    def commands():
        if rig.seconds == .3:
            rig.dead = True
            rig.request()
            rig.mute(False)
    rig.callback = commands
    result = rig.run(duration=1)
    receipt = json.loads((rig.out / "receipts" / ("a" * 32 + ".json")).read_text())
    assert receipt["accepted"] is False and receipt["reason"] == "chief_not_connected"
    assert result["radio"]["muted"] is True and not rig.transport.started


def test_mute_still_works_without_chief_and_blocks_new_unmute(tmp_path):
    rig = Rig(tmp_path)
    def commands():
        if rig.seconds == .2:
            rig.mute(False)
        if rig.seconds == .4:
            rig.dead = True
        if rig.seconds == 1.0:
            rig.mute(True, "c" * 32)
        if rig.seconds == 1.1:
            rig.mute(False, "d" * 32)
    rig.callback = commands
    result = rig.run(duration=2)
    assert result["radio"]["muted"] is True
    rows = rig.radio_events()
    assert any(row["state"] == "interrupted" for row in rows)
    assert len(rig.transport.started) == 1


@pytest.mark.parametrize("change,reason", [
    ({"service_id": "other-instance"}, "service_binding_mismatch"),
    ({"at": BASE - 10}, "stale_request"),
])
def test_old_or_wrong_instance_approval_never_replays(tmp_path, change, reason):
    rig = Rig(tmp_path)
    rig.callback = lambda: rig.request(**change) if rig.seconds == .2 else None
    result = rig.run(duration=.6)
    receipt = json.loads((rig.out / "receipts" / ("a" * 32 + ".json")).read_text())
    assert receipt["reason"] == reason and not receipt["accepted"]
    assert result["supervision"]["fuel_state"] == "unarmed"


def test_denied_probe_does_not_speak_as_verified_disconnection(tmp_path):
    rig = Rig(tmp_path)
    def commands():
        if rig.seconds == .2:
            rig.mute(False)
        if rig.seconds == .4:
            rig.deny = True
    rig.callback = commands
    result = rig.run(duration=2)
    assert result["chief_link"]["state"] == "lost"
    assert result["chief_link"]["liveness"]["state"] == "unknown"
    assert not rig.transport.started


def test_reconnected_chief_cancels_warning_without_repeat(tmp_path):
    rig = Rig(tmp_path)
    def commands():
        if rig.seconds == .2:
            rig.mute(False)
        if rig.seconds == .4:
            rig.dead = True
        if rig.seconds == 1.0:
            rig.dead = False  # Same test identity; no actual process revival claimed.
            rig.heartbeat()
    rig.callback = commands
    result = rig.run(duration=2)
    assert result["chief_link"]["state"] == "connected"
    assert result["radio"]["chief_loss_delivery_state"] == "interrupted"
    assert len(rig.transport.started) == 1


@pytest.mark.parametrize("where", ["visit", "service"])
def test_explicit_departure_stops_service_without_capture_control(tmp_path, where):
    rig = Rig(tmp_path)
    def stop():
        if rig.seconds == .3:
            write_status((rig.visit if where == "visit" else rig.out) / "stop",
                         dict(at=rig.clock(), declaration="driver_left"))
    rig.callback = stop
    result = rig.run()
    assert result["declared_presence"] is False and result["stop_reason"] == "driver_left"
    assert not result["driver_departure_inferred"] and result["radio"]["closed"]
    assert not (rig.visit / "worker" / "stop").exists()


def test_finished_visit_is_refused_before_output_or_run_creation(tmp_path):
    rig = Rig(tmp_path)
    write_status(rig.visit / "status.json", dict(schema_version=1, status="complete"))
    with pytest.raises(ValueError, match="fresh_declared_visit_required"):
        rig.run()
    assert not rig.out.exists() and not rig.transport.started


def test_status_storage_failure_preserves_journal_and_closes_audio(tmp_path, monkeypatch):
    from simcoach.live import operations_service
    rig = Rig(tmp_path)
    original = operations_service.write_status
    def publish(path, value):
        if path == rig.out / "status.json":
            raise OSError("synthetic disk full")
        return original(path, value)
    monkeypatch.setattr(operations_service, "write_status", publish)
    with pytest.raises(OSError, match="disk full"):
        rig.run()
    assert rig.events()[-1]["event"] == "service_failed"
    assert not (rig.out / "status.json").exists() and not rig.transport.active


@pytest.mark.parametrize("interruption", ["deadline", "departure", "mute"])
def test_native_preparation_cannot_outlive_service_authority(tmp_path, interruption):
    rig = Rig(tmp_path)
    def commands():
        if rig.seconds == .2:
            rig.mute(False)
        if rig.seconds == .4:
            rig.dead = True
    def prepare():
        if interruption == "deadline":
            rig.seconds = 4
        elif interruption == "mute":
            rig.mute(True, identity="c" * 32)
        else:
            write_status(rig.out / "stop", dict(at=rig.clock(), declaration="driver_left"))
    rig.callback = commands
    rig.transport.prepare = prepare
    result = rig.run(duration=3)
    assert not rig.transport.started and result["radio"]["closed"]
    assert result["stop_reason"] == ("driver_left" if interruption == "departure"
                                     else "duration_elapsed")
    if interruption == "mute":
        receipt = json.loads((rig.out / "receipts" / ("c" * 32 + ".json")).read_text())
        assert receipt["applied"] and receipt["muted"]


def test_integrated_service_uses_visit_mute_and_receipts(tmp_path):
    rig = Rig(tmp_path)
    (rig.visit / "receipts").mkdir()
    def commands():
        if rig.seconds == .2:
            write_status(rig.visit / "mute.json", dict(schema_version=1, command_id="e" * 32,
                                                       at=rig.clock(), muted=False))
    rig.callback = commands
    rig.run(duration=.6, visit_controls=True)
    receipt = json.loads((rig.visit / "receipts" / ("e" * 32 + ".json")).read_text())
    assert receipt["accepted"] is True
    assert not list((rig.out / "receipts").glob("*.json"))


def service_child(root, claim_name):
    """Test subprocess entry: real OS isolation/claim, synthetic source/fake output."""
    started = time.time()
    class OwnedFake(FakeTransport):
        def __init__(self):
            super().__init__(time.time)
            self.claim = WindowsOutputClaim(name=claim_name)

        def prepare_owner(self):
            self.claim.acquire()

        @property
        def ownership(self):
            return self.claim.snapshot()

        def close(self):
            self.stop()
            self.claim.close()

    def source():
        now = time.time()
        seconds = now - started
        value = status(seconds, 2 if (root / "low-fuel-test-only").exists() else 8)
        sample = int(seconds * 10000) + 1
        stamp = datetime.fromtimestamp(now, UTC)
        # ISO microseconds must not round this synthetic source into the future.
        if stamp.timestamp() > now:
            stamp -= timedelta(microseconds=1)
        value.update(updated_at=stamp.isoformat(), samples=sample)
        value["latest"]["mSequenceNumber"] = sample * 2
        return "source-a", value

    result = run_service(
        root / "chief", root / "operations", archive=root,
        plan=FuelPlan(CONTEXT, .5, 6, 2, 2, "synthetic process test"), present=True,
        duration=12, enable_audio=True, phrase=root / "fuel", selection=root,
        chief_phrase=root / "chief-phrase", transport_factory=OwnedFake, source=source)
    assert result["radio"]["closed"]


@pytest.mark.skipif(os.name != "nt", reason="Windows isolated service and chief processes")
def test_real_service_survives_owned_chief_exit_and_keeps_fuel_radio(tmp_path):
    from pathlib import Path
    chief_dir = tmp_path / "chief"
    chief_dir.mkdir()
    make_phrase(tmp_path / "fuel")
    make_phrase(tmp_path / "chief-phrase", text=CHIEF_LOSS_PHRASE)
    claim_name = "Local\\ORC.Test.Service." + uuid4().hex
    chief_script = """
import os,sys,time
from pathlib import Path
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.visit_recovery import probe_process
root=Path(sys.argv[1])
write_status(root/'host-process.json',dict(schema_version=1,process=probe_process(os.getpid())))
write_status(root/'status.json',dict(schema_version=1,status='running',declared_presence=True))
deadline=time.perf_counter()+20
while time.perf_counter()<deadline:
    write_status(root/'heartbeat.json',dict(schema_version=1,at=time.time()))
    if (root/'exit-test-only').exists(): os._exit(7)
    time.sleep(.05)
os._exit(8)
"""
    service_script = """
from pathlib import Path
import sys
from tests.test_operations_service import service_child
service_child(Path(sys.argv[1]),sys.argv[2])
"""
    streams = [(tmp_path / name).open("wb") for name in ("chief.stderr", "service.stderr")]
    chief = subprocess.Popen([sys.executable, "-c", chief_script, str(chief_dir)],
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=streams[0])
    service = None
    def wait_value(path, predicate, child, timeout=5):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            assert child.poll() is None
            try:
                value = json.loads(Path(path).read_text())
                if predicate(value):
                    return value
            except (OSError, ValueError):
                pass
            time.sleep(.03)
        pytest.fail(f"condition not observed: {Path(path).name}")

    try:
        wait_value(chief_dir / "heartbeat.json", lambda _: True, chief)
        command = [sys.executable, "-c", service_script, str(tmp_path), claim_name]
        service = subprocess.Popen(command,
                                   creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdout=subprocess.DEVNULL, stderr=streams[1])
        out = tmp_path / "operations"
        state = wait_value(out / "status.json", lambda v: v["chief_link"]["state"] == "connected",
                           service)
        now = time.time()
        write_status(out / "requests" / ("a" * 32 + ".json"), dict(
            schema_version=1, service_id=state["service_id"], at=now, action="fuel",
            approval=dict(cockpit_litres=8, verified_at=now, source_id="source-a",
                          solo_player_confirmed=True, note="Synthetic process test only")))
        write_status(out / "mute.json", dict(schema_version=1, at=now, command_id="b" * 32,
                                             muted=False))
        wait_value(out / "status.json", lambda v: v["supervision"]["fuel_state"] == "armed"
                   and not v["radio"]["muted"], service)
        with pytest.raises(RuntimeError, match="already_owned"):
            WindowsOutputClaim(name=claim_name).acquire()
        (chief_dir / "exit-test-only").touch()
        assert chief.wait(timeout=5) == 7
        wait_value(out / "status.json",
                   lambda v: v["radio"]["chief_loss_delivery_state"] == "played", service)
        (tmp_path / "low-fuel-test-only").touch()
        last = wait_value(out / "status.json", lambda v: v["radio"]["delivery_state"] == "played",
                          service)
        assert last["declared_presence"] and not last["driver_departure_inferred"]
        assert last["radio"]["output_ownership"]["state"] == "held"
        write_status(out / "stop", dict(at=time.time(), declaration="driver_left"))
        assert service.wait(timeout=5) == 0
        final = json.loads((out / "status.json").read_text())
        assert final["status"] == "complete" and not final["declared_presence"]
        assert final["radio"]["output_ownership"]["state"] == "closed"
    finally:
        if chief.poll() is None:
            (chief_dir / "exit-test-only").touch()
            chief.wait(timeout=22)
        if service is not None and service.poll() is None:
            service.wait(timeout=15)  # It owns a bounded duration; never kill the rig.
        for stream in streams:
            stream.close()

@pytest.mark.parametrize("interrupt", ["source_lost", "driver_left", "duration_elapsed"])
def test_new_fuel_intent_rechecks_boundaries_before_applying(tmp_path, monkeypatch, interrupt):
    from simcoach.live import operations_service

    rig = Rig(tmp_path)
    original_read = operations_service._artifact
    original_source = rig.source
    injected = False

    def read(root, name):
        nonlocal injected
        result = original_read(root, name)
        if root == rig.out and name.startswith("requests/") and not injected:
            injected = True
            if interrupt == "driver_left":
                write_status(rig.visit / "stop", dict(at=rig.clock(), declaration="driver_left"))
            elif interrupt == "duration_elapsed":
                rig.seconds = 1.05  # Request is still under its own age limit.
                rig.heartbeat()
        return result

    def source():
        identity, packet = original_source()
        if injected and interrupt == "source_lost":
            packet.update(coverage="stale_mapping", latest_is_current=False)
        return identity, packet

    monkeypatch.setattr(operations_service, "_artifact", read)
    rig.callback = lambda: rig.request() if rig.seconds == .2 else None
    result = rig.run(duration=1, source=source)
    receipt = json.loads((rig.out / "receipts" / ("a" * 32 + ".json")).read_text())
    assert injected
    assert receipt["accepted"] is False and receipt["applied"] is False
    assert receipt["reason"] == {
        "source_lost": "capture_not_healthy",
        "driver_left": "driver_left",
        "duration_elapsed": "service_duration_elapsed",
    }[interrupt]
    assert "fuel_armed" not in [event["event"] for event in rig.events()]
    expected_stop = "driver_left" if interrupt == "driver_left" else "duration_elapsed"
    assert result["stop_reason"] == expected_stop


def test_departure_publication_lock_defers_new_fuel_intent_without_consuming_it(tmp_path):
    from simcoach.cli.race_watch import SourceLock

    rig = Rig(tmp_path)
    hold = None

    def commands():
        nonlocal hold
        if rig.seconds == .2:
            rig.request()
            hold = SourceLock(rig.visit / "request.lock")
        if rig.seconds == .4:
            assert not list((rig.out / "receipts").iterdir())
            hold.close()
            hold = None

    rig.callback = commands
    try:
        result = rig.run(duration=.9)
    finally:
        if hold:
            hold.close()
    receipt = json.loads((rig.out / "receipts" / ("a" * 32 + ".json")).read_text())
    assert receipt["accepted"] and receipt["decision_at"] >= BASE + .4
    assert sum(event["event"] == "fuel_armed" for event in rig.events()) == 1
    assert result["supervision"]["fuel_state"] == "armed"


def test_fuel_request_that_expires_during_source_recheck_stays_unapplied(tmp_path, monkeypatch):
    from simcoach.live import operations_service

    rig = Rig(tmp_path)
    original_read, original_source = operations_service._artifact, rig.source
    delay = False

    def read(root, name):
        nonlocal delay
        result = original_read(root, name)
        if root == rig.out and name.startswith("requests/"):
            delay = True
        return result

    def source():
        nonlocal delay
        if delay:
            rig.seconds = 3.4
            rig.heartbeat()
            delay = False
        return original_source()

    monkeypatch.setattr(operations_service, "_artifact", read)
    rig.callback = lambda: rig.request() if rig.seconds == .2 else None
    result = rig.run(duration=4, source=source)
    receipt = json.loads((rig.out / "receipts" / ("a" * 32 + ".json")).read_text())
    assert receipt["reason"] == "stale_request" and receipt["decision_at"] is None
    assert not receipt["accepted"] and result["supervision"]["fuel_state"] == "unarmed"
