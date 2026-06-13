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
