import base64
import gzip
import hashlib
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

from simcoach.live import archive_coverage as coverage

BASE = 1700000000


def stamp(offset):
    return datetime.fromtimestamp(BASE + offset, UTC).isoformat()


def sample(offset, sequence, *, wall=None):
    raw = bytearray(20700)
    struct.pack_into("<I", raw, 0, 14)
    struct.pack_into("<I", raw, 7320, sequence)
    return dict(
        schema_version=1,
        at=stamp(offset if wall is None else wall),
        offset_s=offset,
        sequence=sequence,
        struct_size_bytes=20700,
        summary=dict(mVersion=14, mSequenceNumber=sequence),
        raw_base64=base64.b64encode(raw).decode(),
    )


def archive(tmp_path):
    run = tmp_path / "archive"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                expected_version=14,
                struct_size_bytes=20700,
                sequence_offset=7320,
                started_at=stamp(0),
            )
        )
    )
    (run / "segments.jsonl").write_text("")
    return run


def segment(run, number, records, *, manifest=True, **overrides):
    name = f"samples-{number:06d}.jsonl.gz"
    raw = gzip.compress("".join(json.dumps(row) + "\n" for row in records).encode(), mtime=0)
    (run / name).write_bytes(raw)
    entry = dict(
        path=name,
        samples=len(records),
        started_offset_s=records[0]["offset_s"],
        sha256=hashlib.sha256(raw).hexdigest(),
        closed_at=stamp(records[-1]["offset_s"] + 1),
    )
    entry.update(overrides)
    if manifest:
        with (run / "segments.jsonl").open("a") as handle:
            handle.write(json.dumps(entry) + "\n")
    return name


def audit(run, names, start=0, end=0.2, **kwargs):
    return coverage.analyze_archive_coverage(
        run, start_at=stamp(start), end_at=stamp(end), segment_names=names, **kwargs
    )


def test_fixed_window_clips_verified_union_without_baseline_extrapolation(tmp_path):
    run = archive(tmp_path)
    first = segment(run, 1, [sample(0, 2), sample(0.04, 4), sample(0.08, 6)])
    second = segment(run, 2, [sample(0.12, 8), sample(0.16, 10)])
    result = audit(run, [second, first], start=0.02, end=0.2)
    assert result["integrity_result"] == "PASS" and result["metric_known"]
    assert result["verified_sample_span_s"] == pytest.approx(0.14, abs=1e-6)
    assert result["unverified_s"] == pytest.approx(0.04, abs=1e-6)
    assert result["requested_segments"] == [second, first]
    assert result["boundaries"][0]["bridge_allowed"]


def test_reported_break_blocks_even_short_bridge_between_verified_segments(tmp_path):
    run = archive(tmp_path)
    first = segment(run, 1, [sample(0, 2), sample(.03, 4)], continuity_break_after=True)
    following = segment(run, 2, [sample(.06, 6), sample(.09, 8)])
    result = audit(run, [first, following], end=.09)
    assert result["integrity_result"] == "PASS" and result["metric_known"]
    assert result["verified_sample_span_s"] == pytest.approx(.06, abs=1e-6)
    assert result["boundaries"] == [dict(
        before=first, after=following, bridge_allowed=False,
        reason="collector_reported_coverage_break")]


def test_repeated_sequence_is_valid_bytes_not_advancing_updates(tmp_path):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(0, 2), sample(0.04, 2), sample(0.08, 4), sample(0.12, 2)])
    result = audit(run, [name], end=0.12)
    assert result["verified_sample_span_s"] == pytest.approx(0.12, abs=1e-6)
    assert result["advancing_sequence_span_s"] == pytest.approx(0.04, abs=1e-6)
    assert result["repeated_sequence_samples"] == 1
    assert result["sequence_regression_intervals"] == 1


def test_omitted_and_orphan_segments_break_bridges(tmp_path):
    run = archive(tmp_path)
    a = segment(run, 1, [sample(0, 2), sample(0.02, 4)])
    middle = segment(run, 2, [sample(0.04, 6), sample(0.06, 8)])
    c = segment(run, 3, [sample(0.08, 10), sample(0.1, 12)])
    result = audit(run, [a, c], end=0.1)
    assert result["verified_sample_span_s"] == pytest.approx(0.04, abs=1e-6)
    assert not result["boundaries"][0]["bridge_allowed"]
    orphan = segment(run, 4, [sample(0.12, 14)], manifest=False)
    result = audit(run, [a, middle, orphan])
    assert result["segments"][-1]["reason"] == "unmanifested_segment"
    assert result["integrity_result"] == "FAIL"


def test_missing_manifest_number_cannot_hide_orphan_bridge(tmp_path):
    run = archive(tmp_path)
    a = segment(run, 1, [sample(0, 2), sample(0.02, 4)])
    segment(run, 2, [sample(0.04, 6)], manifest=False)
    c = segment(run, 3, [sample(0.06, 8), sample(0.08, 10)])
    result = audit(run, [a, c], end=0.08)
    assert result["verified_sample_span_s"] == pytest.approx(0.04, abs=1e-6)
    assert not result["boundaries"][0]["bridge_allowed"]


@pytest.mark.parametrize(
    "damage", ["hash", "gzip", "raw_size", "raw_sequence", "odd", "count", "start"]
)
def test_corrupt_segments_never_contribute_spans_or_get_repaired(tmp_path, damage):
    run = archive(tmp_path)
    rows = [sample(0, 2), sample(0.04, 4)]
    overrides = {}
    if damage == "raw_size":
        rows[0]["raw_base64"] = base64.b64encode(b"short").decode()
    elif damage == "raw_sequence":
        rows[0]["sequence"] = 8
    elif damage == "odd":
        rows[0] = sample(0, 3)
    elif damage == "count":
        overrides["samples"] = 3
    elif damage == "start":
        overrides["started_offset_s"] = 0.01
    name = segment(run, 1, rows, **overrides)
    if damage in ("hash", "gzip"):
        (run / name).write_bytes(b"not gzip")
        if damage == "gzip":
            entry = json.loads((run / "segments.jsonl").read_text())
            entry["sha256"] = hashlib.sha256(b"not gzip").hexdigest()
            (run / "segments.jsonl").write_text(json.dumps(entry) + "\n")
    original = {path: path.read_bytes() for path in run.iterdir()}
    result = audit(run, [name])
    assert result["segments"][0]["result"] == "FAIL"
    assert result["verified_sample_span_s"] is None and not result["metric_known"]
    assert all(path.read_bytes() == content for path, content in original.items())


def test_corrupt_middle_segment_cannot_join_good_neighbors(tmp_path):
    run = archive(tmp_path)
    a = segment(run, 1, [sample(0, 2), sample(0.02, 4)])
    b = segment(run, 2, [sample(0.04, 6), sample(0.06, 8)])
    c = segment(run, 3, [sample(0.08, 10), sample(0.1, 12)])
    (run / b).write_bytes(b"broken")
    result = audit(run, [a, b, c], end=0.1)
    assert result["metric_known"] and result["integrity_result"] == "FAIL"
    assert result["verified_sample_span_s"] == pytest.approx(0.04, abs=1e-6)
    assert result["unverified_s"] == pytest.approx(0.06, abs=1e-6)


def test_cumulative_drift_invalidates_mapping_even_when_each_delta_is_small(tmp_path):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(i * 0.04, 2 + i * 2, wall=i * 0.05) for i in range(8)])
    result = audit(run, [name], end=0.4)
    assert (
        not result["metric_known"]
        and result["metric_unknown_reason"] == "time_mapping_discontinuous"
    )
    assert result["segments"][0]["reason"] == "global_utc_offset_alignment_changed"


def test_alignment_anchor_is_not_reset_after_omitted_segment(tmp_path):
    run = archive(tmp_path)
    a = segment(run, 1, [sample(0, 2), sample(0.02, 4)])
    segment(run, 2, [sample(0.04, 6), sample(0.06, 8)])
    c = segment(run, 3, [sample(0.08, 10, wall=0.2), sample(0.1, 12, wall=0.22)])
    assert audit(run, [a, c], end=0.3)["metric_unknown_reason"] == "time_mapping_discontinuous"


def test_long_gap_is_unverified_not_a_clock_mapping_failure(tmp_path):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(0, 2), sample(0.04, 4), sample(0.5, 6), sample(0.54, 8)])
    result = audit(run, [name], end=0.6)
    assert result["metric_known"] and result["over_gap_intervals"] == 1
    assert result["verified_sample_span_s"] == pytest.approx(0.08, abs=1e-6)
    assert result["unverified_s"] == pytest.approx(0.52, abs=1e-6)


@pytest.mark.parametrize(
    "names",
    [
        ["../samples-000001.jsonl.gz"],
        ["samples-000001.jsonl.gz.partial"],
        ["samples-000001.jsonl.gz", "samples-000001.jsonl.gz"],
    ],
)
def test_unsafe_partial_duplicate_names_refused_without_changes(tmp_path, names):
    run = archive(tmp_path)
    with pytest.raises(ValueError, match="explicit_unique"):
        audit(run, names)
    assert len(list(run.iterdir())) == 2


def test_limit_aborts_without_fabricated_zero_or_full_metric(tmp_path, monkeypatch):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(0, 2), sample(0.04, 4)])
    monkeypatch.setattr(coverage, "MAX_SAMPLES", 1)
    result = audit(run, [name])
    assert not result["scope_complete"] and not result["metric_known"]
    assert result["verified_sample_span_s"] is None and result["unverified_s"] is None
    assert result["segments"][0]["result"] == "NOT RUN"


def test_append_only_manifest_growth_outside_selection_is_allowed(tmp_path, monkeypatch):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(0, 2), sample(0.04, 4)])
    original = coverage._segment

    def append(*args):
        result = original(*args)
        segment(run, 2, [sample(0.08, 6)])
        return result

    monkeypatch.setattr(coverage, "_segment", append)
    result = audit(run, [name])
    assert result["metric_known"] and result["integrity_result"] == "PASS"
    assert (
        result["provenance"]["initial_manifest_sha256"]
        != result["provenance"]["final_manifest_sha256"]
    )


def test_changed_run_identity_invalidates_metric(tmp_path, monkeypatch):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(0, 2), sample(0.04, 4)])
    original = coverage._segment

    def change(*args):
        result = original(*args)
        metadata = json.loads((run / "run.json").read_text())
        metadata["pid"] = 123
        (run / "run.json").write_text(json.dumps(metadata))
        return result

    monkeypatch.setattr(coverage, "_segment", change)
    result = audit(run, [name])
    assert not result["metric_known"] and result["verified_sample_span_s"] is None


def test_new_writer_refuses_archive_and_existing_destination(tmp_path):
    run = archive(tmp_path)
    name = segment(run, 1, [sample(0, 2), sample(0.04, 4)])
    report = audit(run, [name])
    with pytest.raises(ValueError, match="inside_source"):
        coverage.write_archive_coverage_report(report, run / "audit")
    output = tmp_path / "result"
    result = coverage.write_archive_coverage_report(report, output)
    assert json.loads(Path(result["report_path"]).read_text()) == report
    with pytest.raises(FileExistsError):
        coverage.write_archive_coverage_report(report, output)
