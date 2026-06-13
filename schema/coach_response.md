# coach_response.json schema

Schema version: 1
Owner: coach refinement layer
Write timing: only after an API response or ChatGPT manual paste validates against deterministic artifacts.
Compatibility: `analysis.yaml.coach_response_schema_version` is null until this artifact exists; readers refuse unsupported versions.

Required wrapper fields: `coach_response_schema_version`, `provider`, `model`, `reasoning_effort`, `created_at`, `response`.

Nullable wrapper fields: `reasoning_effort`.

Enums:

- `provider`: `openai_api`, `chatgpt_manual`
- `reasoning_effort`: null, `low`, `medium`, `high`
- `response.analysis_status`: `reportable`, `consistent`, `inconsistent`, `insufficient_data`, `no_single_dominant_issue`

Wrapper field details:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `coach_response_schema_version` | yes | no | int | this schema version |
| `provider` | yes | no | string | enum |
| `model` | yes | no | string | OpenAI API model name or `user_reported_or_unknown` for manual ChatGPT |
| `reasoning_effort` | yes | yes | string | enum; null for manual ChatGPT |
| `created_at` | yes | no | string | timezone-aware ISO 8601 timestamp |
| `response` | yes | no | object | validated model JSON |

Response fields: exactly `analysis_status`, `corner_segment_id`, `instruction`, `why`, and `confidence_note`. Missing or unexpected keys are invalid.

Response field details:

| Field | Required | Nullable | Type | Unit / meaning |
|---|---:|---:|---|---|
| `analysis_status` | yes | no | string | must match deterministic `selected_delta.json` |
| `corner_segment_id` | yes | conditional | string | selected Corner Segment ID, null for non-reportable outcomes |
| `instruction` | yes | conditional | string | one coach instruction, null for non-reportable outcomes |
| `why` | yes | no | string | non-empty prose explanation |
| `confidence_note` | yes | no | string | non-empty prose confidence caveat |

For reportable outcomes, `corner_segment_id` and `instruction` are non-null and must match the deterministic selected Reportable Delta. For non-reportable outcomes, `corner_segment_id` and `instruction` are null, while `why` and `confidence_note` are non-empty. The model never changes deterministic status, selected Corner Segment, cause, reference, or data values.
