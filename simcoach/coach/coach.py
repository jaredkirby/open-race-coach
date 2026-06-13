"""Coach refinement workflows for API and manual ChatGPT modes."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from simcoach.analysis.deltas import read_session_artifacts
from simcoach.analysis.run import render_coach_report, write_json
from simcoach.coach.prompt import COACH_RESPONSE_JSON_SCHEMA, build_coach_prompt
from simcoach.ingest.session import atomic_write_text, atomic_write_yaml
from simcoach.utils.llm_logger import get_logger

LOGGER = get_logger(__name__)
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_REASONING_EFFORT = "low"


class CoachRefinementError(RuntimeError):
    pass


def start_chatgpt_refinement(session_dir: Path, analysis_run: Path) -> None:
    LOGGER.info("[START] start_chatgpt_refinement | analysis_run=%s", analysis_run)
    session, _ticks, laps = read_session_artifacts(session_dir)
    analysis, selected_delta = load_analysis_context(analysis_run)
    validate_analysis_run_ownership(session, analysis)
    ensure_refinement_allowed(analysis, mode="chatgpt")

    prompt = build_coach_prompt(session, analysis, selected_delta)
    atomic_write_text(analysis_run / "coach_prompt.md", prompt)
    report = render_coach_report(
        session, analysis_run, laps, analysis["reference_mode"], selected_delta
    )
    atomic_write_text(analysis_run / "coach_report.md", report)
    atomic_write_text(session_dir / "coach_report.md", report)
    update_analysis_yaml(
        analysis_run,
        {
            "coach_refinement_mode": "chatgpt",
            "coach_refinement_status": "awaiting_response",
            "coach_refinement_error": None,
        },
    )
    print(f"{analysis_run / 'coach_prompt.md'} | paste into ChatGPT and request JSON only")
    LOGGER.info("[END] start_chatgpt_refinement | analysis_run=%s", analysis_run)


def import_chatgpt_response(session_dir: Path, analysis_run: Path, response_path: Path) -> None:
    LOGGER.info(
        "[START] import_chatgpt_response | analysis_run=%s response=%s", analysis_run, response_path
    )
    session, _ticks, laps = read_session_artifacts(session_dir)
    analysis, selected_delta = load_analysis_context(analysis_run)
    validate_analysis_run_ownership(session, analysis)
    if analysis.get("coach_refinement_mode") != "chatgpt" or analysis.get(
        "coach_refinement_status"
    ) not in {"awaiting_response", "invalid_response"}:
        raise CoachRefinementError("Analysis Run is not awaiting a ChatGPT response")

    try:
        response = parse_json_response(response_path.read_text(encoding="utf-8"))
        validate_coach_response(response, selected_delta)
    except Exception as exc:
        update_analysis_yaml(
            analysis_run,
            {
                "coach_refinement_status": "invalid_response",
                "coach_refinement_error": short_error(exc),
            },
        )
        raise

    wrapper = coach_response_wrapper(
        provider="chatgpt_manual",
        model="user_reported_or_unknown",
        reasoning_effort=None,
        response=response,
    )
    write_json(analysis_run / "coach_response.json", wrapper)
    report = render_refined_report(session, analysis_run, laps, analysis, selected_delta, response)
    atomic_write_text(analysis_run / "coach_report.md", report)
    atomic_write_text(session_dir / "coach_report.md", report)
    update_analysis_yaml(
        analysis_run,
        {
            "coach_response_schema_version": 1,
            "coach_refinement_status": "complete",
            "coach_refinement_error": None,
        },
    )
    print(f"{analysis_run} | chatgpt refinement complete")
    LOGGER.info("[END] import_chatgpt_response | analysis_run=%s", analysis_run)


def refine_with_openai_api(
    session_dir: Path, analysis_run: Path, *, client: Any | None = None
) -> None:
    LOGGER.info("[START] refine_with_openai_api | analysis_run=%s", analysis_run)
    session, _ticks, laps = read_session_artifacts(session_dir)
    analysis, selected_delta = load_analysis_context(analysis_run)
    validate_analysis_run_ownership(session, analysis)
    ensure_refinement_allowed(analysis, mode="api")

    model = os.environ.get("SIMCOACH_OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    reasoning_effort = os.environ.get("SIMCOACH_OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
    prompt = build_coach_prompt(session, analysis, selected_delta)
    try:
        response_text = call_openai_responses_api(
            prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            client=client,
        )
        response = parse_json_response(response_text)
        validate_coach_response(response, selected_delta)
    except Exception as exc:
        update_analysis_yaml(
            analysis_run,
            {
                "coach_refinement_mode": "api",
                "coach_refinement_status": "failed",
                "coach_refinement_error": short_error(exc),
            },
        )
        raise

    wrapper = coach_response_wrapper(
        provider="openai_api",
        model=model,
        reasoning_effort=reasoning_effort,
        response=response,
    )
    write_json(analysis_run / "coach_response.json", wrapper)
    report = render_refined_report(session, analysis_run, laps, analysis, selected_delta, response)
    atomic_write_text(analysis_run / "coach_report.md", report)
    atomic_write_text(session_dir / "coach_report.md", report)
    update_analysis_yaml(
        analysis_run,
        {
            "coach_response_schema_version": 1,
            "coach_refinement_mode": "api",
            "coach_refinement_status": "complete",
            "coach_refinement_error": None,
        },
    )
    print(f"{analysis_run} | api refinement complete")
    LOGGER.info("[END] refine_with_openai_api | analysis_run=%s", analysis_run)


def call_openai_responses_api(
    prompt: str,
    *,
    model: str,
    reasoning_effort: str,
    client: Any | None = None,
) -> str:
    if client is None:
        from openai import OpenAI

        client = OpenAI()
    response = client.responses.create(
        model=model,
        input=prompt,
        reasoning={"effort": reasoning_effort},
        text={
            "format": {
                "type": "json_schema",
                "name": "open_race_coach_response",
                "schema": COACH_RESPONSE_JSON_SCHEMA,
                "strict": True,
            },
            "verbosity": "low",
        },
    )
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
    elif isinstance(response, dict):
        dumped = response
    else:
        dumped = json.loads(response.model_dump_json())
    return _extract_text_from_response_dump(dumped)


def load_analysis_context(analysis_run: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis = yaml.safe_load((analysis_run / "analysis.yaml").read_text(encoding="utf-8"))
    selected_delta = json.loads((analysis_run / "selected_delta.json").read_text(encoding="utf-8"))
    if analysis.get("run_status") != "complete":
        raise CoachRefinementError(
            "Coach Refinement requires a complete deterministic Analysis Run"
        )
    return analysis, selected_delta


def ensure_refinement_allowed(analysis: dict[str, Any], *, mode: Literal["api", "chatgpt"]) -> None:
    current_mode = analysis.get("coach_refinement_mode")
    status = analysis.get("coach_refinement_status")
    if mode == "chatgpt":
        if current_mode is None and status == "not_requested":
            return
        raise CoachRefinementError("ChatGPT refinement requires an unrefined Analysis Run")
    if current_mode is None and status == "not_requested":
        return
    if current_mode == "api" and status == "failed":
        return
    raise CoachRefinementError("API refinement is not allowed for this Analysis Run state")


def validate_analysis_run_ownership(session: dict[str, Any], analysis: dict[str, Any]) -> None:
    if analysis.get("recorded_session_id") != session.get("recorded_session_id"):
        raise CoachRefinementError("Analysis Run recorded_session_id does not match session.yaml")


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("coach response must be a JSON object")
    return parsed


def validate_coach_response(response: dict[str, Any], selected_delta: dict[str, Any]) -> None:
    required = {"analysis_status", "corner_segment_id", "instruction", "why", "confidence_note"}
    missing = required - set(response)
    if missing:
        raise ValueError(f"coach response missing keys: {sorted(missing)}")
    unexpected = set(response) - required
    if unexpected:
        raise ValueError(f"coach response unexpected keys: {sorted(unexpected)}")
    if response["analysis_status"] != selected_delta["analysis_status"]:
        raise ValueError("coach response analysis_status does not match deterministic result")
    if not isinstance(response["why"], str) or not response["why"].strip():
        raise ValueError("coach response why must be non-empty")
    if not isinstance(response["confidence_note"], str) or not response["confidence_note"].strip():
        raise ValueError("coach response confidence_note must be non-empty")

    if selected_delta["analysis_status"] == "reportable":
        deterministic = selected_delta["selected_delta"]
        if response["corner_segment_id"] != deterministic["corner_segment_id"]:
            raise ValueError("coach response corner_segment_id does not match deterministic result")
        if not isinstance(response["instruction"], str) or not response["instruction"].strip():
            raise ValueError("reportable coach response requires a non-empty instruction")
    else:
        if response["corner_segment_id"] is not None or response["instruction"] is not None:
            raise ValueError("non-reportable coach response must not contain an instruction")


def render_refined_report(
    session: dict[str, Any],
    analysis_run: Path,
    laps: list[dict[str, Any]],
    analysis: dict[str, Any],
    selected_delta: dict[str, Any],
    response: dict[str, Any],
) -> str:
    report = render_coach_report(
        session, analysis_run, laps, analysis["reference_mode"], selected_delta
    )
    lines = report.splitlines()
    one_idx = lines.index("## The one thing")
    why_idx = lines.index("## Why (the data)")
    data_idx = lines.index("## Checked areas")
    one_text = response["instruction"] or (
        f"No data-supported Coaching Instruction. {response['why']}"
    )
    refined = (
        lines[: one_idx + 1]
        + [one_text, ""]
        + lines[why_idx:data_idx]
        + ["Coach refinement:", response["why"], response["confidence_note"], ""]
        + lines[data_idx:]
    )
    return "\n".join(refined).rstrip() + "\n"


def coach_response_wrapper(
    *,
    provider: str,
    model: str,
    reasoning_effort: str | None,
    response: dict[str, Any],
) -> dict[str, Any]:
    return {
        "coach_response_schema_version": 1,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "created_at": datetime.now().astimezone().isoformat(),
        "response": response,
    }


def update_analysis_yaml(analysis_run: Path, updates: dict[str, Any]) -> None:
    path = analysis_run / "analysis.yaml"
    analysis = yaml.safe_load(path.read_text(encoding="utf-8"))
    analysis.update(updates)
    analysis["updated_at"] = datetime.now().astimezone().isoformat()
    atomic_write_yaml(path, analysis)


def short_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:300]


def _extract_text_from_response_dump(dumped: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in dumped.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    if not chunks:
        raise ValueError("OpenAI response did not contain output text")
    return "\n".join(chunks)
