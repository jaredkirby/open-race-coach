from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simcoach.analysis.run import analyze_session
from simcoach.coach.coach import (
    CoachRefinementError,
    import_chatgpt_response,
    refine_with_openai_api,
    start_chatgpt_refinement,
)
from simcoach.coach.prompt import build_coach_prompt
from simcoach.ingest.session import create_recorded_session, finalize_recorded_session
from tests.test_analysis import build_synthetic_reportable_ticks
from tests.test_session import session_info


def test_chatgpt_prompt_and_response_import(tmp_path: Path) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)

    start_chatgpt_refinement(session_dir, run_dir)
    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis["coach_refinement_mode"] == "chatgpt"
    assert analysis["coach_refinement_status"] == "awaiting_response"
    assert (run_dir / "coach_prompt.md").exists()
    assert "Return JSON only" in (run_dir / "coach_prompt.md").read_text()

    response_path = tmp_path / "chatgpt_response.json"
    response_path.write_text(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C2",
                "instruction": "Carry more speed through C2.",
                "why": "The deterministic analysis selected C2 with repeatable minimum-speed loss.",
                "confidence_note": (
                    "The instruction is bounded to the selected deterministic delta."
                ),
            }
        ),
        encoding="utf-8",
    )
    import_chatgpt_response(session_dir, run_dir, response_path)

    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    wrapper = json.loads((run_dir / "coach_response.json").read_text())
    report = (run_dir / "coach_report.md").read_text()
    assert analysis["coach_refinement_status"] == "complete"
    assert analysis["coach_response_schema_version"] == 1
    assert wrapper["provider"] == "chatgpt_manual"
    assert wrapper["response"]["instruction"] == "Carry more speed through C2."
    assert "Carry more speed through C2." in report
    assert "Minimum speed was 42.0 m/s" in report
    assert "10.8 km/h" in report
    assert (session_dir / "coach_report.md").read_text() == report


def test_invalid_chatgpt_response_sets_invalid_state_without_response_artifact(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)
    start_chatgpt_refinement(session_dir, run_dir)
    bad_response = tmp_path / "bad_response.json"
    bad_response.write_text(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C999",
                "instruction": "Invent a different corner.",
                "why": "Wrong corner.",
                "confidence_note": "Bad.",
            }
        ),
        encoding="utf-8",
    )

    try:
        import_chatgpt_response(session_dir, run_dir, bad_response)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid response should fail validation")

    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis["coach_refinement_status"] == "invalid_response"
    assert analysis["coach_refinement_error"]
    assert not (run_dir / "coach_response.json").exists()


def test_chatgpt_response_rejects_unexpected_keys_without_response_artifact(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)
    start_chatgpt_refinement(session_dir, run_dir)
    bad_response = tmp_path / "extra_response.json"
    bad_response.write_text(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C2",
                "instruction": "Carry more speed through C2.",
                "why": "The deterministic analysis selected C2.",
                "confidence_note": "Bounded to deterministic evidence.",
                "raw_ticks": [{"t": 0.1, "speed": 42.0}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected keys"):
        import_chatgpt_response(session_dir, run_dir, bad_response)

    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis["coach_refinement_status"] == "invalid_response"
    assert "unexpected keys" in analysis["coach_refinement_error"]
    assert not (run_dir / "coach_response.json").exists()


def test_openai_api_refinement_uses_mocked_responses_client(tmp_path: Path) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)
    fake_client = FakeOpenAIClient(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C2",
                "instruction": "Carry more minimum speed in C2.",
                "why": "C2 has the selected repeatable minimum-speed delta.",
                "confidence_note": "The model did not alter deterministic evidence.",
            }
        )
    )

    refine_with_openai_api(session_dir, run_dir, client=fake_client)

    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    wrapper = json.loads((run_dir / "coach_response.json").read_text())
    assert analysis["coach_refinement_mode"] == "api"
    assert analysis["coach_refinement_status"] == "complete"
    assert wrapper["provider"] == "openai_api"
    assert fake_client.responses.kwargs["model"] == "gpt-5.4-mini"
    assert fake_client.responses.kwargs["reasoning"] == {"effort": "low"}
    assert fake_client.responses.kwargs["text"]["format"]["type"] == "json_schema"


def test_openai_api_failure_preserves_deterministic_artifacts_and_allows_retry(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)
    original_report = (run_dir / "coach_report.md").read_text()
    original_session_report = (session_dir / "coach_report.md").read_text()
    original_selected_delta = (run_dir / "selected_delta.json").read_text()
    original_corner_segments = (run_dir / "corner_segments.json").read_text()
    bad_client = FakeOpenAIClient(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C999",
                "instruction": "Invent a different corner.",
                "why": "Wrong corner.",
                "confidence_note": "Bad.",
            }
        )
    )

    try:
        refine_with_openai_api(session_dir, run_dir, client=bad_client)
    except ValueError:
        pass
    else:
        raise AssertionError("schema-invalid API response should fail validation")

    failed_analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert failed_analysis["coach_refinement_mode"] == "api"
    assert failed_analysis["coach_refinement_status"] == "failed"
    assert failed_analysis["coach_refinement_error"]
    assert not (run_dir / "coach_response.json").exists()
    assert (run_dir / "coach_report.md").read_text() == original_report
    assert (session_dir / "coach_report.md").read_text() == original_session_report
    assert (run_dir / "selected_delta.json").read_text() == original_selected_delta
    assert (run_dir / "corner_segments.json").read_text() == original_corner_segments

    good_client = FakeOpenAIClient(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C2",
                "instruction": "Carry more minimum speed in C2.",
                "why": "C2 is the deterministic selected issue.",
                "confidence_note": "This retry stayed within deterministic evidence.",
            }
        )
    )

    refine_with_openai_api(session_dir, run_dir, client=good_client)

    retried_analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    wrapper = json.loads((run_dir / "coach_response.json").read_text())
    assert retried_analysis["coach_refinement_mode"] == "api"
    assert retried_analysis["coach_refinement_status"] == "complete"
    assert retried_analysis["coach_refinement_error"] is None
    assert wrapper["response"]["instruction"] == "Carry more minimum speed in C2."
    assert "Carry more minimum speed in C2." in (run_dir / "coach_report.md").read_text()


def test_refinement_refuses_mismatched_recorded_session_without_mutation(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = analyzed_session(tmp_path / "source")
    other_session_dir, _other_run_dir = analyzed_session(tmp_path / "other")
    original_report = (run_dir / "coach_report.md").read_text()
    original_session_report = (session_dir / "coach_report.md").read_text()
    other_session_report = (other_session_dir / "coach_report.md").read_text()

    with pytest.raises(CoachRefinementError, match="recorded_session_id does not match"):
        start_chatgpt_refinement(other_session_dir, run_dir)

    assert not (run_dir / "coach_prompt.md").exists()
    assert (run_dir / "coach_report.md").read_text() == original_report
    assert (session_dir / "coach_report.md").read_text() == original_session_report
    assert (other_session_dir / "coach_report.md").read_text() == other_session_report


def test_refinement_mode_cannot_switch_after_api_failure(tmp_path: Path) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)
    bad_client = FakeOpenAIClient(
        json.dumps(
            {
                "analysis_status": "reportable",
                "corner_segment_id": "C999",
                "instruction": "Invent a different corner.",
                "why": "Wrong corner.",
                "confidence_note": "Bad.",
            }
        )
    )
    with pytest.raises(ValueError):
        refine_with_openai_api(session_dir, run_dir, client=bad_client)

    with pytest.raises(CoachRefinementError, match="ChatGPT refinement requires"):
        start_chatgpt_refinement(session_dir, run_dir)

    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    assert analysis["coach_refinement_mode"] == "api"
    assert analysis["coach_refinement_status"] == "failed"
    assert not (run_dir / "coach_prompt.md").exists()


def test_coach_prompt_allowlists_deterministic_aggregate_evidence(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = analyzed_session(tmp_path)
    session = yaml.safe_load((session_dir / "session.yaml").read_text())
    analysis = yaml.safe_load((run_dir / "analysis.yaml").read_text())
    selected_delta = json.loads((run_dir / "selected_delta.json").read_text())
    selected_delta["selected_delta"]["per_lap_rows"] = [{"lap": 2, "raw_speed": [1, 2, 3]}]
    selected_delta["selected_delta"]["raw_ticks"] = [{"t": 0.1, "speed": 42.0}]
    selected_delta["selected_delta"]["cause_metric"]["raw_samples"] = [39.0, 40.0]
    selected_delta["corner_summaries"][0]["debug_lap_rows"] = [{"lap": 2, "loss": 0.1}]

    prompt = build_coach_prompt(session, analysis, selected_delta)
    evidence = json.loads(prompt.split("Deterministic evidence:\n", 1)[1])

    assert "per_lap_rows" not in prompt
    assert "raw_ticks" not in prompt
    assert "raw_samples" not in prompt
    assert "debug_lap_rows" not in prompt
    assert evidence["selected_delta"]["corner_segment_id"] == "C2"
    assert evidence["selected_delta"]["dominant_cause"] == "min_speed"
    assert "median_corner_loss_s" in evidence["corner_summaries"][0]


def analyzed_session(tmp_path: Path) -> tuple[Path, Path]:
    info = session_info()
    session_id, started_at, paths = create_recorded_session(tmp_path, info)
    finalize_recorded_session(
        paths, session_id, info, started_at, build_synthetic_reportable_ticks()
    )
    run_dir = analyze_session(paths.root)
    selected_delta = json.loads((run_dir / "selected_delta.json").read_text())
    assert selected_delta["analysis_status"] == "reportable"
    assert selected_delta["selected_delta"]["corner_segment_id"] == "C2"
    return paths.root, run_dir


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.kwargs = {}

    def create(self, **kwargs: object) -> FakeResponse:
        self.kwargs = kwargs
        return FakeResponse(self.output_text)


class FakeOpenAIClient:
    def __init__(self, output_text: str) -> None:
        self.responses = FakeResponses(output_text)
