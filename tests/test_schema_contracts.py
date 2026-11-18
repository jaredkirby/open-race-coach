from __future__ import annotations

from pathlib import Path


def test_every_v0_durable_artifact_has_exactly_one_schema_doc() -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schema"

    assert {path.name for path in schema_dir.glob("*.md")} == {
        "analysis_yaml.md",
        "coach_response.md",
        "corner_segments.md",
        "lap_record.md",
        "selected_delta.md",
        "session_yaml.md",
        "tick.md",
        "telemetry_archive.md",
        "race_watch.md",
        "radio.md",
        "visit_clock.md",
        "driver_visit.md",
        "live_reaction.md",
        "visit_outcome.md",
        "visit_recovery.md",
        "visit_watch.md",
        "operations_service.md",
        "archive_coverage.md",
        "visit_review.md",
        "record_control.md",
        "stint_checkpoint.md",
        "fuel_budget.md",
        "collector_release.md",
        "live_dashboard.md",
        "dashboard_feedback.md",
        "input_coordinator.md",
        "input_gateway.md",
        "session_recipe.md",
        "visit_release.md",
    }


def test_schema_docs_include_phase_zero_contract_sections() -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schema"
    for path in schema_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        required_terms = [
            "schema version:",
            "owner:",
            "write timing:",
            "compatibility:",
            "required",
            "nullable",
            "enum",
            "unit",
        ]
        missing = [term for term in required_terms if term not in lowered]
        assert not missing, f"{path.name} missing contract terms: {missing}"
