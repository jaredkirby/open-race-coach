import errno

import pytest

from simcoach.live import json_read


def access_error(winerror=None):
    error = PermissionError(errno.EACCES, "synthetic access interruption")
    if winerror is not None:
        error.winerror = winerror
    return error


@pytest.mark.parametrize("winerror", [None, 5, 32])
def test_transient_access_failure_retries_and_retains_type(monkeypatch, winerror):
    monkeypatch.setattr(json_read.time, "sleep", lambda delay: None)
    calls, diagnostics = [], []

    def read():
        calls.append(1)
        if len(calls) < 3:
            raise access_error(winerror)
        return {"producer_time": 10}

    result = json_read.read_with_retry(read, role="test", diagnostic=diagnostics.append)
    assert result == {"producer_time": 10}
    assert len(calls) == 3 and diagnostics[0]["recovered"]
    assert diagnostics[0]["failures"][0]["error_type"] == "PermissionError"
    assert diagnostics[0]["failures"][0]["errno"] == errno.EACCES


def test_persistent_access_error_exhausts_without_cached_fallback(monkeypatch):
    monkeypatch.setattr(json_read.time, "sleep", lambda delay: None)
    calls, diagnostics = [], []

    def read():
        calls.append(1)
        raise access_error()

    with pytest.raises(PermissionError):
        json_read.read_with_retry(read, role="test", diagnostic=diagnostics.append)
    assert len(calls) == 3 and diagnostics[0]["recovered"] is False


@pytest.mark.parametrize("error", [FileNotFoundError(), ValueError("corrupt"),
                                  OSError(errno.EIO, "disk error")])
def test_missing_corrupt_and_other_io_are_not_retried(error):
    calls = []

    def read():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        json_read.read_with_retry(read, role="test")
    assert len(calls) == 1


def test_retry_deadline_does_not_reset(monkeypatch):
    times = iter([0.0, 0.001, 0.06])
    monkeypatch.setattr(json_read.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(json_read.time, "sleep", lambda delay: None)
    calls = []

    def read():
        calls.append(1)
        raise access_error()

    with pytest.raises(PermissionError):
        json_read.read_with_retry(read, role="test")
    assert len(calls) == 1
