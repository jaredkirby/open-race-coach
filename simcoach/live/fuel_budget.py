"""Offline lap fuel evidence from explicit closed AMS2 archive segments."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import math
import zlib
from collections import Counter
from dataclasses import asdict
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from simcoach.cli.telemetry_watch import write_status
from simcoach.live import archive_coverage as archive_io
from simcoach.live.supervisor import Context, FuelPlan, integer, number, strict_json


def decode(value, layout):
    """Read only the viewed slot; never return names or another participant's data."""
    page = layout.from_buffer_copy(base64.b64decode(value["raw_base64"], validate=True))
    index = integer(int(page.mViewedParticipantIndex), maximum=63)
    count = integer(int(page.mNumParticipants), minimum=1, maximum=64)
    if index >= count:
        raise ValueError("view_unavailable")
    part = page.mParticipantInfo[index]
    def label(raw):
        return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "strict")
    context = Context(label(page.mCarName), label(page.mTrackLocation),
                      label(page.mTrackVariation), int(page.mSessionState))
    if (not part.mIsActive or label(page.mCarNames[index]) != context.car
            or page.mRaceStates[index] != page.mRaceState
            or page.mPitModes[index] != page.mPitMode):
        raise ValueError("view_binding_inconsistent")
    capacity = number(float(page.mFuelCapacity), minimum=.001, maximum=10000)
    fraction = number(float(page.mFuelLevel), maximum=1)
    length = number(float(page.mTrackLength), minimum=1, maximum=100000)
    distance = number(float(part.mCurrentLapDistance), maximum=length+1)
    return dict(at=archive_io._utc(value["at"]), offset=number(value["offset_s"]),
                sequence=integer(value["sequence"]), context=asdict(context),
                index=index, count=count, capacity=capacity, litres=capacity*fraction,
                completed=int(part.mLapsCompleted), progress=min(1, distance/length),
                game=int(page.mGameState), race=int(page.mRaceState), pit=int(page.mPitMode),
                speed=number(float(page.mSpeed), maximum=1000),
                invalidated=bool(page.mLapInvalidated),
                slot_invalidated=bool(page.mLapsInvalidated[index]),
                current_lap=int(part.mCurrentLap), sector=int(part.mCurrentSector),
                sector_count=int(page.mNumSectors), current_time=float(page.mCurrentTime),
                distance=distance, length=length, position=tuple(part.mWorldPosition))


def _observed_invalidated_crossing(before, after, wall, offset, max_gap_s):
    if not (
        before["progress"] >= .98 and after["progress"] <= .02
        and before["completed"] == after["completed"]
        and before["current_lap"] == after["current_lap"] > 0
        and before["race"] == after["race"] == 2
        and before["count"] == after["count"]
        and after["sequence"] > before["sequence"]
        and abs(after["capacity"] - before["capacity"]) <= .001
        and -.001 <= before["litres"] - after["litres"] <= .05
        and before["invalidated"] and before["slot_invalidated"]
        and not after["invalidated"] and not after["slot_invalidated"]
        and 2 <= before["sector_count"] == after["sector_count"] <= 12
        and before["sector"] == before["sector_count"] - 1 and after["sector"] == 0
        and abs(before["length"] - after["length"]) <= .001
        and before["current_time"] == -1
        and 0 <= after["current_time"] <= max(wall, offset) + .05
        and 0 < min(wall, offset) <= max(wall, offset) <= min(.125, max_gap_s)
        and abs(wall - offset) <= .05
        and min(before["speed"], after["speed"]) >= 5
        and all(math.isfinite(value) for sample in (before, after) for value in sample["position"])
    ):
        return False
    travel = (before["speed"] + after["speed"]) * max(wall, offset) / 2
    displacement = math.dist(before["position"], after["position"])
    forward_along_track = before["length"] - before["distance"] + after["distance"]
    return (.2 * travel <= displacement <= 2 * travel + 1
            and 0 <= forward_along_track <= 2 * travel + 1)


class LapFuel:
    def __init__(self, context, index, max_gap_s):
        self.context, self.index, self.max_gap_s = asdict(context), index, max_gap_s
        self.previous = self.anchor = None
        self.prestart = None
        self.issues = set()
        self.invalidated = False
        self.rows = []
        self.start_boundary = None
        self.diagnostics = Counter()
        self.samples = 0
        self.progress_at = self.minimum_litres = None

    def close(self, end, reason=None, *, end_boundary=None):
        if self.anchor is None:
            return
        issues = self.issues | ({reason} if reason else set())
        burn = self.anchor["litres"] - end["litres"]
        if burn <= .001:
            issues.add("no_measurable_consumption")
        if end["at"] <= self.anchor["at"]:
            issues.add("nonpositive_duration")
        self.rows.append(dict(start_at=archive_io._stamp(self.anchor["at"]),
            end_at=archive_io._stamp(end["at"]), completed_start=self.anchor["completed"],
            completed_end=end["completed"], start_litres=self.anchor["litres"],
            end_litres=end["litres"], consumed_litres=burn, samples=self.samples,
            simulator_invalidated=self.invalidated, eligible=not issues, reasons=sorted(issues),
            start_boundary=self.start_boundary, end_boundary=end_boundary))
        self.anchor = None

    def gap(self, reason):
        self.diagnostics[reason] += 1
        if self.previous:
            self.close(self.previous, reason)
        self.previous = None
        self.prestart = None

    def add(self, row):
        if row["context"] != self.context or row["index"] != self.index:
            self.gap("context_or_view_changed")
            return
        if (self.context["session_state"] == 5 and row["game"] in (2, 4)
                and row["race"] == 1 and row["pit"] == 0 and row["speed"] <= 1
                and row["completed"] == 0 and row["progress"] <= .1):
            self.gap("stationary_prestart")
            self.prestart = row
            return
        if row["game"] != 2 or row["race"] not in (2, 3) or row["pit"] != 0:
            self.gap("not_active_track_running")
            return
        if self.previous is None:
            if row["race"] == 3:
                self.diagnostics["post_finish"] += 1
                return
            pre = self.prestart
            start = (pre is not None and row["completed"] == 0 and row["progress"] <= .1
                     and row["speed"] <= 1 and row["count"] == pre["count"]
                     and abs(row["capacity"]-pre["capacity"]) <= .001
                     and row["sequence"] > pre["sequence"]
                     and 0 <= row["at"]-pre["at"] <= self.max_gap_s
                     and 0 <= row["offset"]-pre["offset"] <= self.max_gap_s
                     and -.001 <= pre["litres"]-row["litres"] <= .05)
            self.anchor, self.previous = pre if start else row, row
            self.prestart = None
            self.progress_at, self.minimum_litres = row["at"], row["litres"]
            self.issues = set() if start else {"partial_start"}
            self.start_boundary = "stationary_grid_to_green" if start else None
            self.invalidated = row["invalidated"] or bool(start and pre["invalidated"])
            self.samples = 2 if start else 1
            return
        prev = self.previous
        self.samples += 1
        self.invalidated |= row["invalidated"]
        wall, offset = row["at"]-prev["at"], row["offset"]-prev["offset"]
        if wall < 0 or offset < 0 or abs(wall-offset) > .05:
            self.issues.add("clock_discontinuity")
        if wall == 0 or offset == 0:
            self.diagnostics["coarse_timestamp_pair"] += 1
        if max(wall, offset) > self.max_gap_s:
            self.issues.add("source_gap")
        if row["sequence"] < prev["sequence"]:
            self.issues.add("sequence_regression")
        if row["at"] - self.progress_at > self.max_gap_s:
            self.issues.add("source_progress_gap")
        if row["sequence"] != prev["sequence"]:
            self.progress_at = row["at"]
        if row["sequence"] == prev["sequence"]:
            self.diagnostics["repeated_sequence"] += 1
            if any(row[k] != prev[k] for k in ("litres", "completed", "progress")):
                self.issues.add("changed_without_sequence_progress")
        if row["count"] != prev["count"] or abs(row["capacity"]-prev["capacity"]) > .001:
            self.issues.add("participant_or_capacity_changed")
        if row["litres"] > self.minimum_litres + .001:
            self.issues.add("fuel_increase")
        self.minimum_litres = min(self.minimum_litres, row["litres"])
        if _observed_invalidated_crossing(prev, row, wall, offset, self.max_gap_s):
            boundary = "observed_invalidated_crossing"
            self.diagnostics[boundary] += 1
        elif row["completed"] != prev["completed"]:
            boundary = ("completed_counter_increment" if row["completed"] == prev["completed"]+1
                        and prev["progress"] >= .8 and row["progress"] <= .2
                        else "completed_counter_discontinuity")
        else:
            boundary = None
        if boundary is not None:
            established = boundary != "completed_counter_discontinuity"
            if not established:
                self.issues.add("counter_or_boundary_discontinuity")
            self.close(row, end_boundary=boundary)
            self.anchor = row
            self.start_boundary = boundary if established else None
            self.minimum_litres = row["litres"]
            self.issues = set() if established else {"partial_start"}
            self.invalidated, self.samples = row["invalidated"], 1
        elif row["progress"] < prev["progress"]-.05:
            self.issues.add("distance_regression")
        self.previous = row
        if row["race"] == 3:
            self.close(row, "post_finish_fragment")
            self.previous = None

    def finish(self):
        if self.previous:
            self.close(self.previous, "partial_end")


def rate_estimate(rows, *, margin_fraction=.25, rounding_litres=.05):
    """Policy margin, not a statistical upper bound. Slow/invalid laps are retained."""
    number(margin_fraction, minimum=.1, maximum=1)
    number(rounding_litres, minimum=.001, maximum=1)
    eligible = [r["consumed_litres"] for r in rows if r["eligible"]]
    result = dict(eligible_laps=len(eligible), minimum_laps=3,
                  margin_fraction=margin_fraction, rounding_litres=rounding_litres,
                  max_observed_litres=max(eligible) if eligible else None,
                  litres_per_lap=None, status="insufficient_data")
    partial_reasons = {"partial_start", "partial_end", "archive_continuity_break"}
    partials = [r["consumed_litres"] for r in rows if not r["eligible"]
                and set(r.get("reasons", [])) <= partial_reasons]
    result["max_partial_litres"] = max(partials) if partials else None
    if eligible and partials and max(partials) > max(eligible):
        result["status"] = "unresolved_partial_consumption"
        return result
    if len(eligible) >= 3:
        step = Decimal(str(rounding_litres))
        padded = Decimal(str(max(eligible))) * (1 + Decimal(str(margin_fraction)))
        result.update(status="review_required", litres_per_lap=float(
            (padded / step).to_integral_value(rounding=ROUND_CEILING) * step))
    return result


def analyze_fuel_budget(archive, *, segment_names, start_at, end_at, context,
                        participant_index, max_gap_s=.25, margin_fraction=.25,
                        rounding_litres=.05):
    """Bounded raw-byte verification; no reads from live simulator mappings."""
    from simcoach.adapters.ams2 import SharedMemory

    start, end = archive_io._utc(start_at), archive_io._utc(end_at)
    if end <= start or not isinstance(context, Context):
        raise ValueError("invalid_window_or_context")
    integer(participant_index, maximum=63)
    number(max_gap_s, minimum=.02, maximum=1)
    rate_estimate([], margin_fraction=margin_fraction, rounding_litres=rounding_litres)
    if (not segment_names or len(segment_names) > archive_io.MAX_SEGMENTS
            or len(set(segment_names)) != len(segment_names)
            or any(not isinstance(n, str) or not archive_io.SEGMENT_NAME.fullmatch(n)
                   for n in segment_names)):
        raise ValueError("explicit_unique_closed_segments_required")
    archive = Path(archive).resolve(strict=True)
    source_id = archive_io._source(archive)
    entries, manifest_hash = archive_io._manifest(archive)
    selected = [e for e in entries if e["path"] in segment_names]
    if len(selected) != len(segment_names):
        raise ValueError("segment_not_manifested")
    report = dict(schema_version=1, archive_path=str(archive), source_id=source_id,
        manifest_sha256=manifest_hash, source_segments=selected, context=asdict(context),
        participant_index=participant_index, local_player_identity_verified=False,
        window=dict(start_at=start_at, end_at=end_at), max_gap_s=max_gap_s,
        integrity_result="NOT RUN", scope_complete=False, failures=[],
        scope="explicit selected segments; not full window coverage",
        live_validation="NOT ESTABLISHED", automatic_approval=False)
    evidence = LapFuel(context, participant_index, max_gap_s)
    budget = archive_io._Budget()
    previous_number = None
    previous_break = False
    verified_count = 0
    try:
        for entry in selected:
            segment_number = int(entry["path"][8:14])
            if previous_number is not None and segment_number != previous_number+1:
                evidence.gap("omitted_segment")
            elif previous_break:
                evidence.gap("archive_continuity_break")
            previous_number = segment_number
            previous_break = entry.get("continuity_break_after", False)
            compressed = archive_io._compressed(archive, entry["path"], budget)
            if hashlib.sha256(compressed).hexdigest() != entry["sha256"]:
                raise ValueError("compressed_sha256_mismatch")
            count, decoded, first_offset = 0, 0, None
            with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
                while True:
                    budget.check()
                    line = stream.readline(archive_io.MAX_RECORD_BYTES+1)
                    if not line:
                        break
                    count += 1
                    budget.samples += 1
                    decoded += len(line)
                    budget.decompressed += len(line)
                    if (len(line) > archive_io.MAX_RECORD_BYTES
                            or decoded > archive_io.MAX_SEGMENT_DECOMPRESSED_BYTES
                            or budget.decompressed > archive_io.MAX_DECOMPRESSED_BYTES
                            or budget.samples > archive_io.MAX_SAMPLES):
                        raise ValueError("sample_or_decompressed_byte_limit")
                    at, offset, _ = archive_io._record(line)
                    verified_count += 1
                    first_offset = offset if first_offset is None else first_offset
                    if not start <= at <= end:
                        continue
                    try:
                        evidence.add(decode(strict_json(line.decode("utf8")), SharedMemory))
                    except (ValueError, TypeError, KeyError) as exc:
                        evidence.gap(str(exc) if type(exc) is ValueError else "invalid_sample")
            if count != entry["samples"] or first_offset != entry["started_offset_s"]:
                raise ValueError("manifest_count_or_start_mismatch")
        if archive_io._source(archive) != source_id:
            raise ValueError("archive_identity_changed")
        after, _ = archive_io._manifest(archive)
        if [e for e in after if e["path"] in segment_names] != selected:
            raise ValueError("selected_manifest_changed")
        report.update(integrity_result="PASS", scope_complete=True)
    except (OSError, ValueError, KeyError, EOFError, zlib.error, archive_io._Limit) as exc:
        report.update(integrity_result="FAIL", failures=[dict(error_type=type(exc).__name__,
            reason=str(exc) if type(exc) in (ValueError, archive_io._Limit)
            else "archive_read_failed")])
    evidence.finish()
    estimate = rate_estimate(evidence.rows, margin_fraction=margin_fraction,
                             rounding_litres=rounding_litres)
    if not report["scope_complete"]:
        estimate.update(status="unavailable", litres_per_lap=None)
    report.update(laps=evidence.rows, diagnostics=dict(evidence.diagnostics), estimate=estimate,
                  verified_samples=verified_count, samples_examined=budget.samples)
    return report


def draft_plan(report, *, timed_laps, out_laps=1, in_laps=1, reserve_laps=2):
    if (report["integrity_result"] != "PASS" or not report["scope_complete"]
            or report["estimate"]["status"] != "review_required"):
        raise ValueError("usable_fuel_evidence_required")
    rate = report["estimate"]["litres_per_lap"]
    content = json.dumps(report, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    return FuelPlan(Context(**report["context"]), rate, timed_laps, reserve_laps,
        (1+reserve_laps)*rate,
        "Offline fuel evidence requires chief review; report content SHA256 " + digest,
        out_laps=out_laps, in_laps=in_laps)


def write_report(report, out):
    out = Path(out).resolve()
    archive = Path(report["archive_path"]).resolve()
    if out == archive or archive in out.parents or out in archive.parents:
        raise ValueError("report_must_be_separate_from_archive")
    out.mkdir(parents=True, exist_ok=False)
    write_status(out / "report.json", report)
    return out / "report.json"
