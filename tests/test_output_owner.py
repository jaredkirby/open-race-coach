"""Output exclusion without opening a real audio device."""
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from simcoach.live.fuel_radio import _StartGuard
from simcoach.live.output_owner import WindowsOutputClaim
from simcoach.live.radio import Dispatcher, Transmission
from tests.test_radio import native_fake, wav


class Kernel:
    def __init__(self):
        self.handles = {}
        self.next_handle = 1
        self.error = 0
        self.fail_create = self.fail_close = False

    def CreateMutexW(self, security, initial_owner, name):
        assert security is None and initial_owner is False
        if self.fail_create:
            self.error = 5
            return None
        self.error = 183 if name in self.handles.values() else 0
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = name
        return handle

    def CloseHandle(self, handle):
        if self.fail_close:
            self.error = 6
            return False
        del self.handles[handle]
        return True

    def claim(self):
        return WindowsOutputClaim(kernel=self, last_error=lambda: self.error)


def transport(kernel):
    value = native_fake()
    value._claim = kernel.claim()
    return value


def test_same_thread_second_claim_is_denied_not_recursive():
    kernel = Kernel()
    first, second = kernel.claim(), kernel.claim()
    first.acquire()
    with pytest.raises(RuntimeError, match="already_owned"):
        second.acquire()
    assert len(kernel.handles) == 1 and second.state == "unavailable"
    first.close()
    with pytest.raises(RuntimeError, match="not_reusable"):
        second.acquire()  # Failed owner cannot silently retry later.
    next_claim = kernel.claim()
    next_claim.acquire()
    next_claim.close()


def test_access_denial_never_grants_output():
    kernel = Kernel()
    kernel.fail_create = True
    claim = kernel.claim()
    with pytest.raises(RuntimeError, match="create_failed_5"):
        claim.acquire()
    assert claim.state == "unavailable" and not kernel.handles


def test_failed_duplicate_handle_cleanup_is_retained_for_explicit_close():
    kernel = Kernel()
    first, second = kernel.claim(), kernel.claim()
    first.acquire()
    kernel.fail_close = True
    with pytest.raises(RuntimeError, match="close_failed"):
        second.acquire()
    assert second.handle and second.state == "unavailable"
    kernel.fail_close = False
    first.close()
    with pytest.raises(RuntimeError, match="already_owned"):
        kernel.claim().acquire()
    second.close()
    assert not kernel.handles


@pytest.mark.parametrize("failure", ["waveOutReset", "waveOutUnprepareHeader", "waveOutClose"])
def test_unconfirmed_native_stop_retains_output_claim(tmp_path, failure):
    kernel = Kernel()
    first, second = transport(kernel), transport(kernel)
    source = wav(tmp_path / "voice.wav")
    first.start(source, can_start=lambda: True)
    first._dll.fail = failure
    with pytest.raises(RuntimeError):
        first.close()
    assert first.ownership["state"] == "held"
    with pytest.raises(RuntimeError, match="already_owned"):
        second.start(source, can_start=lambda: True)
    assert not second._dll.calls
    first._dll.fail = None
    first.close()
    assert first.ownership["state"] == "closed" and not kernel.handles


def test_finished_and_muted_output_holds_claim_until_explicit_close(tmp_path):
    kernel = Kernel()
    first = transport(kernel)
    source = wav(tmp_path / "voice.wav")
    first.start(source, can_start=lambda: True)
    first._dll.header.flags |= 1
    assert first.poll() == "played"
    first.stop()
    assert first.ownership["state"] == "held"
    with pytest.raises(RuntimeError, match="already_owned"):
        transport(kernel).start(source, can_start=lambda: True)
    first.close()
    with pytest.raises(RuntimeError, match="closed"):
        first.start(source, can_start=lambda: True)


def test_claim_release_failure_keeps_dispatcher_journal_owned(tmp_path):
    kernel = Kernel()
    native = transport(kernel)
    dispatcher = Dispatcher(tmp_path / "radio.jsonl", native, clock=lambda: 1000,
                            evidence_valid=lambda _: True)
    source = wav(tmp_path / "voice.wav")
    native.start(source, can_start=lambda: True)
    kernel.fail_close = True
    with pytest.raises(RuntimeError, match="claim_close_failed"):
        dispatcher.close()
    assert not dispatcher.closed and native.ownership["state"] == "held"
    with pytest.raises(RuntimeError, match="owns this journal"):
        Dispatcher(tmp_path / "radio.jsonl", transport(kernel), clock=lambda: 1000,
                   evidence_valid=lambda _: True)
    kernel.fail_close = False
    dispatcher.close()
    assert not kernel.handles


def test_different_journals_cannot_overlap_or_replay_prior_message(tmp_path):
    kernel = Kernel()
    first, second = transport(kernel), transport(kernel)
    one = Dispatcher(tmp_path / "one.jsonl", first, clock=lambda: 1000,
                     evidence_valid=lambda _: True)
    assert first.ownership["state"] == "held" and not first._dll.calls
    message = Transmission("one", "event", "evidence", str(wav(tmp_path / "voice.wav")),
                           90, 999, 1005, False)
    try:
        one.submit(message)
        one.tick()
        with pytest.raises(RuntimeError, match="already_owned"):
            Dispatcher(tmp_path / "two.jsonl", second, clock=lambda: 1000,
                       evidence_valid=lambda _: True)
        assert not second._dll.calls
    finally:
        one.close()
    third = transport(kernel)
    reopened = Dispatcher(tmp_path / "one.jsonl", third, clock=lambda: 1000,
                          evidence_valid=lambda _: True)
    try:
        assert not reopened.submit(message)
        reopened.tick()
        assert not third._dll.calls
        reopened.submit(replace(message, transmission_id="new", dedup_key="new-event"))
        reopened.tick()
        assert third._dll.calls.count("waveOutWrite") == 1
    finally:
        reopened.close()


def test_fuel_guard_forwards_ownership_and_retains_it_on_failed_close(tmp_path):
    kernel = Kernel()
    native = transport(kernel)
    guard = _StartGuard(native, lambda: True, lambda: None)
    owner = Dispatcher(tmp_path / "fuel.jsonl", guard, clock=lambda: 1000,
                       evidence_valid=lambda _: True)
    assert guard.ownership["state"] == "held" and not native._dll.calls
    kernel.fail_close = True
    with pytest.raises(RuntimeError, match="claim_close_failed"):
        owner.close()
    assert not owner.closed and guard.ownership["state"] == "held"
    kernel.fail_close = False
    owner.close()
    assert guard.ownership["state"] == "closed" and not kernel.handles


@pytest.mark.skipif(os.name != "nt", reason="Windows named kernel object")
def test_thread_exit_does_not_release_living_process_output():
    name = "Local\\ORC.Test.Output." + uuid4().hex
    claim = WindowsOutputClaim(name=name)
    errors = []

    def acquire():
        try:
            claim.acquire()
        except Exception as error:
            errors.append(error)

    owner = threading.Thread(target=acquire)
    owner.start()
    owner.join(timeout=5)
    try:
        assert not owner.is_alive() and not errors and claim.state == "held"
        with pytest.raises(RuntimeError, match="already_owned"):
            WindowsOutputClaim(name=name).acquire()
    finally:
        claim.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden test-child process")
def test_process_exit_releases_claim_without_stale_file_or_audio(tmp_path):
    name = "Local\\ORC.Test.Output." + uuid4().hex
    wav(tmp_path / "voice.wav")
    script = """
import os, sys, time
from pathlib import Path
from simcoach.live.output_owner import WindowsOutputClaim
from simcoach.live.radio import Dispatcher, Transmission
from tests.test_radio import native_fake
root = Path(sys.argv[2])
transport = native_fake()  # Fake WinMM; never opens an audio device.
transport._claim = WindowsOutputClaim(name=sys.argv[1])
owner = Dispatcher(root/'radio.jsonl', transport, clock=lambda:1000,
                   evidence_valid=lambda _:True)
message = Transmission('crash-message','crash-event','test',str(root/'voice.wav'),
                       90,999,1005,False)
owner.submit(message)
owner.tick()
assert owner.states['crash-message']=='playing'
(root/'ready').touch()
deadline = time.perf_counter()+15
while time.perf_counter()<deadline:
    if (root/'exit-test-only').exists(): os._exit(7)
    time.sleep(.02)
os._exit(8)
"""
    child = subprocess.Popen([sys.executable, "-c", script, name, str(tmp_path)],
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.perf_counter() + 5
        while not (tmp_path / "ready").exists() and time.perf_counter() < deadline:
            assert child.poll() is None
            time.sleep(.02)
        assert (tmp_path / "ready").exists()
        with pytest.raises(RuntimeError, match="already_owned"):
            WindowsOutputClaim(name=name).acquire()
        (tmp_path / "exit-test-only").touch()
        assert child.wait(timeout=5) == 7
        replacement = native_fake()
        replacement._claim = WindowsOutputClaim(name=name)
        restarted = Dispatcher(tmp_path / "radio.jsonl", replacement, clock=lambda: 1000,
                               evidence_valid=lambda _: True)
        try:
            restarted.tick()
            assert restarted.states["crash-message"] == "interrupted"
            assert not replacement._dll.calls
            assert replacement.ownership["state"] == "held"
        finally:
            restarted.close()
    finally:
        if child.poll() is None:
            (tmp_path / "exit-test-only").touch()
            child.wait(timeout=20)
        child.communicate()
