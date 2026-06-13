# Open Race Coach — Project Bootstrap Spec v0.1

**Status:** Draft for dev-agent execution
**Owner:** Jared Kirby
**Repo:** fresh (this spec is the founding document — commit it as `docs/SPEC_v0.1.md`)
**Date:** 2026-06-12
**Public domain:** https://openracecoach.com
**Internal Python package:** `simcoach` remains the import namespace for v0.

**Current evidence note (2026-06-13):** local Phase 0–3 scaffolding, deterministic tests, coach-mode plumbing, and self-contained AMS2/ACC adapter code exist in the worktree. The code has been exercised with synthetic fixtures and two public Project CARS/Project CARS 2 shared-memory-derived JSON samples, including one CREST sample with real `mCurrentLapDistance`, participant, car-state, timing, event, and track-length values. That is useful compatibility evidence for overlapping PC2/AMS2 field names and adapter normalization, but it is **not** a substitute for raw `$pcars2$` mmap bytes or a live Windows AMS2 capture. v0 is not done until the Definition of Done in §10 is satisfied with real simulator sessions.

---

## 1. What this is

A local-first, post-session telemetry coaching tool for sim racing. It ingests telemetry from multiple sims via shared memory, normalizes it into a common schema stored as Parquet on disk, analyzes lap-over-lap deltas at the corner level, and produces **at most one plain-English coaching instruction per session** — the biggest repeatable, data-supported divergence from the selected reference lap, or an explicit no-instruction status when the data does not support one.

**One-sentence product:** "Drive a session; before your next one, Open Race Coach tells you the one repeatable driving delta most worth attacking, or says the data does not support one."

### Design philosophy
- **Filesystem-as-API.** No database. YAML for metadata, Parquet for telemetry, JSONL for lap records, Markdown for human-readable output. Every artifact is inspectable with `cat`, `head`, or pandas.
- **Adapter pattern from day one.** Three sims (AMS2, ACC, AC) share one normalized schema. Each sim gets one adapter file. Porting to a new sim = write one adapter, touch nothing else.
- **Confidence-gated coaching.** The coach never gives advice the data doesn't support. If a delta is within lap-to-lap noise, it says so explicitly.
- **Self-reference honesty.** v0 compares the driver to laps they already drove. Without an external reference lap, it does **not** claim to know theoretical optimal pace.
- **Post-session only.** No live overlays, no real-time anything in v0.

---

## 2. Target environment

| Item | Value |
|---|---|
| OS | Windows 11 (shared memory is Windows-only; this is fine) |
| Language | Python 3.11+ |
| Package mgmt | `uv` (preferred) or pip + `requirements.txt` |
| Storage | Local filesystem, repo-adjacent `data/` dir (gitignored) |
| LLM (coach layer) | OpenAI. Two supported modes: `api` via the OpenAI Responses API, and `chatgpt` via manual copy/paste into the user's ChatGPT subscription. |
| Default API model | `gpt-5.4-mini` for cost-controlled coaching; configurable via `SIMCOACH_OPENAI_MODEL`. Use `gpt-5.5` only when evals show the extra quality is worth the token cost. |
| Token-cost policy | Prefer `chatgpt` mode for personal local use when the goal is to spend ChatGPT subscription messages instead of API tokens. API usage is billed separately from ChatGPT subscriptions and requires API billing to be enabled. |
| Key deps | `numpy`, `pandas`, `pyarrow`, `pyyaml`, `openai`, `ulid-py`. AMS2/AC/ACC shared-memory reading via `ctypes` against documented structs (no heavyweight third-party telemetry frameworks — keep adapters self-contained and auditable) |

---

## 3. Repo layout (create exactly this)

```
open-race-coach/
├── docs/
│   ├── SPEC_v0.1.md              # this document
│   └── DECISIONS.md              # running ADR-lite log (date, decision, why)
├── simcoach/
│   ├── __init__.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py               # SimAdapter ABC + NormalizedTick dataclass
│   │   ├── ams2.py               # Phase 1
│   │   ├── acc.py                # Phase 4
│   │   └── ac.py                 # later, stub only
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── recorder.py           # session loop: poll adapter → buffer → flush Parquet
│   │   └── session.py            # session lifecycle, metadata capture, provisional lap detection, final lap derivation
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── segmentation.py       # corner detection from normalized ground-plane curvature
│   │   ├── deltas.py             # corner-level comparison vs reference lap
│   │   └── confidence.py         # noise estimation, delta significance gating
│   ├── coach/
│   │   ├── __init__.py
│   │   ├── prompt.py             # structured deltas → OpenAI/ChatGPT prompt construction
│   │   └── coach.py              # API call, ChatGPT import, output validation, Markdown report
│   └── utils/
│       ├── __init__.py
│       └── llm_logger.py         # structured logging module (see §8)
├── schema/
│   ├── tick.md                   # normalized tick schema doc (source of truth)
│   ├── lap_record.md             # laps.jsonl record schema doc
│   ├── session_yaml.md           # session.yaml schema doc
│   ├── analysis_yaml.md          # analysis.yaml schema doc
│   ├── corner_segments.md        # corner_segments.json schema doc
│   ├── selected_delta.md         # selected_delta.json schema doc
│   └── coach_response.md         # coach_response.json schema doc
├── data/                         # gitignored; created at runtime
│   └── sessions/
│       └── {YYYY-MM-DD_HHMM}_{sim}_{track}_{session_type}_{recorded_session_id}/
│           ├── session.yaml
│           ├── ticks.parquet
│           ├── laps.jsonl
│           ├── coach_report.md   # convenience copy of latest Analysis Run report, not canonical
│           └── analysis/
│               └── {YYYY-MM-DD_HHMMSS}_{reference_mode}/
│                   ├── analysis.yaml
│                   ├── corner_segments.json
│                   ├── selected_delta.json
│                   ├── coach_report.md
│                   ├── coach_prompt.md    # only written in --coach-mode chatgpt
│                   └── coach_response.json # optional validated coach response
├── tests/
│   ├── test_adapters.py          # struct parsing against fixture bytes
│   ├── test_segmentation.py      # corner detection on synthetic + recorded traces
│   ├── test_deltas.py
│   └── fixtures/                 # captured raw shared-memory dumps + known-good traces
├── scripts/
│   ├── record.py                 # CLI entry: start a recording session
│   ├── analyze.py                # CLI entry: analyze a session dir, emit coach report
│   ├── validate_session.py       # offline Recorded Session artifact/invariant validation
│   ├── validate_ams2.py          # struct sanity asserts (see §5.1)
│   ├── capture_ams2_fixture.py   # Windows-only raw $pcars2$ fixture capture
│   └── capture_acc_fixture.py    # Windows-only raw ACC page fixture capture
├── .gitignore                    # data/, .venv/, logs/, __pycache__
├── pyproject.toml
└── README.md                     # quickstart: enable shared memory, record, analyze
```

---

## 4. Data contracts (the load-bearing section)

### 4.1 NormalizedTick (one row in `ticks.parquet`)

This is the **only** schema the analysis layer ever sees. Adapters do all the translation.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `t` | float64 | seconds | Monotonic Capture Time from the start of Open Race Coach recording. Use a recorder-owned monotonic clock; do not use a simulator clock that can pause, reset, freeze, or jump backward. |
| `lap` | int32 | — | Current lap number (sim-reported, 1-indexed) |
| `lap_dist_pct` | float32 | 0.0–1.0 | Normalized Lap Progress. **Primary alignment key for all comparisons** |
| `lap_dist_m` | float32 nullable | meters | Distance around current lap. Use sim-provided value when exposed; otherwise derive from `track_length_m` if known. Required for meter-based coaching language; provenance lives in `session.yaml.lap_dist_m_source`. |
| `speed` | float32 | m/s | Store SI; convert at display only |
| `throttle` | float32 | 0.0–1.0 | |
| `brake` | float32 | 0.0–1.0 | |
| `steering` | float32 | -1.0–1.0 | Negative = left |
| `gear` | int8 | — | -1 reverse, 0 neutral, 1+ forward |
| `rpm` | float32 | rev/min | |
| `vehicle_state` | string | enum | `running` \| `pit` \| `paused` \| `menu` \| `replay` \| `unknown`; normalized capture state used for lap validity and ingestion hygiene |
| `pos_x` | float32 | m | Normalized ground-plane X; adapters translate each sim's world axes into this convention |
| `pos_y` | float32 | m | Normalized ground-plane Y; used with `pos_x` for corner segmentation |
| `pos_z` | float32 | m | Elevation/vertical axis where exposed; otherwise 0.0 |

**Tick rate:** downsample to **60 Hz** at ingest. Full physics rate is noise for v0 purposes.

**Explicitly deferred to v1 (do not add):** tire temps, slip angles, ride height, suspension travel, fuel, weather.

### 4.2 Lap record (one line in `laps.jsonl`)

`laps.jsonl` is a final derived lap index over `ticks.parquet`, not the primary recording source of truth. During recording, lap state may be tracked provisionally for console output, but permanent lap records are written during Recorded Session finalization from the finalized tick buffer or `ticks.parquet`.

`recorded_session_id` is a lowercase ULID generated at capture start. `lap_id` is formed as `{recorded_session_id}:lap:{lap}`.

```json
{
  "lap_id": "01jz8example0000000000000000:lap:7",
  "lap": 7,
  "lap_time_s": 92.413,
  "lap_time_source": "sim",
  "sector_times_s": [31.2, 35.1, 26.1],
  "valid": true,
  "invalid_reason": null,
  "tick_range": [41250, 46795]
}
```

- `lap_time_source` is `sim` when the simulator exposes a final lap time for that lap; otherwise it is `derived_from_ticks`, using the elapsed `t` delta across the finalized half-open `tick_range`.
- `sector_times_s` is nullable. Use simulator sector times only when exposed and trustworthy; do not invent official sector timing by splitting the lap into thirds. If sector timing is unavailable, write `null`.
- `tick_range` is zero-based, half-open row indexes into `ticks.parquet`: `[start_idx, end_idx_exclusive]`.
- `valid` comes from simulator invalidation evidence plus Open Race Coach hard hygiene checks. A sim cut/invalid flag or explicit off-track/cut field invalidates the lap when exposed, but simulator flags do not bypass inferred checks for teleport/reset, unusable Lap Progress (`lap_dist_pct` gaps, reversals, long flat spots, or discontinuities that prevent fixed-grid resampling), missing tick ranges, or non-running Vehicle State during the lap. `vehicle_state=unknown` does not automatically invalidate a lap, but laps with unknown state must pass all other validity checks strictly. If any analyzed lap contains unknown Vehicle State, set `session.yaml.validity_method` to `unknown_plus_inferred`. If any tick in a lap is known `paused`, `menu`, or `replay`, invalidate the lap. If `pit` appears within a completed timed lap, invalidate the lap. If more than 20% of lap ticks are known non-running states, invalidate the lap. **Slow laps are not invalid by themselves.** Document which Validity Method was used in `session.yaml`.
- `invalid_reason` is nullable for valid laps and otherwise one of: `sim_invalidated`, `bad_lap_progress`, `missing_tick_range`, `non_running_vehicle_state`, `teleport_or_reset`, `metadata_boundary`, or `unknown_invalidity`.
- Invalid laps are stored but **never** used as Reference Laps, Comparison Laps, noise-estimation inputs, consistency-classification inputs, or personal-best candidates.

### 4.3 `session.yaml`

```yaml
session_schema_version: 1
tick_schema_version: 1
lap_schema_version: 1
recorded_session_id: 01jz8example0000000000000000 # lowercase ULID
sim: ams2                 # ams2 | acc | ac
track_raw: "Interlagos GP" # Simulator Label for display/audit
track: interlagos_gp       # Matching Key: normalized deterministic string
car_raw: "Formula Inter"   # Simulator Label for display/audit
car: formula_inter         # Matching Key: normalized deterministic string
session_type: practice    # practice | qualifying | race | hotlap
started_at: 2026-06-12T19:42:00-07:00
ended_at: 2026-06-12T19:56:00-07:00 # nullable until finalization
tick_rate_hz: 60
track_length_m: 4309.0    # nullable if the sim does not expose it
lap_dist_m_source: sim    # sim | derived_from_track_length | unavailable
adapter_version: 0.1.0
validity_method: sim_flag_plus_inferred # sim_flag_plus_inferred | inferred | unknown_plus_inferred
complete: true
failure_reason: null      # non-null when complete=false
notes: ""                 # freeform, optional
```

Required metadata for starting and continuing a Recorded Session is: `sim`, `track_raw`, `track`, `car_raw`, `car`, `session_type`, `adapter_version`, `session_schema_version`, `tick_schema_version`, and `lap_schema_version`. `track_length_m` may be unknown, and `lap_dist_m_source=unavailable` is valid; missing meter distance is not a session-start blocker.

Matching Keys for `track` and `car` are derived from the corresponding Simulator Label by lowercasing, trimming leading/trailing whitespace, replacing every run of non-alphanumeric characters with `_`, and stripping leading/trailing `_`. If normalization produces an empty Matching Key, treat the required metadata as unavailable. v0 does not use alias tables, fuzzy matching, or per-sim hand mappings for Matching Keys.

`validity_method` is `sim_flag_plus_inferred` when the adapter exposes a trusted simulator invalidation flag and Open Race Coach also ran inferred hygiene checks, `inferred` when Valid Lap decisions used Open Race Coach hard evidence without simulator invalidation flags, and `unknown_plus_inferred` when any analyzed lap includes `vehicle_state=unknown` and therefore passed stricter inferred checks. There is no v0 `sim_flag_only` mode: simulator invalidation flags are useful evidence, not permission to ignore broken Lap Progress, missing tick ranges, teleport/reset evidence, or non-running Vehicle State.

`failure_reason` is `null` when `complete: true`. When `complete: false`, it must be one of: `metadata_unavailable`, `artifact_write_failed`, `recording_interrupted`, `no_ticks_collected`, `directory_collision`, `session_boundary_finalization_failed`, or `unknown_failure`.

All durable timestamps are timezone-aware ISO 8601 strings with offsets. Directory timestamps use the local timezone from `started_at` for Recorded Sessions and `created_at` for Analysis Runs; artifact timestamps remain authoritative when directory names and metadata disagree. Recorded Session directory names include the full `recorded_session_id`; if a directory path already exists, treat it as a fatal collision and do not append ad hoc suffixes.

Artifact writes must be atomic at the file level: write to a temporary path in the same directory, flush, then replace. For Recorded Session finalization, write `session.yaml.complete: true` only after required artifacts have been written successfully. If finalization fails after any required artifact is partially available, write `session.yaml.complete: false` with `failure_reason` when possible. For Analysis Runs, write deterministic artifacts to a new run directory; if deterministic analysis fails after directory creation, write `analysis.yaml.run_status: failed` with a controlled `run_error` reason and do not update the Recorded Session-level `coach_report.md`.

### 4.4 Analysis Run artifacts

Each Analysis Run writes canonical artifacts under `analysis/{YYYY-MM-DD_HHMMSS}_{reference_mode}/`. `reference_mode` is `best` or `personal`, matching the CLI `--reference` value used when the deterministic Analysis Run was created. If the computed Analysis Run directory already exists, refuse the run with a collision error; do not overwrite it and do not append ad hoc suffixes. `analysis.yaml` records `analysis_schema_version`, nullable `corner_segments_schema_version`, nullable `selected_delta_schema_version`, nullable `coach_response_schema_version`, the Recorded Session path at run creation time, `recorded_session_id`, reference mode, selected `reference_lap_id`, selected Reference Lap source path, analyzer version, effective thresholds used, `run_status`, optional `run_error`, `coach_refinement_mode`, `coach_refinement_status`, optional `coach_refinement_error`, `analysis_status`, and timestamps. `recorded_session_id` is the ownership identity; the stored Recorded Session path is audit metadata and may be stale after moves or copies. `run_status` is deterministic artifact lifecycle state: `complete | failed`. `run_error` is `null` when `run_status=complete`; when `run_status=failed`, it must be one of: `unsupported_schema`, `incomplete_recorded_session`, `missing_required_artifact`, `artifact_read_failed`, `artifact_write_failed`, `invalid_artifact_contract`, or `analysis_exception`. `coach_refinement_mode` is LLM workflow ownership: `null | api | chatgpt`. `coach_refinement_status` is LLM workflow state: `not_requested | complete | awaiting_response | invalid_response | failed`. `analysis_status` is nullable only when `run_status=failed`; otherwise it is the coaching outcome: `reportable | consistent | inconsistent | insufficient_data | no_single_dominant_issue`. In every complete deterministic Analysis Run, `corner_segments_schema_version` and `selected_delta_schema_version` are non-null, `corner_segments.json` exists, `selected_delta.json` exists, and `coach_report.md` exists even when `analysis_status=insufficient_data`. `corner_segments.json` records the Corner Segments derived from the selected Reference Lap for this Analysis Run; if no valid Reference Lap exists or segmentation cannot produce usable Corner Segments, it stores an empty ordered list plus the reason. `selected_delta.json` records the deterministic selected Reportable Delta or the non-reportable `analysis_status`. `coach_response_schema_version` is always present in `analysis.yaml`; it is `null` until `coach_response.json` exists, then equals that artifact's schema version. The Recorded Session-level `coach_report.md` is only a convenience copy of the most recently completed Coach Report render for that Recorded Session, not a canonical artifact.

`analysis.yaml` must store the effective threshold values for reproducibility: `min_comparison_laps`, `min_median_corner_loss_s`, `robust_noise_multiplier`, `dominant_cause_min_lap_fraction`, `single_dominant_margin_s`, all Lap Loss Cause thresholds, and segmentation thresholds once tuned. `docs/DECISIONS.md` records threshold rationale; `analysis.yaml` records the values actually used.

Failed deterministic Analysis Runs are terminal diagnostic artifacts, not partially usable Analysis Runs. When `run_status=failed`, only `analysis.yaml` is required; `analysis_status`, `reference_lap_id`, Reference Lap source path, `corner_segments_schema_version`, `selected_delta_schema_version`, and `coach_response_schema_version` are `null`; `coach_refinement_mode` is `null`; `coach_refinement_status` is `not_requested`; `coach_refinement_error` is `null`; and no `corner_segments.json`, `selected_delta.json`, `coach_report.md`, `coach_prompt.md`, or `coach_response.json` is canonical for that failed run.

Analysis Runs are immutable after deterministic completion. Creating an Analysis Run always uses a new timestamped directory and never overwrites an existing completed deterministic run. Coach Refinement is the only mutation exception, and may change only `analysis.yaml` refinement fields, `coach_prompt.md`, `coach_response.json`, `analysis/.../coach_report.md` prose fields, and the Recorded Session-level report copy. A Coach Refinement workflow can start only when `run_status=complete`. The first refinement action sets `coach_refinement_mode` to `api` or `chatgpt`; once set, the mode cannot be changed for that Analysis Run. API call failures and invalid API responses both set `coach_refinement_mode: api` and `coach_refinement_status: failed`, record a short error in `analysis.yaml.coach_refinement_error`, do not write `coach_response.json`, do not update any Coach Report, and leave `run_status: complete`. A later API retry is allowed only when `coach_refinement_mode` is `api` and `coach_refinement_status=failed`, or when `coach_refinement_mode` is `null` and `coach_refinement_status=not_requested`; successful retry clears `coach_refinement_error`. ChatGPT manual mode may create a new Analysis Run or target an existing complete deterministic Analysis Run with `coach_refinement_mode: null` and `coach_refinement_status: not_requested`; it writes `coach_prompt.md`, sets `coach_refinement_mode: chatgpt`, and sets `coach_refinement_status: awaiting_response`. `--chatgpt-response <path> --analysis-run <dir>` completes that refinement exactly once by writing `coach_response.json`, re-rendering allowed prose fields in `coach_report.md`, and setting `analysis.yaml.coach_refinement_status: complete`. Invalid response imports set `coach_refinement_status: invalid_response`, record a short validation error in `analysis.yaml.coach_refinement_error`, do not write `coach_response.json`, do not update any Coach Report, and leave `run_status: complete`. A later `--chatgpt-response` retry is allowed only when `coach_refinement_mode` is `chatgpt` and `coach_refinement_status` is either `awaiting_response` or `invalid_response`; successful retry clears `coach_refinement_error`. Every successful Coach Report render updates the Recorded Session-level `coach_report.md` convenience copy: plain deterministic analysis writes the deterministic report, API mode writes the refined report when refinement succeeds and otherwise leaves the deterministic report, ChatGPT prompt creation writes the deterministic report, and successful ChatGPT response import updates it again with the refined report.

Schema compatibility is strict for the target Recorded Session: if `session.yaml`, `ticks.parquet`, or `laps.jsonl` uses an unsupported Artifact Schema Version, `analyze.py` refuses to create an Analysis Run. For Personal Best Reference search, unsupported candidate Recorded Sessions are skipped with `[SKIP]` logs, because one stale historical session must not block analysis of a supported target.

`corner_segments.json` stores an ordered list of Corner Segments with `corner_segment_id`, half-open `start_edge_idx`, half-open `end_edge_idx`, half-open `start_lap_dist_pct`, half-open `end_lap_dist_pct`, optional `start_lap_dist_m`, optional `end_lap_dist_m`, and summary geometry metrics used to derive the segment. Corner Segment windows never wrap across the start/finish boundary; if curvature would produce a wraparound segment, split it at `lap_dist_pct=0.0/1.0` and merge only within each side. Number Corner Segments in increasing `start_lap_dist_pct` order as `C1`, `C2`, ...

`selected_delta.json` uses the same top-level `analysis_status` enum as `analysis.yaml`. When `analysis_status=reportable`, it must include exactly one selected Reportable Delta with `corner_segment_id`, `dominant_cause`, `comparison_lap_count`, `median_corner_loss_s`, `robust_noise_s`, a cause-metric object, meter/percentage provenance, and `runner_up_margin_s`. The cause-metric object stores `metric`, `unit`, `reference_value`, `comparison_median`, `signed_delta`, and `bad_direction_delta`; `bad_direction_delta` is always positive when the Comparison Laps are worse in the selected cause's direction. `runner_up_margin_s` is the selected delta's median loss minus the second-best Reportable Delta candidate's median loss; it is `null` when no second candidate exists. When non-reportable, it must include `analysis_status`, a short `reason`, `comparison_lap_count`, `reference_lap_id` when one exists, and `corner_summaries`.

`corner_summaries` is aggregate-only and must not include per-lap rows. Each entry includes `corner_segment_id`, `classification` (`reportable_candidate | consistent | inconsistent | insufficient_data`), `median_corner_loss_s`, `robust_noise_s`, nullable `dominant_cause`, nullable `dominant_cause_lap_fraction`, and a short `reason`. For `analysis_status=no_single_dominant_issue`, at least the top two `reportable_candidate` entries must be present so the Coach Report can explain why no single Coaching Instruction was selected. For `analysis_status=consistent`, include up to five strongest consistent corners by absolute median loss. For `analysis_status=inconsistent`, include corners with significant loss but no dominant repeatable Lap Loss Cause. For `analysis_status=insufficient_data`, include no `corner_summaries` when no usable Corner Segments exist; otherwise include the aggregate corner-level insufficiency reasons.

### 4.4.1 Coach report (`analysis/.../coach_report.md`)

Markdown, human-first. Structure:

```markdown
# Coach Report — {track} / {car} / {date}
**Session:** {n} laps, {n_valid} valid. Best valid lap: {time_or_none}. Reference: {session-best|personal-best|none} {lap_number_or_selected_lap}.

## The one thing
{If `analysis_status=reportable`: one Coaching Instruction, 1–3 sentences, driver language, with location, magnitude, comparison basis, and confidence basis. If the selected difference identifies an outcome but not a proven input change, say so explicitly. If non-reportable: no Coaching Instruction; state the exact non-reportable status in driver language and say what data is needed next. No invented corner names, official turn numbers, generic coaching tips, raw enum values, or filesystem paths.}

## Why (the data)
{Detected corner segment in human wording, time delta, measured difference: brake point, min speed, throttle point, or coast duration. Show meters only when supported by `lap_dist_m_source`; otherwise show lap-distance percentages. Preserve deterministic evidence even after LLM refinement.}

## Checked areas
{Per-corner summary in human wording. Corners where driver is already consistent get a one-line "no action" note.}
```

### 4.5 Invariants (assert these in code)

1. `ticks.parquet` rows are strictly monotonic in `t` within a session. `t` is Capture Time from a recorder-owned monotonic clock, not a simulator clock that can pause, reset, freeze, or jump backward.
2. Every lap in `laps.jsonl` maps to a contiguous tick range; no gaps, no overlaps.
3. `lap_dist_pct` is sufficiently dense and monotonic within a lap (modulo wraparound at lap boundary) to support fixed-grid resampling; flag gaps, reversals, long flat spots, discontinuities, teleports, or resets and mark the lap invalid with `invalid_reason=bad_lap_progress` when Lap Progress is unusable.
4. Analysis resamples valid laps onto a fixed `lap_dist_pct` grid before segmentation or delta comparison; raw 60 Hz ticks are never compared directly.
5. v0 does not derive Lap Progress from `pos_x`/`pos_y`; position traces support Corner Segment geometry only.
6. If `lap_dist_m_source` is `unavailable`, coach output must not claim meter-based brake/throttle differences. If `lap_dist_m_source` is `derived_from_track_length`, meter-based language is allowed but must be rounded coarsely, e.g. nearest 5 m or 10 m.
7. The coach layer receives **only** deterministic analysis results — never raw tick data. It may render the selected Reportable Delta, but it must not choose the Corner Segment, cause, confidence status, or reference.
8. `analyze.py` refuses any Recorded Session with `complete: false`.
9. Reference Lap selection and Personal Best Reference search consider only Recorded Sessions with `complete: true` and artifact schema versions supported by the analyzer.
10. Reference Lap selection is deterministic. `--reference best` uses the fastest Valid Lap in the target Recorded Session and breaks ties by lowest lap number. `--reference personal` searches candidate Recorded Session directories that are immediate children of one sessions root: by default, the parent directory containing `<session_dir>`; when `--sessions-root <dir>` is supplied, that directory is the sessions root. The target Recorded Session is always included even if it is not an immediate child of the effective sessions root. Candidate directories must contain a direct `session.yaml`, match exact `sim`, `track`, and `car` Matching Keys, be complete, use supported schema versions, and contain at least one Valid Lap. If multiple non-target candidate directories have the same `recorded_session_id`, skip that duplicated ID with a `[SKIP] duplicate_recorded_session_id` log rather than choosing an arbitrary copy. If the target Recorded Session ID also appears elsewhere, include the target path and skip the duplicate copies. Pick the lowest `lap_time_s`; break ties by earliest `started_at`, then lexical `recorded_session_id`, then lowest lap number. Store both `reference_lap_id` and the Reference Lap source path in `analysis.yaml`.

The fixed comparison grid is 1000 equal-width Lap Progress bins defined by 1001 edge points `i / 1000` for integer `i=0..1000`. Corner Segment boundaries align to grid edges and store half-open edge-index windows `[start_edge_idx, end_edge_idx)`, in addition to the corresponding Lap Progress values. Interpolate each lap's elapsed time, speed, throttle, brake, steering, and position at the grid edge points. For threshold point metrics, evaluate edge points inside the segment window, excluding `end_edge_idx`. A Corner Segment's time loss is `(comparison_elapsed_at_end_edge - comparison_elapsed_at_start_edge) - (reference_elapsed_at_end_edge - reference_elapsed_at_start_edge)`; positive means the Comparison Lap lost time to the Reference Lap.

Driver-input metric semantics are deterministic. Within each Corner Segment, brake application point is the first grid point where `brake >= 0.1` remains true for at least 0.10s of elapsed lap time; if no sustained crossing exists, the value is `null` and cannot produce a `brake_point` Lap Loss Cause. Throttle reapplication point is the first grid point after minimum speed where `throttle >= 0.5` remains true for at least 0.20s of elapsed lap time; if no sustained crossing exists, the value is `null` and cannot produce a `throttle_reapplication` cause. Minimum speed is the lowest interpolated speed inside the Corner Segment. Coast duration is the sum of elapsed time inside the Corner Segment where `throttle < 0.1` and `brake < 0.1`.

When more than one Lap Loss Cause clears its threshold for the same Comparison Lap and Corner Segment, select the cause with the largest severity ratio: absolute measured bad-direction difference divided by that cause's threshold. Break exact ties by this fixed order: `brake_point`, `min_speed`, `throttle_reapplication`, `coast_duration`. If the relevant metric is `null`, that cause is not eligible.

Cause metric signs are fixed. For `brake_point`, `signed_delta = comparison_median - reference_value` in Lap Progress and `bad_direction_delta = reference_value - comparison_median` because earlier braking is worse. For `min_speed`, `signed_delta = comparison_median - reference_value` in m/s and `bad_direction_delta = reference_value - comparison_median` because lower minimum speed is worse. For `throttle_reapplication`, `signed_delta = comparison_median - reference_value` in Lap Progress and `bad_direction_delta = comparison_median - reference_value` because later throttle is worse. For `coast_duration`, `signed_delta = comparison_median - reference_value` in seconds and `bad_direction_delta = comparison_median - reference_value` because longer coasting is worse. A cause cannot be selected unless its `bad_direction_delta` is positive and clears the cause threshold.

Global `analysis_status` is assigned after per-corner classification. Use `insufficient_data` when no valid Reference Lap exists, fewer than four Comparison Laps exist, resampling cannot produce usable Corner Segments, or all candidate corners lack enough comparable data. Use `reportable` when exactly one Reportable Delta candidate clears every gate, or when multiple candidates clear every gate and the top candidate's median loss exceeds the second candidate's median loss by at least `single_dominant_margin_s`. Use `no_single_dominant_issue` when two or more Reportable Delta candidates clear every gate but the top does not beat the runner-up by the margin. Use `inconsistent` when significant time loss exists but no Corner Segment has a dominant repeatable Lap Loss Cause. Use `consistent` when Comparison Laps do not show significant repeatable loss versus the Reference Lap.

### 4.6 LLM usage contract

The analysis layer is deterministic and complete without a model. The model only rewrites structured, already-selected coaching evidence into driver-facing language.

Supported coach modes:

| Mode | CLI | What happens | Billing/token source |
|---|---|---|---|
| OpenAI API | `--coach --coach-mode api` | Calls the OpenAI Responses API with the structured deterministic analysis payload and refines the deterministic `coach_report.md` prose. | OpenAI API billing; charged separately from ChatGPT subscriptions. |
| ChatGPT manual | `--coach --coach-mode chatgpt` | Writes `coach_prompt.md`, prints instructions to paste it into ChatGPT, then exits. A separate `--analysis-run <dir> --chatgpt-response <path>` invocation imports the pasted JSON response. | User's ChatGPT subscription message allowance; no API tokens consumed by Open Race Coach. |

Hard rules:

1. No automated ChatGPT web scraping, browser control, session-cookie use, or unofficial ChatGPT API.
2. `chatgpt` mode is a copy/paste workflow by design. It exists because ChatGPT subscriptions and API billing are separate products.
3. In both modes, the prompt includes only session metadata plus the structured deterministic analysis outcome. It never includes raw tick data.
4. ChatGPT manual mode accepts JSON only, optionally wrapped in a single fenced code block. Arbitrary Markdown is rejected.
5. API mode defaults to `gpt-5.4-mini`, `reasoning.effort=low`, and concise text verbosity. `gpt-5.5` is allowed through config for quality comparisons, not as the default.

Prompt evidence boundary: the coach prompt may include only aggregated deterministic evidence for the selected Reportable Delta when `analysis_status=reportable`, or aggregate status evidence when the outcome is non-reportable: `corner_segment_id`, `analysis_status`, `dominant_cause`, Comparison Lap count, median corner loss, robust noise, the selected cause-metric object, optional interquartile range for the relevant cause, meter/percentage provenance, and deterministic consistency-summary labels. It must not include per-tick data or per-lap rows, except as counts or aggregate distributions.

For non-reportable outcomes, coach response JSON must set `corner_segment_id: null`, `instruction: null`, and provide non-empty `why` plus `confidence_note`. For `analysis_status=reportable`, `corner_segment_id` and `instruction` are required and must match the deterministic selected Reportable Delta. Any mismatch between model output and deterministic artifacts is an invalid response, not a prompt for the implementation to reconcile.

Configuration:

- `OPENAI_API_KEY`: required only for `--coach-mode api`.
- `SIMCOACH_OPENAI_MODEL`: optional API model override; default `gpt-5.4-mini`.
- `SIMCOACH_OPENAI_REASONING_EFFORT`: optional API reasoning override; default `low`.

---

## 5. Build phases

### Phase 0 — Repo bootstrap + schema contracts

**Goal:** create the repo skeleton, install project tooling, and write the durable artifact schema docs before any recorder or analyzer implementation.

1. Create the directory layout in §3, `.gitignore`, `pyproject.toml`, and a minimal `README.md`.
2. Write `schema/tick.md`, `schema/lap_record.md`, `schema/session_yaml.md`, `schema/analysis_yaml.md`, `schema/corner_segments.md`, `schema/selected_delta.md`, and `schema/coach_response.md` as human-readable source-of-truth contracts. Each schema doc must include schema version, required fields, nullable fields, enum values, units for numeric fields, artifact owner, write timing, and compatibility/refusal behavior.
3. Keep schema docs and §4 aligned. If implementation discovers a schema change, update the relevant schema doc and §4 in the same change, bump only the affected Artifact Schema Version, and add a `docs/DECISIONS.md` entry.

**Acceptance:** a clean clone has the expected directories and all schema docs; every durable artifact mentioned in §4 has exactly one schema doc; no Phase 1 code starts until these docs exist.

### Phase 1 — AMS2 ingest (do this first, end-to-end)

**Goal:** `python scripts/record.py --sim ams2` runs while AMS2 is open, writes a complete valid session directory.

1. Implement `adapters/base.py`: `SimAdapter` ABC with `connect()`, `read_tick() -> NormalizedTick | None`, `read_session_info() -> dict`, `disconnect()`. Define `NormalizedTick` as a frozen dataclass mirroring §4.1.
2. Implement `adapters/ams2.py` against the **Project CARS 2 shared memory API** (`$pcars2$` memory-mapped file, `ctypes` struct). The PC2 struct layout is community-documented; pin the struct definition in the adapter file with field offsets commented.
3. **AMS2 struct validation script** (`scripts/validate_ams2.py`) — run before trusting anything. Known PC2-derivative quirk: some fields are stale or repurposed. Assert at minimum:
   - `mSpeed` is ~0 when car is stationary in pit
   - `mThrottle`/`mBrake` respond 0→1 to pedal input
   - `mCurrentLap` increments at start/finish line
   - `mLapInvalidated` flips on a deliberate cut
   - world position changes smoothly while driving
4. Implement `ingest/recorder.py`: poll loop at 60 Hz, buffer ticks in memory, flush to Parquet every N seconds (append via row groups or buffer-then-write-once at session end — **buffer-and-write-once is acceptable for v0**; a session is < 100 MB). On Ctrl+C or normal simulator disconnect, attempt graceful finalization from buffered ticks. A hard process kill may lose buffered ticks in v0.
5. Implement `ingest/session.py`: detect Recorded Session start (sim connected + on track), capture metadata, track lap boundaries provisionally for console output, derive final `laps.jsonl` from finalized ticks, and finalize `session.yaml` on exit. If finalization fails after ticks were collected, write whatever partial artifacts can be written safely and mark `session.yaml` with `complete: false` plus `failure_reason`; incomplete Recorded Sessions are retained for debugging but are not analyzable. Treat any change to `sim`, `track`, `car`, `session_type`, `adapter_version`, or any session-owned Artifact Schema Version as a Recorded Session Boundary: finalize the current directory, log `[BRANCH] recorded_session_boundary reason={field_changed}`, and start a new directory automatically if ticks continue. Never write heterogeneous ticks into one `ticks.parquet`. If required metadata becomes unknown mid-session for more than 2.0s, finalize and stop with a clear error rather than guessing.
   - Before starting a Recorded Session, require required metadata to be present and stable for 1.0s. During a Recorded Session, if required metadata becomes unknown for more than 2.0s, finalize the current Recorded Session as incomplete with `failure_reason=metadata_unavailable` rather than guessing continuity.

**Acceptance:** record a real 10-lap AMS2 practice session; session dir contains required session artifacts with `complete: true`; invariants in §4.5 pass; laps.jsonl times match the sim's timing screen within 0.05s.

### Phase 2 — Segmentation + deltas

**Goal:** `python scripts/analyze.py data/sessions/{dir}` creates a complete deterministic Analysis Run directory, including `analysis.yaml`, `corner_segments.json`, `selected_delta.json`, and `coach_report.md`, then prints the selected result summary to the console.

1. `analysis/segmentation.py`: derive Corner Segments from the selected Reference Lap's resampled `pos_x/pos_y` trace via curvature (heading change rate over distance) for each Analysis Run. Smooth → threshold → merge adjacent segments → number Corner Segments sequentially (`C1`, `C2`, …), and write the segment definitions to `analysis/.../corner_segments.json`. **Self-deriving only. No track database. No hand-labeled corner names or official turn numbers.**
2. `analysis/deltas.py`: select the Reference Lap before computing deltas. `--reference best` uses the fastest Valid Lap in the target Recorded Session, tie-broken by lowest lap number. `--reference personal` uses Personal Best Reference search from §4.5. If no eligible Reference Lap exists, create the Analysis Run with `analysis_status: insufficient_data` and a deterministic Coach Report explaining that no valid reference was available. For each Comparison Lap vs. Reference Lap, per Corner Segment compute:
   - time delta (integrate over the corner's fixed `lap_dist_pct` window after resampling)
   - brake application point (`lap_dist_pct` and optional `lap_dist_m` where brake first crosses the sustained `>= 0.1` threshold)
   - minimum speed and its position
   - throttle reapplication point (first sustained `>= 0.5` threshold crossing after minimum speed)
   - coast duration (throttle < 0.1 AND brake < 0.1)
   - Lap Loss Cause: assign only when the Comparison Lap loses at least 0.05s in that Corner Segment versus the Reference Lap. Select the largest threshold-normalized, directionally bad driver-input difference that clears its threshold using the metric semantics and tie-break rules in §4.5: `brake_point` if braking starts earlier by at least 0.005 `lap_dist_pct`; `min_speed` if minimum speed is lower by at least 1.0 m/s; `throttle_reapplication` if throttle resumes later by at least 0.005 `lap_dist_pct`; `coast_duration` if coasting lasts at least 0.10s longer. If no cause clears threshold, assign `unclassified`.
3. `analysis/confidence.py`: per-corner noise = robust dispersion of that corner's time across the driver's own Comparison Laps (median absolute deviation scaled to sigma, with stddev logged for debugging). When the Reference Lap comes from the same Recorded Session, exclude it from the Comparison Lap set. A delta is **reportable** only if all are true:
   - a valid Reference Lap exists
   - ≥ 4 valid Comparison Laps exist
   - the median corner loss is ≥ 0.05s
   - `abs(median_loss) > 1.5 × robust_noise`
   - one dominant cause (`brake_point`, `min_speed`, `throttle_reapplication`, or `coast_duration`) explains ≥ 60% of Comparison Laps for that Corner Segment, with median effect in the expected direction
   - if exactly one Reportable Delta candidate exists, it is reportable without a runner-up margin; if two or more candidates exist, the top candidate must beat the second candidate by ≥ 0.03s or the session is `no_single_dominant_issue`
   Otherwise label the Corner Segment `consistent`, `inconsistent`, or `insufficient_data`. A Corner Segment with significant time loss but no dominant repeatable cause is `inconsistent`, not reportable.

**Acceptance:** on the Phase-1 session, segmentation finds a plausible corner count for the track (sanity-check by eye against the track map), and confidence output is defensible: either one dominant Reportable Delta survives the gate or the Analysis Run is explicitly labeled `consistent`, `inconsistent`, `insufficient_data`, or `no_single_dominant_issue`. The deterministic `coach_report.md` is complete without an LLM: if `analysis_status=reportable`, `## The one thing` contains a terse template instruction from the selected Reportable Delta; otherwise it states that there is no data-supported Coaching Instruction and explains the exact non-reportable status.

### Phase 3 — Coach layer

**Goal:** `analyze.py --coach --coach-mode api` creates the deterministic Analysis Run artifacts, calls the model once, and refines only the prose fields in `analysis/.../coach_report.md`, also updating Recorded Session-level `coach_report.md` as the latest completed render. `analyze.py --analysis-run <dir> --coach --coach-mode api` may also refine or retry an existing deterministic Analysis Run whose `coach_refinement_mode`/`coach_refinement_status` allow API refinement. `--coach-mode chatgpt` writes `coach_prompt.md`, renders the deterministic report, and leaves `coach_refinement_status: awaiting_response`; `analyze.py --analysis-run <dir> --coach --coach-mode chatgpt` may start ChatGPT manual refinement for an existing unrefined deterministic Analysis Run; a later run with `--analysis-run <dir> --chatgpt-response <path>` imports the pasted JSON response, refines `analysis/.../coach_report.md`, and updates Recorded Session-level `coach_report.md` again.

1. `coach/prompt.py`: serialize either the single selected Reportable Delta or the non-reportable `analysis_status` (`consistent`, `inconsistent`, `insufficient_data`, or `no_single_dominant_issue`) into a compact structured block. Include car and track Simulator Labels plus Matching Keys, Recorded Session context, reference mode, confidence labels, dominant cause when selected, and the exact allowed Corner Segment ID when applicable. **Hard rule: no raw telemetry in the prompt.**
2. `coach/coach.py`: support two paths:
   - `api`: call the OpenAI Responses API once, defaulting to `gpt-5.4-mini`, with `reasoning.effort=low`. The model is configurable through `SIMCOACH_OPENAI_MODEL`; `gpt-5.5` is the quality-escalation option. If `--analysis-run <dir>` is supplied, target an existing Analysis Run only when `run_status=complete` and either `coach_refinement_mode=null` with `coach_refinement_status=not_requested` or `coach_refinement_mode=api` with `coach_refinement_status=failed`; leave deterministic artifacts untouched. Treat transport failures, provider errors, and schema-invalid API responses as API refinement failures.
   - `chatgpt`: write `coach_prompt.md` and exit with clear terminal instructions requiring JSON-only output. If `--analysis-run <dir> --coach --coach-mode chatgpt` is supplied, target an existing Analysis Run only when `run_status=complete`, `coach_refinement_mode=null`, and `coach_refinement_status=not_requested`. If `--chatgpt-response <path>` is supplied, require `--analysis-run <dir>` targeting an existing Analysis Run with `coach_refinement_mode=chatgpt` and `coach_refinement_status: awaiting_response` or `invalid_response`, accept JSON directly or JSON inside one fenced code block, validate it against the same response contract as API mode, and render `coach_report.md` only after validation succeeds. Reject arbitrary Markdown.
3. Prompt contract: experienced race-engineer voice; render the selected issue exactly when deterministic analysis provides one; use concrete, actionable phrasing ("brake later into C4 — your reference begins braking at 0.534 lap distance; this session's median is 0.520"). The model must not choose a different Corner Segment, cause, confidence status, or reference. Use meters only when `lap_dist_m_source` supports the conversion, following §4.5 precision rules. If nothing is reportable, say there is no data-supported Coaching Instruction and explain the deterministic status: consistent, inconsistent without a dominant issue, insufficient valid data, or no single dominant issue.
4. Output contract: the model returns a compact JSON object with `analysis_status`, `corner_segment_id`, `instruction`, `why`, and `confidence_note`. `coach.py` owns Markdown rendering; the model does not write the final report directly. When persisted, `coach_response.json` wraps the validated model output with audit metadata:
   ```json
   {
     "coach_response_schema_version": 1,
     "provider": "openai_api",
     "model": "gpt-5.4-mini",
     "reasoning_effort": "low",
     "created_at": "2026-06-12T19:42:00-07:00",
     "response": {
       "analysis_status": "reportable",
       "corner_segment_id": "C4",
       "instruction": "...",
       "why": "...",
       "confidence_note": "..."
     }
   }
   ```
   For ChatGPT manual mode, use `"provider": "chatgpt_manual"` and `"model": "user_reported_or_unknown"`. Do not scrape or infer ChatGPT's backend model, and do not add a v0 CLI path for model-name capture.
5. Validate output against the reportable/non-reportable JSON rules, require the deterministic allowed Corner Segment ID when the response gives a corner-specific instruction, and render the §4.4 report. The LLM may replace prose fields only; it must not change `selected_delta.json`, `corner_segments.json`, `analysis_status`, or data sections. On API failure, schema-invalid API response, or invalid ChatGPT paste, keep the deterministic report and set `coach_refinement_status` to `failed` or `invalid_response` as appropriate — **the analysis must never be blocked by the LLM layer.**

**Acceptance:** report generated for a real session in both `api` mode (with mocked API in tests, live call only during manual validation) and `chatgpt` mode (prompt file + pasted response import). Instruction references the dominant reportable corner when one exists; report renders cleanly in a Markdown viewer.

### Phase 4 — ACC adapter (proves the pattern)

1. Implement `adapters/acc.py` against ACC's three shared-memory pages (Physics / Graphics / Static).
2. **No analysis or coach changes permitted.** Adapter-base or schema changes are allowed only if they remain backward-compatible with AMS2 fixtures and are logged in `DECISIONS.md`. If ACC needs analysis-specific branching, that's an architecture bug — fix the abstraction.
3. Capture raw ACC page fixtures with `scripts/capture_acc_fixture.py` after a live ACC validation run, storing all three page dumps plus their JSON sidecar together.

**Acceptance:** full record → analyze → coach loop on a real ACC session with no analysis/coach code modified.

### Out of scope for v0 (hard NOs — do not build, do not stub beyond an empty file)

- Live/in-session overlays or audio
- Track database, corner-name database, or any per-track config files
- Reference laps from other drivers / community data
- GUI of any kind (CLI + Markdown reports only)
- Multi-driver / cloud sync / accounts
- Setup file parsing or setup advice
- Automated ChatGPT web control, scraping, session-cookie use, or unofficial ChatGPT APIs
- AC adapter (stub file only; defer)

---

## 6. Testing strategy

- **Fixture-first for adapters:** capture raw shared-memory byte dumps to `tests/fixtures/` during Phase 1 validation with `scripts/capture_ams2_fixture.py`; adapter tests parse fixtures, never require a running sim. `tests/test_raw_fixtures.py` skips when no raw live captures are present, and becomes an adapter regression check as soon as reviewed raw sidecars plus `.bin` dumps are committed. Public shared-memory-derived samples are allowed as secondary evidence only when provenance is recorded and limitations are explicit. They may validate real field names and overlapping adapter mappings, but they do not prove raw mmap layout, field offsets, or live simulator behavior.
- **Known public PC2-derived fixture:** `tests/fixtures/pcars2/rest-cars_example.json` is Project CARS 2 shared-memory-derived JSON from `ocindev/rest-cars`. It validates real PC2 field names such as `mSpeed`, `mThrottle`, `mBrake`, `mSteering`, `mGear`, `mRPM`, `mTrackLocation`, `mTrackVariation`, `mTrackLength`, `mLapInvalidated`, and participant world position after mapping into the AMS2/PC2 `ctypes` struct. It does **not** include `mCurrentLapDistance`, and must not be used to claim live lap-distance extraction is validated.
- **Known public lap-distance fixture:** `tests/fixtures/pcars2/crest_example.json` is Project CARS shared-memory-derived JSON from `NLxAROSA/CREST`. It validates real `mCurrentLapDistance`, participant array, car-state, timing, event, and track-length values after mapping into the AMS2/PC2 adapter path. It records `mVersion: 5`, not AMS2's expected live shared-memory version, so it is compatibility evidence only.
- **Synthetic traces for segmentation:** generate a parametric track (e.g., two straights + four arcs) where corner boundaries are known analytically; assert detection within tolerance. Then add one recorded real-lap fixture as a regression anchor.
- **Golden-file test for deltas:** one recorded session fixture with hand-verified expected deltas, asserted with realistic tolerances (`≤ 0.02s` for recorded timing deltas; tighter tolerances only for synthetic data).
- Coach layer: test prompt construction, OpenAI API payload construction, ChatGPT prompt-file generation, ChatGPT response import validation, and output validation with mocked responses. No live API calls in tests.
- CI-runnable on any OS (fixtures decouple tests from Windows/shared memory).

---

## 7. CLI surface (v0, complete)

```
python scripts/record.py --sim ams2 [--out data/sessions]
    # Blocks until Ctrl+C or sim session end. Prints lap times live to console.

python scripts/analyze.py <session_dir> [--coach --coach-mode api|chatgpt] [--reference best|personal] [--sessions-root <dir>]
python scripts/analyze.py <session_dir> --analysis-run <analysis_run_dir> --coach --coach-mode api
python scripts/analyze.py <session_dir> --analysis-run <analysis_run_dir> --coach --coach-mode chatgpt
python scripts/analyze.py <session_dir> --analysis-run <analysis_run_dir> --chatgpt-response <path>
    # 'best' = session best (default). 'personal' = best valid lap across all
    # stored sessions matching exact sim+track+car Matching Keys.
    # api mode requires OPENAI_API_KEY and uses OpenAI API billing.
    # chatgpt mode writes coach_prompt.md for manual paste into ChatGPT.
    # --analysis-run with api retries or applies API refinement only when coach_refinement_mode/status is null/not_requested or api/failed.
    # --analysis-run with chatgpt starts manual refinement only when coach_refinement_mode is null and coach_refinement_status is not_requested.
    # --chatgpt-response retries or completes one existing awaiting_response/invalid_response coach refinement.

    # Refusal rules:
    # --coach requires --coach-mode, and --coach-mode without --coach is invalid.
    # --sessions-root is valid only with --reference personal on a new deterministic Analysis Run.
    # --reference and --sessions-root are invalid with --analysis-run because the deterministic reference is already fixed.
    # --chatgpt-response requires --analysis-run and must not be combined with --coach or --coach-mode.
    # --analysis-run must have the same recorded_session_id as <session_dir>; otherwise refuse instead of refining the wrong Recorded Session.
    # Once coach_refinement_mode is api or chatgpt, the other mode is invalid for that Analysis Run.

python scripts/validate_ams2.py
    # Interactive struct sanity check. Run once per AMS2 update.

python scripts/validate_session.py data/sessions/<session_dir>
    # Offline artifact and invariant validation for a finalized Recorded Session.

python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5
    # Windows-only. Captures raw $pcars2$ mmap bytes plus JSON sidecars for adapter regression fixtures.

python scripts/capture_acc_fixture.py --out tests/fixtures/acc --count 5
    # Windows-only. Captures raw ACC physics/graphics/static page bytes plus JSON sidecars.
```

---

## 8. Logging conventions (mandatory, repo-wide)

This repo will be developed iteratively with a coding agent; logs are the primary debugging interface. Every module follows the structured-prefix convention:

- Create `simcoach/utils/llm_logger.py`: thin wrapper over stdlib `logging` — console + rotating file handler (`logs/`, keep last 5 runs), format `%(asctime)s %(name)s %(levelname)s %(message)s`, exposing `get_logger(__name__)`.
- **Prefixes:** `[START]`/`[END]` on public functions (with inputs/outputs), `[BRANCH]` at decision points, `[STATE]` after data transforms (log **shape and summary, never full data** — a tick DataFrame logged whole will explode any context window), `[ERROR]` with full reproduction context (path, sizes, values), `[LOOP:START/PROGRESS/END]` for iteration (progress every N, never per-item), `[SKIP]` when something is bypassed and why, `[WARN]`/`[RETRY]` for recoverable issues.
- Recorder poll loop: `[LOOP:PROGRESS]` once per lap, not per tick.
- Examples:
  - `[STATE] ticks | shape=(46795, 12) laps=10 t_range=(0.0, 812.4)`
  - `[BRANCH] lap 7 invalid -> sim_invalidated mLapInvalidated=1`
  - `[ERROR] ams2.read_tick | mmap read failed, sim closed? error={e}`

---

## 9. Conventions & hygiene

- `docs/DECISIONS.md`: append-only log — `YYYY-MM-DD | decision | why | alternatives rejected`. Every non-obvious choice goes here (struct quirk workarounds, threshold values, schema changes, model defaults).
- Schema changes bump the relevant per-artifact schema version and get a `DECISIONS.md` entry. `session.yaml` records session-owned artifact versions; `analysis.yaml` records Analysis Run artifact versions. Old artifacts are never migrated in v0; analysis checks versions and refuses politely.
- Type hints everywhere; `ruff` for lint/format; docstrings state units on every numeric parameter.
- Commits: conventional-ish (`feat:`, `fix:`, `docs:`, `test:`), one phase = one or more PRs/commit groups, never one mega-commit.
- Versioning: adapter files carry their own `ADAPTER_VERSION` string, written into `session.yaml`.

---

## 10. Definition of done (v0)

Current local status: the repo can run the CI-runnable test suite without a simulator, and schema/adapter/recorder/analyzer/coach scaffolding exists. This is not v0 completion evidence. The unchecked items below remain the authority.

- [ ] Phase 1–3 acceptance criteria met on real AMS2 sessions
- [ ] Phase 0 schema docs exist and match §4 data contracts
- [ ] Phase 4 acceptance met on a real ACC session with no analysis/coach changes
- [ ] All §4.5 invariants asserted in code and covered by tests
- [ ] Test suite green without a sim running
- [ ] README quickstart verified from a clean clone: enable shared memory → record → analyze → generate report via either OpenAI API mode or ChatGPT manual mode
- [ ] `DECISIONS.md` reflects every threshold, struct workaround, schema change, and model-default decision

---

## 11. Open questions requiring simulator evidence (answer during Phase 1, log in DECISIONS.md)

1. AMS2 shared-memory mode: **Project CARS 2 mode** is assumed; confirm field fidelity vs. Project CARS 1 mode during validation.
2. Lap Progress usability thresholds: tune exact max-gap, reversal-tolerance, long-flat-spot, and coverage thresholds from AMS2 fixtures before freezing `bad_lap_progress` behavior.
3. Corner-segmentation thresholds (curvature cutoff, min segment length, merge distance): tune on 2–3 real tracks, then freeze and document.

---

## 12. Current validation ledger

This section is a working evidence map for the build. It prevents local tests, public fixture checks, and real simulator acceptance from being blurred together.

### Proven locally without a simulator

- Schema docs exist for every v0 durable artifact listed in §4.
- AMS2/PC2 and ACC adapters expose fixture-friendly snapshot parsing seams.
- The AMS2 adapter has offset tests for the locally pinned `ctypes` struct and maps speed, inputs, gear, rpm, lap invalidation, vehicle state, track/car metadata, track length, lap progress, and world position into `NormalizedTick` / `session.yaml` contracts.
- `scripts/capture_ams2_fixture.py` can summarize raw AMS2/PC2 fixture bytes into a provenance sidecar in CI-safe unit tests; the actual byte capture path still requires Windows shared memory.
- `scripts/capture_acc_fixture.py` can summarize raw ACC physics/graphics/static page bytes into a provenance sidecar in CI-safe unit tests; the actual byte capture path still requires Windows shared memory.
- `tests/test_raw_fixtures.py` is ready to parse committed raw AMS2/ACC sidecars and byte dumps in CI; it currently skips because no reviewed live raw captures are committed.
- The public `rest-cars` Project CARS 2 shared-memory-derived JSON sample maps into the AMS2/PC2 adapter path for the fields it contains.
- Recorder finalization writes `session.yaml`, `ticks.parquet`, and `laps.jsonl`, derives final lap records from finalized ticks, and invalidates laps for bad Lap Progress, non-running Vehicle State, teleport/reset evidence, missing tick ranges, and simulator invalidation evidence.
- `scripts/validate_session.py` re-reads finalized Recorded Session artifacts and validates required `session.yaml` metadata, schema support, completion, tick invariants, full tick-row coverage by lap ranges, lap IDs, derived lap timing, and lap invalidity consistency before analysis.
- Deterministic analysis, personal-best reference selection, segmentation, delta/cause selection, confidence gating, deterministic report rendering, OpenAI API refinement plumbing, ChatGPT manual prompt/response workflow, and CLI refusal rules are covered by CI-runnable tests with synthetic fixtures and mocked model responses.

### Not proven until live simulator validation

- Raw AMS2 `$pcars2$` mmap layout and field fidelity against the installed AMS2 build.
- Whether AMS2 `mCurrentLap`, `mCurrentLapDistance`, `mLapInvalidated`, `mGameState`, `mSessionState`, `mPitMode`, and `mWorldPosition` have the exact semantics assumed by the adapter.
- Whether a real 10-lap AMS2 practice recording produces complete artifacts whose lap times match the simulator timing screen within 0.05s.
- Whether curvature segmentation thresholds produce plausible Corner Segment counts on real AMS2 tracks.
- Whether a real ACC recording can complete the record → analyze → coach loop without analysis or coach changes.
- README quickstart from a genuinely clean clone on Windows with simulator shared memory enabled.

### Live validation commands

```bash
uv run python scripts/validate_ams2.py
uv run python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5
uv run python scripts/record.py --sim ams2 --out data/sessions
uv run python scripts/validate_session.py data/sessions/<ams2_session_dir>
uv run python scripts/analyze.py data/sessions/<ams2_session_dir>
uv run python scripts/analyze.py data/sessions/<ams2_session_dir> --coach --coach-mode chatgpt
uv run python scripts/record.py --sim acc --out data/sessions
uv run python scripts/capture_acc_fixture.py --out tests/fixtures/acc --count 5
uv run python scripts/validate_session.py data/sessions/<acc_session_dir>
uv run python scripts/analyze.py data/sessions/<acc_session_dir>
```

After each live validation finding, update `docs/DECISIONS.md` with the evidence and adjust this spec only when the contract changes.
