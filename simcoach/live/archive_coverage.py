"""Bounded offline evidence spans from explicitly selected closed raw segments."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import math
import os
import re
import struct
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

from simcoach.live.supervisor import strict_json

MAX_SEGMENTS = 16
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_SEGMENT_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_SEGMENT_DECOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
MAX_SAMPLES = 50000
MAX_AUDIT_SECONDS = 60
DELTA_TOLERANCE_S = 0.025
ALIGNMENT_TOLERANCE_S = 0.05
SEGMENT_NAME = re.compile(r"samples-[0-9]{6}\.jsonl\.gz")


class _Limit(Exception):
    pass


class _TimeIntegrity(Exception):
    pass


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _integer(value, *, minimum=0, maximum=2**32 - 1):
    return type(value) is int and minimum <= value <= maximum


def _utc(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset().total_seconds() != 0
        ):
            raise ValueError("timestamp_requires_utc")
        return value.timestamp()
    except (OSError, ValueError, OverflowError):
        raise ValueError("invalid_utc_timestamp") from None


def _stamp(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def _child(archive, name):
    path = archive / name
    if path.resolve().parent != archive:
        raise ValueError("child_resolves_outside_archive")
    return path


def _small(path):
    with path.open("rb") as handle:
        raw = handle.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise _Limit("metadata_byte_limit")
    return raw


def _source(archive):
    raw = _small(_child(archive, "run.json"))
    run = strict_json(raw.decode("utf-8"))
    if (
        not isinstance(run, dict)
        or not _integer(run.get("schema_version"), minimum=1, maximum=1)
        or not _integer(run.get("expected_version"), minimum=14, maximum=14)
        or not _integer(run.get("struct_size_bytes"), minimum=20700, maximum=20700)
        or not _integer(run.get("sequence_offset"), minimum=7320, maximum=7320)
    ):
        raise ValueError("unsupported_archive_identity_or_layout")
    _utc(run["started_at"])
    return hashlib.sha256(raw).hexdigest()


def _manifest(archive):
    raw = _small(_child(archive, "segments.jsonl"))
    if raw and not raw.endswith(b"\n"):
        raise ValueError("incomplete_manifest_line")
    rows, names = [], set()
    previous_number, previous_offset = 0, -1
    for line in raw.splitlines():
        row = strict_json(line.decode("utf-8"))
        if not isinstance(row, dict):
            raise ValueError("invalid_manifest_entry")
        name, digest = row.get("path"), row.get("sha256")
        if (
            not isinstance(name, str)
            or SEGMENT_NAME.fullmatch(name) is None
            or name in names
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not _integer(row.get("samples"), minimum=1)
            or not _number(row.get("started_offset_s"))
            or ("continuity_break_after" in row and type(row["continuity_break_after"]) is not bool)
        ):
            raise ValueError("invalid_or_duplicate_manifest_entry")
        number = int(name[8:14])
        if number <= previous_number or row["started_offset_s"] <= previous_offset:
            raise ValueError("manifest_order_invalid")
        _utc(row["closed_at"])
        rows.append(row)
        names.add(name)
        previous_number, previous_offset = number, row["started_offset_s"]
    return rows, hashlib.sha256(raw).hexdigest()


class _Budget:
    def __init__(self):
        self.started = time.perf_counter()
        self.compressed = self.decompressed = self.samples = 0

    def check(self):
        if time.perf_counter() - self.started >= MAX_AUDIT_SECONDS:
            raise _Limit("elapsed_work_limit")


def _record(raw):
    if not raw.endswith(b"\n"):
        raise ValueError("incomplete_sample_line")
    value = strict_json(raw.decode("utf-8"))
    if (
        not isinstance(value, dict)
        or not _integer(value.get("schema_version"), minimum=1, maximum=1)
        or not _integer(value.get("struct_size_bytes"), minimum=20700, maximum=20700)
        or not _integer(value.get("sequence"))
        or value["sequence"] % 2
        or not _number(value.get("offset_s"))
        or not isinstance(value.get("raw_base64"), str)
    ):
        raise ValueError("invalid_sample_metadata")
    page = base64.b64decode(value["raw_base64"], validate=True)
    if (
        len(page) != 20700
        or struct.unpack_from("<I", page)[0] != 14
        or struct.unpack_from("<I", page, 7320)[0] != value["sequence"]
    ):
        raise ValueError("raw_layout_or_sequence_mismatch")
    summary = value.get("summary")
    if (
        not isinstance(summary, dict)
        or not _integer(summary.get("mVersion"), minimum=14, maximum=14)
        or not _integer(summary.get("mSequenceNumber"))
        or summary["mSequenceNumber"] != value["sequence"]
    ):
        raise ValueError("summary_layout_or_sequence_mismatch")
    return _utc(value["at"]), float(value["offset_s"]), value["sequence"]


def _progress(before, after):
    wall, offset = after[0] - before[0], after[1] - before[1]
    if wall <= 0 or offset <= 0:
        raise _TimeIntegrity("sample_time_not_advancing")
    if abs(wall - offset) > DELTA_TOLERANCE_S:
        raise _TimeIntegrity("utc_offset_delta_disagrees")
    return wall, offset


def _compressed(archive, name, budget):
    budget.check()
    path = _child(archive, name)
    size = path.stat().st_size
    if size > MAX_SEGMENT_COMPRESSED_BYTES or budget.compressed + size > MAX_COMPRESSED_BYTES:
        raise _Limit("compressed_byte_limit")
    with path.open("rb") as handle:
        compressed = handle.read(MAX_SEGMENT_COMPRESSED_BYTES + 1)
    budget.compressed += len(compressed)
    if len(compressed) > MAX_SEGMENT_COMPRESSED_BYTES or budget.compressed > MAX_COMPRESSED_BYTES:
        raise _Limit("compressed_byte_limit")
    return compressed


def _segment(archive, entry, budget, anchor, evidence):
    compressed = _compressed(archive, entry["path"], budget)
    digest = hashlib.sha256(compressed).hexdigest()
    evidence.update(observed_sha256=digest, compressed_bytes=len(compressed))
    if digest != entry["sha256"]:
        raise ValueError("compressed_sha256_mismatch")
    samples, decoded_bytes, drift = [], 0, 0
    local_anchor = anchor
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as source:
        while True:
            budget.check()
            line = source.readline(MAX_RECORD_BYTES + 1)
            if not line:
                break
            decoded_bytes += len(line)
            budget.decompressed += len(line)
            budget.samples += 1
            if (
                len(line) > MAX_RECORD_BYTES
                or decoded_bytes > MAX_SEGMENT_DECOMPRESSED_BYTES
                or budget.decompressed > MAX_DECOMPRESSED_BYTES
                or budget.samples > MAX_SAMPLES
            ):
                raise _Limit("decompressed_record_or_sample_limit")
            sample = _record(line)
            if local_anchor is None:
                local_anchor = sample[0] - sample[1]
            deviation = abs((sample[0] - sample[1]) - local_anchor)
            drift = max(drift, deviation)
            evidence["max_alignment_deviation_s"] = drift
            if deviation > ALIGNMENT_TOLERANCE_S:
                raise _TimeIntegrity("global_utc_offset_alignment_changed")
            if samples:
                _progress(samples[-1], sample)
            samples.append(sample)
    if len(samples) != entry["samples"]:
        raise ValueError("manifest_sample_count_mismatch")
    if not samples or abs(samples[0][1] - entry["started_offset_s"]) > 1e-9:
        raise ValueError("manifest_start_offset_mismatch")
    return samples, local_anchor, drift, len(compressed)


def _union(intervals, start, end):
    clipped = sorted((max(a, start), min(b, end)) for a, b in intervals if a < end and b > start)
    merged = []
    for a, b in clipped:
        if merged and a <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(b, merged[-1][1])
        else:
            merged.append((a, b))
    return sum(b - a for a, b in merged)


def analyze_archive_coverage(archive, *, start_at, end_at, segment_names, max_gap_s=0.1):
    """Conservative verified spans in a fixed UTC window, never proven lost-data time."""
    start, end = _utc(start_at), _utc(end_at)
    if end <= start or not _number(max_gap_s) or not 0 < max_gap_s <= 1:
        raise ValueError("invalid_window_or_gap")
    if (
        not isinstance(segment_names, (list, tuple))
        or not segment_names
        or len(segment_names) > MAX_SEGMENTS
        or any(not isinstance(n, str) or SEGMENT_NAME.fullmatch(n) is None for n in segment_names)
        or len(set(segment_names)) != len(segment_names)
    ):
        raise ValueError("explicit_unique_closed_segment_names_required")
    archive = Path(archive).resolve()
    report = dict(
        schema_version=1,
        archive_path=str(archive),
        source_id=None,
        requested_window=dict(
            start_at=_stamp(start),
            end_at=_stamp(end),
            duration_s=end - start,
            basis="explicit UTC verification window, not seat time",
        ),
        requested_segments=list(segment_names),
        max_gap_s=max_gap_s,
        delta_tolerance_s=DELTA_TOLERANCE_S,
        alignment_tolerance_s=ALIGNMENT_TOLERANCE_S,
        limits=dict(
            max_segments=MAX_SEGMENTS,
            metadata_bytes=MAX_METADATA_BYTES,
            compressed_bytes=MAX_COMPRESSED_BYTES,
            decompressed_bytes=MAX_DECOMPRESSED_BYTES,
            segment_compressed_bytes=MAX_SEGMENT_COMPRESSED_BYTES,
            segment_decompressed_bytes=MAX_SEGMENT_DECOMPRESSED_BYTES,
            record_bytes=MAX_RECORD_BYTES,
            samples=MAX_SAMPLES,
            elapsed_work_s=MAX_AUDIT_SECONDS,
        ),
        scope_complete=False,
        integrity_result="NOT RUN",
        metric_known=False,
        metric_unknown_reason=None,
        verified_sample_span_s=None,
        unverified_s=None,
        advancing_sequence_span_s=None,
        segments=[],
        boundaries=[],
        diagnostics=[],
        samples_verified=0,
        samples_in_window=0,
        repeated_sequence_samples=0,
        sequence_regression_intervals=0,
        over_gap_intervals=0,
        provenance={},
        meaning=(
            "Unverified remainder is not proven lost samples, "
            "absent driver time or capture failure."
        ),
    )
    budget = _Budget()
    try:
        report["source_id"] = _source(archive)
        manifest, manifest_hash = _manifest(archive)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, _Limit) as exc:
        report["metric_unknown_reason"] = (
            str(exc) if type(exc) in (ValueError, _Limit) else type(exc).__name__
        )
        report["segments"] = [
            dict(path=name, result="NOT RUN", reason="source_metadata_unavailable")
            for name in segment_names
        ]
        return report
    report["provenance"] = dict(
        initial_manifest_sha256=manifest_hash,
        source_identity_basis="sha256 original run.json bytes",
    )
    by_name = {entry["path"]: (index, entry) for index, entry in enumerate(manifest)}
    ordered = sorted(segment_names, key=lambda name: by_name.get(name, (math.inf,))[0])
    verified, anchor, maximum_drift = {}, None, 0
    discontinuity, limited = False, False
    for name in ordered:
        item = dict(
            path=name, result="NOT RUN", reason=None, expected_sha256=None, verified_samples=0
        )
        report["segments"].append(item)
        if limited:
            item["reason"] = "audit_limit_already_reached"
            continue
        if name not in by_name:
            item.update(result="FAIL", reason="unmanifested_segment")
            continue
        index, entry = by_name[name]
        item["expected_sha256"] = entry["sha256"]
        try:
            samples, new_anchor, drift, size = _segment(archive, entry, budget, anchor, item)
            if verified:
                prior_samples = verified[max(verified)][1]
                _progress(prior_samples[-1], samples[0])
            anchor = new_anchor
            maximum_drift = max(maximum_drift, drift)
            verified[index] = name, samples
            item.update(
                result="PASS",
                verified_samples=len(samples),
                compressed_bytes=size,
                sha256=entry["sha256"],
                max_alignment_deviation_s=drift,
            )
        except _Limit as exc:
            limited = True
            item["reason"] = str(exc)
        except _TimeIntegrity as exc:
            discontinuity = True
            item.update(result="FAIL", reason=str(exc))
        except (
            OSError,
            EOFError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
            zlib.error,
        ) as exc:
            item.update(
                result="FAIL", reason=str(exc) if type(exc) is ValueError else type(exc).__name__
            )
    intervals, fresh_intervals = [], []

    def interval(before, after):
        wall, offset = _progress(before, after)
        if after[2] == before[2]:
            report["repeated_sequence_samples"] += 1
        elif after[2] < before[2]:
            report["sequence_regression_intervals"] += 1
        if max(wall, offset) <= max_gap_s:
            intervals.append((before[0], after[0]))
            if after[2] > before[2]:
                fresh_intervals.append((before[0], after[0]))
        else:
            report["over_gap_intervals"] += 1

    previous = None
    for index, (name, samples) in sorted(verified.items()):
        report["samples_verified"] += len(samples)
        report["samples_in_window"] += sum(start <= s[0] <= end for s in samples)
        for a, b in zip(samples, samples[1:], strict=False):
            interval(a, b)
        if previous:
            prior_index, prior_name, last = previous
            adjacent = index == prior_index + 1 and int(name[8:14]) == int(prior_name[8:14]) + 1
            reason = None
            if not adjacent:
                reason = "omitted_or_unverified_segment"
            elif manifest[prior_index].get("continuity_break_after"):
                reason = "collector_reported_coverage_break"
            report["boundaries"].append(
                dict(
                    before=prior_name,
                    after=name,
                    bridge_allowed=reason is None,
                    reason=reason,
                )
            )
            if reason is None:
                interval(last, samples[0])
        previous = index, name, samples[-1]
    stable = True
    try:
        final_manifest, final_hash = _manifest(archive)
        stable = (
            _source(archive) == report["source_id"] and final_manifest[: len(manifest)] == manifest
        )
        report["provenance"]["final_manifest_sha256"] = final_hash
        for item in report["segments"]:
            if item["result"] == "PASS":
                raw = _compressed(archive, item["path"], budget)
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    stable = False
                    item.update(result="FAIL", reason="segment_changed_during_audit")
    except _Limit as exc:
        limited = True
        stable = False
        report["diagnostics"].append(str(exc))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        stable = False
    report["scope_complete"] = not limited
    failed = any(s["result"] == "FAIL" for s in report["segments"])
    report["integrity_result"] = (
        "FAIL"
        if failed or not stable and not limited
        else "PASS"
        if all(s["result"] == "PASS" for s in report["segments"])
        else "NOT RUN"
    )
    report["provenance"].update(
        source_and_selected_bindings_stable=stable,
        alignment_anchor_utc_minus_offset_s=anchor,
        max_alignment_deviation_s=maximum_drift,
    )
    if not stable:
        report["metric_unknown_reason"] = "source_stability_not_established"
    elif discontinuity:
        report["metric_unknown_reason"] = "time_mapping_discontinuous"
    elif report["samples_verified"] < 2:
        report["metric_unknown_reason"] = "insufficient_verified_sample_evidence"
    else:
        span = _union(intervals, start, end)
        report.update(
            metric_known=True,
            verified_sample_span_s=span,
            unverified_s=max(0, end - start - span),
            advancing_sequence_span_s=_union(fresh_intervals, start, end),
        )
    report["work"] = dict(
        compressed_bytes_read=budget.compressed,
        decompressed_bytes_read=budget.decompressed,
        sample_lines_read=budget.samples,
    )
    return report


def write_archive_coverage_report(report, output_dir):
    """New private report only; refuse adding evidence inside the source archive."""
    destination = Path(output_dir).resolve()
    if destination.is_relative_to(Path(report["archive_path"]).resolve()):
        raise ValueError("report_destination_inside_source_archive")
    destination.mkdir(parents=True, exist_ok=False)
    path = destination / "report.json"
    partial = path.with_suffix(".json.partial")
    with partial.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    partial.rename(path)
    return dict(report_path=str(path), report=report)
