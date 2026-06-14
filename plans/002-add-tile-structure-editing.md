# Plan 002: Add explicit Coaching Tile structure editing

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report; do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 859bdfd..HEAD -- docs/crt_terminal_text.html CONTEXT.md`
> If any in-scope file changed since this plan was written, compare the "Current state" excerpts against the live code before proceeding. If they do not match in meaning, stop and report.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `859bdfd`, 2026-06-13

## Why this matters

The current editor lets the user edit text inside the active tile but barely lets them edit the board. Layout changes implicitly hide or reveal tiles, new tiles appear only as side effects of choosing a larger layout, and there is no visible list, add, duplicate, delete, or reorder operation. That is not a competent board editor; it is a set of hidden array mutations. Confidence: high.

## Current state

Relevant excerpts:

```js
docs/crt_terminal_text.html:717
const initialTiles = [
  makeTile("instruction", "selected_delta", sampleSelectedDelta),
  makeTile("evidence", "selected_delta", sampleSelectedDelta),
  makeTile("checked_areas", "analysis_summary", "C1 consistent\nC2 reportable\nC3 consistent"),
  makeTile("session_status", "analysis_summary", "Recorded Session: complete\nValid laps: 6\nReference: session best\nOutcome: reportable")
];
```

```js
docs/crt_terminal_text.html:846
function render() {
  const visibleTiles = state.tiles.slice(0, layoutTileCount(state.layout));
  board.className = `debrief-board layout-${state.layout}`;
  board.innerHTML = "";
  visibleTiles.forEach((tile, index) => {
    const element = document.createElement("button");
    ...
  });
}
```

```js
docs/crt_terminal_text.html:897
function ensureTileCount(count) {
  while (state.tiles.length < count) {
    state.tiles.push(makeTile("raw_note", "manual", "Driver note"));
  }
  if (!state.tiles.slice(0, count).some((tile) => tile.id === activeTileId)) {
    activeTileId = state.tiles[0].id;
  }
}
```

```js
docs/crt_terminal_text.html:906
function layoutTileCount(layout) {
  return layout === "focus" ? 1 : layout === "split" ? 2 : 4;
}
```

Domain vocabulary from `CONTEXT.md`:

- A Debrief Board is a driver-facing composition of Coaching Tiles.
- A Coaching Tile has a specific coaching purpose.
- A Debrief Layout Preset constrains composition; it should not become a generic freeform canvas.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| HTML parse | `python3 -m html.parser docs/crt_terminal_text.html` | exit 0 |
| Python tests | `uv run pytest` | all tests pass |
| Lint | `uv run ruff check .` | exit 0, no violations |

## Scope

**In scope**:

- `docs/crt_terminal_text.html`
- `CONTEXT.md` only if the existing Debrief Board vocabulary is insufficient

**Out of scope**:

- Arbitrary drag-and-drop canvas positioning
- Resizable floating windows
- Python analysis artifacts
- Any change to the meaning of Reference Lap, Reportable Delta, or Coaching Instruction

## Git workflow

- Branch: `codex/002-add-tile-structure-editing`
- Commit style: `feat: add debrief tile structure editing`
- Do not include generated screenshots unless explicitly requested.

## Steps

### Step 1: Add a visible tile list

Add a compact tile list to the editor panel above the Tile Type controls. Each row should show:

- Tile ordinal, for example `1`
- Tile label, for example `Instruction`
- Edited state, using existing `tileMeta(tile)` or a shorter equivalent
- Active state with `aria-current="true"` or an equivalent accessible marker

Clicking a row must set `activeTileId`, render the board, and sync the editor.

Keep the UI dense; this is an operational tool, not a landing page.

**Verify**: `python3 -m html.parser docs/crt_terminal_text.html` exits 0.

### Step 2: Add tile add, duplicate, delete, and move controls

Add controls near the tile list:

- `Add Tile`
- `Duplicate`
- `Delete`
- `Move Up`
- `Move Down`

Behavior:

- `Add Tile`: append `makeTile("raw_note", "manual", "Driver note")`, activate it, save, render, sync.
- `Duplicate`: clone the active tile, generate a new `id`, insert after active tile, activate clone, save, render, sync.
- `Delete`: remove active tile if there is more than one tile. If there is one tile, do nothing and set status `At least one tile required`.
- `Move Up` / `Move Down`: reorder `state.tiles`, keep the same active tile, save, render, sync.

Do not allow empty boards.

**Verify**: Use `rg -n "Add Tile|Duplicate|Delete|Move Up|Move Down|At least one tile required" docs/crt_terminal_text.html` and confirm all controls/statuses exist.

### Step 3: Make layout capacity explicit

Right now, layout capacity silently slices `state.tiles`. Preserve Debrief Layout Presets, but surface the capacity:

- Show a status such as `Showing 4 of 6 tiles` when the selected layout displays fewer tiles than exist.
- Ensure hidden tiles remain editable through the tile list.
- When the active tile is hidden by the current layout, selecting it in the tile list should either:
  - temporarily show a clear status `Tile hidden by current layout`, or
  - switch to the smallest layout that can display it. Prefer the first option because layout presets should not change unexpectedly.

**Verify**: In a browser, create six tiles, choose `Split`, select tile 5 in the list, and confirm the editor still edits tile 5 while the board clearly says it is hidden by the current layout.

### Step 4: Preserve stable tile identity

Do not derive identity from tile position. Keep `id` as the tile identity field. Ensure add/duplicate/reorder/delete operations do not reuse IDs. The existing `makeTile` generates `tile-${Math.random().toString(16).slice(2)}`; that is acceptable for a local static artifact, but if Plan 001 has landed, imported tile IDs must still be preserved unless duplicated.

**Verify**: Export/import verification from Plan 001, if available, still round-trips tile IDs. If Plan 001 is not landed, manually inspect `state.tiles.map(tile => tile.id)` in DevTools after duplicate/reorder.

## Test plan

- Manual browser test: add tile, edit text, switch layouts, confirm text persists.
- Manual browser test: duplicate a source-derived tile and confirm original and duplicate can diverge independently.
- Manual browser test: delete active tile and confirm active tile moves to an adjacent remaining tile.
- Manual browser test: reorder tiles and confirm the visible board order changes.

## Done criteria

- [ ] `python3 -m html.parser docs/crt_terminal_text.html` exits 0.
- [ ] `uv run pytest` exits 0.
- [ ] `uv run ruff check .` exits 0.
- [ ] User can add, duplicate, delete, and reorder Coaching Tiles without changing source code.
- [ ] Hidden tiles remain discoverable and editable.
- [ ] Empty board state is impossible through the UI.
- [ ] No files outside the in-scope list are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- The file has already moved to a framework/build system; this plan assumes a no-build static HTML file.
- The maintainer asks for freeform drag/drop resizing. That contradicts the current Debrief Layout Preset vocabulary and needs a separate design decision.
- Tile structure editing requires schema changes to deterministic Analysis Run artifacts. It should not.

## Maintenance notes

Reviewers should reject any implementation that turns the Debrief Board into a generic canvas. The domain model says layout presets constrain composition. The right improvement is explicit tile management inside presets, not arbitrary windows.
