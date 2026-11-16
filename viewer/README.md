# Local Telemetry Viewer

`node viewer/build.mjs --demo` creates an offline HTML page under `.scratch/telemetry-viewer/`. The demo uses invented signals, lap times, and a path defined in `demo.mjs`. It is not Live Simulator Validation evidence. No package install, server, browser network requests, or Codex workspace files are needed.

To view a reviewed export from a Recorded Session:

```bash
node viewer/build.mjs --input <export.json> [--timing <elapsed-timing.json>]
```

Open the path printed by the command. The real preview embeds the full JSON input. Do not share, stage, or host that HTML or the input files. The builder reads inputs without modifying them, writes output only under ignored `.scratch/`, and leaves the public site unchanged. Store input files outside the tracked tree, such as under `.scratch/`.

## Input

The viewer reads display exports, not canonical `ticks.parquet` or `laps.jsonl`. Version 1 is intended for short debriefs with a same-session Reference Lap and at least one Comparison Lap:

- `format: "orc_visual_preview"`, `version: 1`, `recorded_session_id`, and the `sim`, `track`, and `car` display labels.
- `progress`: a strictly increasing 0-to-1 Lap Progress grid, shared by all series.
- `laps`: unique `lap`, `lap_id`, `valid`, `invalid_reason`, `lap_time_s`, and `lap_time_source`. Each Valid Lap has `signals.speed` in m/s and `signals.brake` and `signals.throttle` as fractions. Each series matches the grid; `null` is a gap. Excluded records have no comparison delta.
- `analysis`: the pinned Analysis Run's `status`, `reference_lap_id`, and `comparison_lap_count`. Optional `coaching_instruction` is displayed only for `reportable`. The viewer does not select or rewrite the instruction.
- `path.x` and `path.y`: Reference Lap ground-plane coordinates in meters, on the same grid. `segments` are optional `{id, start, end}` Corner Segments on that grid. Optional `scope_note`, `units`, `transformations`, `limitations`, and `validation_provenance` retain display provenance.

The optional timing input has the same `recorded_session_id`, `reference_lap_id`, and `progress`, plus `laps` with `lap`, `lap_id`, and `elapsed_s` for the same Valid Laps. Supply `units.elapsed_s: "s"`, `transformations` that describe the elapsed origin, `limitations`, and `validation_provenance`. Use comparable lap-relative elapsed seconds, not speed-derived estimates. Nulls stay gaps. Without timing coverage the delta chart takes no space.

## Controls and Checks

Main opens with traces, track, and laps. Drag on a trace to zoom all traces; select another Comparison Lap without losing the range. **Reset zoom** restores the full lap. **BEZEL** or `bezel on` restores the frame; it is off by default.

```bash
node --test tests/telemetry_viewer.test.mjs
node tests/docs_index_browser_test.mjs --viewer-only
```

The browser check uses synthetic data and the repository's Chrome/Chromium harness (`ORC_CHROME_PATH` can select the executable). It never reads a real recording or generated private preview.
