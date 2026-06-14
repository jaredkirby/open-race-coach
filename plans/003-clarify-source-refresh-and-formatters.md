# Plan 003: Clarify source refresh semantics and fix source-derived formatters

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report; do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 859bdfd..HEAD -- docs/crt_terminal_text.html schema/selected_delta.md CONTEXT.md`
> If any in-scope file changed since this plan was written, compare the "Current state" excerpts against the live code before proceeding. If they do not match in meaning, stop and report.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `859bdfd`, 2026-06-13

## Why this matters

The editor currently blurs three distinct operations: changing a tile's type/source metadata, refreshing Tile Display Text from the Tile Source Snapshot, and preserving a user's edited Tile Display Text. Worse, the selected-delta formatter uses old or wrong enum names and forces every cause metric into an `m/s` instruction. That can produce false driver-facing text for brake point, throttle reapplication, and coast duration. Confidence: high.

## Current state

Current UI controls:

```html
docs/crt_terminal_text.html:582
<section class="control-group" aria-label="Tile controls">
  ...
  <select id="tileType">
    <option value="instruction">Instruction</option>
    ...
  </select>
  ...
  <select id="sourceType">
    <option value="manual">Manual</option>
    <option value="coach_report">Coach Report</option>
    <option value="selected_delta">Selected Delta</option>
    <option value="analysis_summary">Analysis Summary</option>
  </select>
  ...
  <button type="button" id="applyTypeButton">Apply Format</button>
  <button type="button" id="resetTileButton">Reset Tile</button>
  <textarea class="source-editor" id="sourceEditor" ...></textarea>
</section>
```

Changing the tile type/source currently mutates metadata only:

```js
docs/crt_terminal_text.html:762
tileType.addEventListener("change", () => {
  const tile = activeTile();
  if (!tile) return;
  tile.tile_type = tileType.value;
  saveState();
  render();
  syncEditor();
});

sourceType.addEventListener("change", () => {
  const tile = activeTile();
  if (!tile) return;
  tile.source_type = sourceType.value;
  saveState();
  render();
  syncEditor();
});
```

Refresh behavior is hidden behind two vague buttons:

```js
docs/crt_terminal_text.html:797
document.getElementById("applyTypeButton").addEventListener("click", () => {
  const tile = activeTile();
  if (!tile) return;
  tile.display_text = formatTile(tile.tile_type, tile.source_type, tile.source_snapshot);
  saveState();
  render();
  syncEditor();
});

document.getElementById("resetTileButton").addEventListener("click", () => {
  const tile = activeTile();
  if (!tile) return;
  tile.display_text = formatTile(tile.tile_type, tile.source_type, tile.source_snapshot);
  saveState();
  render();
  syncEditor();
});
```

The selected-delta formatter currently assumes an `m/s` bad-direction delta for every cause:

```js
docs/crt_terminal_text.html:1003
const corner = delta.corner_segment_id || "unknown corner";
const cause = causeLabel(delta.dominant_cause);
const loss = numberText(delta.median_corner_loss_s, "s");
const repeat = percentText(delta.dominant_cause_lap_fraction);
const start = range.start_lap_dist_m;
const end = range.end_lap_dist_m;
const speed = metric.bad_direction_delta;
return {
  instruction: parsed.analysis_status === "reportable"
    ? `Carry ${numberText(speed, "m/s")} more ${cause} through ${corner}.`
    : "No data-supported Coaching Instruction.",
```

It also uses enum labels that do not match the current schema:

```js
docs/crt_terminal_text.html:1109
function causeLabel(value) {
  const labels = {
    min_speed: "minimum speed",
    brake_earlier: "brake point",
    throttle_later: "throttle reapplication",
    coast_longer: "coast duration"
  };
  return labels[value] || String(value || "selected cause").replace(/_/g, " ");
}
```

Current schema facts:

```md
schema/selected_delta.md:18
- `analysis_status`: `reportable`, `consistent`, `inconsistent`, `insufficient_data`, `no_single_dominant_issue`
- `dominant_cause`: `brake_point`, `min_speed`, `throttle_reapplication`, `coast_duration`
- `cause_metric.unit`: `lap_dist_pct`, `m/s`, `s`
```

```md
schema/selected_delta.md:54
`cause_metric` stores `metric`, `unit`, `reference_value`, `comparison_median`, `signed_delta`, and positive `bad_direction_delta`. `brake_point` and `throttle_reapplication` use `lap_dist_pct`; `min_speed` uses `m/s`; `coast_duration` uses seconds.
```

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| HTML parse | `python3 -m html.parser docs/crt_terminal_text.html` | exit 0 |
| Python tests | `uv run pytest` | all tests pass |
| Lint | `uv run ruff check .` | exit 0, no violations |

## Scope

**In scope**:

- `docs/crt_terminal_text.html`

**Out of scope**:

- Changing `schema/selected_delta.md`
- Changing deterministic analysis output
- Letting the UI invent a Coaching Instruction not supported by `selected_delta.json`
- Adding external Markdown or JSON parsing libraries

## Git workflow

- Branch: `codex/003-clarify-source-refresh-and-formatters`
- Commit style: `fix: correct debrief tile source formatting`

## Steps

### Step 1: Rename and separate refresh controls

Replace `Apply Format` and `Reset Tile` with clearer operations:

- `Refresh From Source`: recompute Tile Display Text from Tile Source Snapshot and current tile/source type.
- `Revert Display Edits`: same behavior as refresh, but visible only or enabled only when the tile is edited.

It is acceptable for both buttons to call the same function initially, but their labels and status messages must make the destructive nature explicit. After refresh/revert, set status to `Display text refreshed from source`.

When Tile Type or Source changes, do not silently overwrite edited Tile Display Text. Set status to `Format changed; refresh when ready` if `tileMeta(tile)` would mark the tile edited.

**Verify**: `rg -n "Refresh From Source|Revert Display Edits|Display text refreshed from source|Format changed; refresh when ready|Apply Format|Reset Tile" docs/crt_terminal_text.html` shows the new labels and no stale button labels.

### Step 2: Add formatter helpers for cause metrics

Implement helpers:

- `formatCauseInstruction(delta, metric, corner)` returns driver-facing text based on `metric.metric` or `delta.dominant_cause`.
- `formatCauseValue(metric)` formats `bad_direction_delta` using `metric.unit`.
- `formatLapDistancePct(value)` formats `lap_dist_pct` as a percentage or lap-distance point, not meters.

Required behavior:

- `min_speed`, unit `m/s`: "Carry X m/s more minimum speed through C2."
- `brake_point`, unit `lap_dist_pct`: "Brake X lap-distance points earlier/later" must respect the schema sign convention. For a worse comparison lap, `bad_direction_delta` is how much earlier the comparison brakes than the reference; driver-facing correction should say to brake later by that amount, not "more brake point".
- `throttle_reapplication`, unit `lap_dist_pct`: driver-facing correction should say to pick up throttle earlier by that amount.
- `coast_duration`, unit `s`: driver-facing correction should say to reduce coast time by X seconds.

Do not use meter language unless `segment_range.lap_dist_m_source` permits it and meter fields are finite. For `lap_dist_pct`, avoid false precision; one decimal percentage point is enough.

**Verify**: In DevTools or a temporary console snippet, call the helper logic through sample selected-delta objects for all four causes and confirm the returned instruction does not use the wrong unit.

### Step 3: Update enum mapping to schema version 1

Update `causeLabel` to support current schema enum values:

- `brake_point`
- `min_speed`
- `throttle_reapplication`
- `coast_duration`

You may keep backward-compatible aliases for the old names if desired, but current schema names must be first-class.

**Verify**: `rg -n "brake_earlier|throttle_later|coast_longer" docs/crt_terminal_text.html` returns no matches unless they appear only in an explicit backward-compatibility alias block.

### Step 4: Add self-test cases for formatter correctness

If Plan 001's `?selftest=1` hook exists, add formatter cases there. If not, add a minimal `runSelfTest()` guarded by `new URLSearchParams(location.search).has("selftest")`.

Self-test must cover:

- `min_speed` with `unit: "m/s"`
- `brake_point` with `unit: "lap_dist_pct"`
- `throttle_reapplication` with `unit: "lap_dist_pct"`
- `coast_duration` with `unit: "s"`
- non-reportable `analysis_status` with `selected_delta: null`

Set `window.__orcSelfTest.ok = true` only if all formatter cases pass.

**Verify**: Open `docs/crt_terminal_text.html?selftest=1` and confirm `window.__orcSelfTest.ok === true`. If a browser is unavailable, report that this manual browser gate was not run.

## Test plan

- Manual browser test: choose a tile, edit Tile Display Text, change Tile Type, confirm the edit is not overwritten automatically.
- Manual browser test: click `Refresh From Source`, confirm display text is recomputed and edited state clears.
- Manual browser test: paste selected-delta JSON for each cause and confirm instruction units are honest.
- Manual browser test: paste non-reportable selected-delta JSON and confirm no Coaching Instruction is fabricated.

## Done criteria

- [ ] `python3 -m html.parser docs/crt_terminal_text.html` exits 0.
- [ ] `uv run pytest` exits 0.
- [ ] `uv run ruff check .` exits 0.
- [ ] Formatter supports current `schema/selected_delta.md` enum names and units.
- [ ] Editing source/type metadata no longer silently overwrites Tile Display Text.
- [ ] Refresh/revert behavior is explicitly labeled and status-reported.
- [ ] `?selftest=1` covers the four selected cause metrics and non-reportable status.
- [ ] No files outside the in-scope list are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- Live `schema/selected_delta.md` has changed the cause enum or unit model since this plan was written.
- The maintainer wants the formatter to use natural-language Coach Report prose instead of selected-delta-derived templates for all reportable cases. That is a product decision, not a small formatter fix.
- The selected-delta data needed for honest wording is absent from the artifact; do not invent values.

## Maintenance notes

This file must keep deterministic ownership straight. Source-derived tile text may render deterministic evidence, but it must not reinterpret telemetry, select a different Corner Segment, or invent official corner names. Review every formatter change against `schema/selected_delta.md`.
