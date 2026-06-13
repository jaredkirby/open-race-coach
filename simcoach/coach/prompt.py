"""Coach prompt construction from deterministic analysis artifacts."""

from __future__ import annotations

import json
from typing import Any


def build_coach_prompt(
    session: dict[str, Any],
    analysis: dict[str, Any],
    selected_delta: dict[str, Any],
) -> str:
    """Build a compact prompt containing no raw telemetry."""

    evidence = {
        "track_raw": session["track_raw"],
        "track": session["track"],
        "car_raw": session["car_raw"],
        "car": session["car"],
        "recorded_session_id": session["recorded_session_id"],
        "reference_mode": analysis["reference_mode"],
        "reference_lap_id": analysis["reference_lap_id"],
        "analysis_status": selected_delta["analysis_status"],
        "comparison_lap_count": selected_delta["comparison_lap_count"],
        "selected_delta": prompt_selected_delta(selected_delta.get("selected_delta")),
        "corner_summaries": [
            prompt_corner_summary(summary) for summary in selected_delta.get("corner_summaries", [])
        ],
    }
    return "\n".join(
        [
            "You are refining a SIM-COACH deterministic Coach Report.",
            "Return JSON only. Do not return Markdown.",
            "Do not choose a different analysis_status, Corner Segment, cause, or reference.",
            "Use concrete driver language. Do not invent corner names or official turn numbers.",
            "If the outcome is non-reportable, set corner_segment_id and instruction to null.",
            "",
            "Required JSON keys:",
            "analysis_status, corner_segment_id, instruction, why, confidence_note",
            "",
            "Deterministic evidence:",
            json.dumps(evidence, indent=2, sort_keys=True),
        ]
    )


COACH_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "analysis_status",
        "corner_segment_id",
        "instruction",
        "why",
        "confidence_note",
    ],
    "properties": {
        "analysis_status": {
            "type": "string",
            "enum": [
                "reportable",
                "consistent",
                "inconsistent",
                "insufficient_data",
                "no_single_dominant_issue",
            ],
        },
        "corner_segment_id": {"type": ["string", "null"]},
        "instruction": {"type": ["string", "null"]},
        "why": {"type": "string"},
        "confidence_note": {"type": "string"},
    },
}


def prompt_selected_delta(delta: dict[str, Any] | None) -> dict[str, Any] | None:
    if delta is None:
        return None
    allowed = {
        "corner_segment_id",
        "dominant_cause",
        "comparison_lap_count",
        "median_corner_loss_s",
        "robust_noise_s",
        "cause_metric",
        "lap_dist_m_source",
        "runner_up_margin_s",
    }
    prompt_delta = {key: delta[key] for key in allowed if key in delta}
    if "cause_metric" in prompt_delta:
        prompt_delta["cause_metric"] = prompt_cause_metric(prompt_delta["cause_metric"])
    return prompt_delta


def prompt_cause_metric(metric: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "metric",
        "unit",
        "reference_value",
        "comparison_median",
        "signed_delta",
        "bad_direction_delta",
        "lap_dist_m_source",
    }
    return {key: metric[key] for key in allowed if key in metric}


def prompt_corner_summary(summary: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "corner_segment_id",
        "classification",
        "median_corner_loss_s",
        "robust_noise_s",
        "dominant_cause",
        "dominant_cause_lap_fraction",
        "reason",
    }
    return {key: summary[key] for key in allowed if key in summary}
