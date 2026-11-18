"""Descriptive lap timing by recorded source; no cross-source consistency estimate."""
from __future__ import annotations

import math
import statistics

BASIS = {"derived_from_ticks": "captured_time", "sim": "simulator_time"}


def summarize_timing(laps):
    """Keep every record in counts; unknown timing never acquires a source by inference."""
    def stats(rows, *, comparable=True):
        valid = [row["lap_time_s"] for row in rows if row.get("valid") is True
                 and type(row.get("lap_time_s")) in (int, float)
                 and math.isfinite(row["lap_time_s"]) and row["lap_time_s"] > 0]
        values = valid if comparable else []
        return dict(valid_laps=len(valid), total_lap_records=len(rows),
                    best_s=min(values) if values else None,
                    median_s=statistics.median(values) if values else None,
                    spread_s=max(values)-min(values) if len(values) >= 2 else None,
                    population_stddev_s=statistics.pstdev(values) if len(values) >= 2 else None)

    groups = {}
    for row in laps:
        source = row.get("lap_time_source")
        source = source if isinstance(source, str) and source in BASIS else "unknown"
        groups.setdefault(source, []).append(row)
    basis = ("unknown" if not groups or "unknown" in groups else
             BASIS[next(iter(groups))] if len(groups) == 1 else "mixed")
    return dict(timing_basis=basis,
                statistics=stats(laps, comparable=basis in BASIS.values()),
                timing_groups={key: stats(rows, comparable=key != "unknown")
                               for key, rows in sorted(groups.items())})
