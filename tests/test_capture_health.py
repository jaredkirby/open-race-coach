import json
from datetime import UTC, datetime, timedelta

import pytest

from simcoach.cli import capture_health

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def status(*, samples=10, offset_s=5.0, updated_at=NOW, **fields):
    return {
        "schema_version": 1,
        "status": "running",
        "coverage": "available",
        "latest_is_current": True,
        "updated_at": updated_at.isoformat(),
        "samples": samples,
        "offset_s": offset_s,
        "target_hz": 60,
        "latest": {"synthetic": "excluded from report"},
        "error": None,
    } | fields


def observe(tmp_path, first, second, **kwargs):
    times = iter((NOW, NOW + timedelta(seconds=1.2)))
    samples = iter((first, second))

    def read(path):
        assert path == tmp_path / "status.json"
        sample = next(samples)
        if isinstance(sample, Exception):
            raise sample
        return sample

    def sleep(seconds):
        assert seconds == 1.2

    return capture_health.observe(
        tmp_path, read=read, clock=lambda: next(times), sleep=sleep, **kwargs
    )


@pytest.mark.parametrize("advance_stamp", [True, False])
def test_healthy(tmp_path, advance_stamp):
    second_stamp = NOW + timedelta(seconds=1) if advance_stamp else NOW
    report = observe(
        tmp_path, status(), status(samples=11, offset_s=6, updated_at=second_stamp)
    )
    assert report == {
        "health": "healthy",
        "reasons": [],
        "observed_at": (NOW + timedelta(seconds=1.2)).isoformat(),
        "expires_at": (second_stamp + timedelta(seconds=3)).isoformat(),
        "sample_advance": {"count": True, "offset": True},
        "freshness": {"max_age_s": 3.0, "first_age_s": 0, "second_age_s": 0.2
                      if advance_stamp else 1.2},
    }


@pytest.mark.parametrize(
    ("first", "health", "code"),
    [
        (status(updated_at=NOW - timedelta(seconds=3)), "degraded", "stale_heartbeat"),
        (status(updated_at=NOW + timedelta(microseconds=1)), "degraded", "future_heartbeat"),
        ({"schema_version": 1, "status": "failed"}, "degraded", "capture_failed"),
        (status(status="complete", coverage="stopped", latest_is_current=False),
         "degraded", "capture_stopped"),
        ({"schema_version": 1, "status": "stopped"}, "degraded", "capture_stopped"),
        (status(coverage="mapping_unavailable"), "degraded", "coverage_mapping_unavailable"),
        (status(coverage="untrusted private text"), "degraded", "coverage_unrecognized"),
        (status(latest_is_current=False), "degraded", "latest_not_current"),
        (status(schema_version=2), "unknown", "unsupported_schema_version"),
        (status(schema_version=True), "unknown", "invalid_schema_version"),
        (status(status=[]), "unknown", "invalid_capture_status"),
        (status(samples=True), "unknown", "invalid_sample_count"),
        (status(samples=float("inf")), "unknown", "invalid_sample_count"),
        (status(offset_s=True), "unknown", "invalid_offset"),
        (status(offset_s=float("nan")), "unknown", "invalid_offset"),
        (status(offset_s=10**400), "unknown", "invalid_offset"),
        (status(latest_is_current=1), "unknown", "invalid_latest_is_current"),
        (status(coverage=[]), "unknown", "invalid_coverage"),
        (status(latest=None), "unknown", "missing_sample_evidence"),
        (status() | {"updated_at": float("inf")}, "unknown", "invalid_updated_at"),
        (status() | {"updated_at": "2026-01-01T11:00:00-01:00"},
         "unknown", "invalid_updated_at"),
        (status() | {"updated_at": "2026-02-30T11:00:00Z"}, "unknown", "invalid_updated_at"),
        ([], "unknown", "status_not_object"),
        ({}, "unknown", "invalid_schema_version"),
        (FileNotFoundError(), "unknown", "status_missing"),
    ],
)
def test_first_sample_must_qualify(tmp_path, first, health, code):
    report = observe(tmp_path, first, status(samples=11, offset_s=6))
    assert report["health"] == health
    assert {"sample": 1, "code": code} in report["reasons"]
    assert report["expires_at"] == report["observed_at"]


@pytest.mark.parametrize(
    ("second", "code"),
    [
        (status(offset_s=6), "sample_count_stalled"),
        (status(samples=11), "offset_stalled"),
        (status(samples=9, offset_s=6), "sample_count_regressed"),
        (status(samples=11, offset_s=4), "offset_regressed"),
        (status(samples=11, offset_s=6, updated_at=NOW - timedelta(seconds=0.1)),
         "heartbeat_regressed"),
        (status(samples=11, offset_s=6, updated_at=NOW - timedelta(seconds=2)),
         "stale_heartbeat"),
    ],
)
def test_stalls_regressions_and_second_sample_freshness(tmp_path, second, code):
    report = observe(tmp_path, status(), second)
    assert report["health"] == "degraded"
    assert {"sample": 2, "code": code} in report["reasons"]


@pytest.mark.parametrize("failure", [PermissionError("private path"), FileNotFoundError()])
def test_second_read_failure(tmp_path, failure):
    report = observe(tmp_path, status(), failure)
    code = "status_missing" if isinstance(failure, FileNotFoundError) else "status_unreadable"
    assert report["health"] == "unknown"
    assert report["reasons"] == [{"sample": 2, "code": code}]
    assert "private path" not in json.dumps(report)


@pytest.mark.parametrize(("contents", "code"), [
    (b'{"schema_version":1,', "status_malformed"),
    (b'{"schema_version":1,"offset_s":NaN}', "status_malformed"),
    (b"\xff", "status_malformed"),
    (b'{"schema_version":1,"status":"running"}', "invalid_updated_at"),
])
def test_partial_file(tmp_path, contents, code):
    path = tmp_path / "status.json"
    path.write_bytes(contents)
    times = iter((NOW, NOW + timedelta(seconds=1.2)))
    reads = iter((status(), None))

    def read(path):
        first = next(reads)
        return capture_health._read_status(path) if first is None else first

    report = capture_health.observe(
        tmp_path, read=read, clock=lambda: next(times), sleep=lambda _: None
    )
    assert report["health"] == "unknown"
    assert report["reasons"] == [{"sample": 2, "code": code}]
    assert path.read_bytes() == contents


@pytest.mark.parametrize(
    "options",
    [
        ["--interval=0"], ["--interval=-1"], ["--interval=nan"], ["--interval=inf"],
        ["--interval=10.1"], ["--max-age=0"], ["--max-age=-1"], ["--max-age=nan"],
        ["--max-age=inf"], ["--run", "another"],
    ],
)
def test_cli_usage_errors(tmp_path, options):
    with pytest.raises(SystemExit) as exc:
        capture_health.main(["--run", str(tmp_path), *options])
    assert exc.value.code == 2


def test_cli_reads_only_status_and_emits_one_private_report(tmp_path, capsys):
    path = tmp_path / "status.json"
    record = status(
        status="failed",
        error="synthetic arbitrary exception containing a path",
        updated_at=NOW,
    )
    payload = json.dumps(record).encode()
    path.write_bytes(payload)
    before = path.stat()
    assert capture_health.main(["--run", str(tmp_path), "--interval", "0.001"]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["health"] == "degraded"
    assert report["reasons"] == [
        {"sample": 1, "code": "capture_failed"}, {"sample": 2, "code": "capture_failed"},
    ]
    assert captured.err == ""
    assert "synthetic" not in captured.out
    assert str(tmp_path) not in captured.out
    assert path.read_bytes() == payload
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert list(tmp_path.iterdir()) == [path]


def test_cli_exit_zero_for_healthy(tmp_path, monkeypatch, capsys):
    report = observe(tmp_path, status(), status(samples=11, offset_s=6))
    monkeypatch.setattr(capture_health, "observe", lambda *_args, **_kwargs: report)
    assert capture_health.main(["--run", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == report

def test_two_healthy_statuses_cannot_demonstrate_progress_across_a_gap(tmp_path):
    first = status(continuity_generation=0)
    second = status(samples=11, offset_s=6, continuity_generation=1)
    report = observe(tmp_path, first, second)
    assert report["health"] == "degraded"
    assert report["reasons"] == [{"sample": 2, "code": "source_continuity_changed"}]


@pytest.mark.parametrize("value", [None, False, -1, 2**53, "1"])
def test_malformed_present_generation_is_unknown(tmp_path, value):
    report = observe(tmp_path, status(), status(samples=11, offset_s=6,
                                                continuity_generation=value))
    assert report["health"] == "unknown"
    assert report["reasons"] == [{"sample": 2, "code": "invalid_continuity_generation"}]
