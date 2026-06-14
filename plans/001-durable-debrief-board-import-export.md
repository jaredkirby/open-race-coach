# Plan 001: Add durable Debrief Board import and export

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report; do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 859bdfd..HEAD -- docs/crt_terminal_text.html CONTEXT.md docs/DECISIONS.md`
> If any in-scope file changed since this plan was written, compare the "Current state" excerpts against the live code before proceeding. If they do not match in meaning, stop and report.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `859bdfd`, 2026-06-13

## Why this matters

The current editor is not durable enough to deserve the name Debrief Board editor. It autosaves to browser `localStorage` only, which is a private browser cache rather than a portable Open Race Coach artifact. A local-first tool should let a driver save, reload, and share a board JSON file without trusting one browser profile. Confidence: high.

## Current state

- `docs/crt_terminal_text.html` is a self-contained static Debrief Board editor.
- `CONTEXT.md` defines Debrief Board, Coaching Tile, Tile Source Snapshot, Tile Display Text, and Edited Coaching Tile as project vocabulary.
- `docs/DECISIONS.md` is append-only decision history; add an entry only if this plan creates a new durable artifact convention.

Relevant current excerpts:

```html
docs/crt_terminal_text.html:570
<section class="control-group" aria-label="Layout presets">
  ...
  <button type="button" id="resetBoardButton">Reset</button>
</section>
```

```js
docs/crt_terminal_text.html:664
const storageKey = "open-race-coach-crt-debrief-board-v1";
```

```js
docs/crt_terminal_text.html:1178
function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved?.tiles?.length) {
      return {
        ...defaultState(),
        ...saved,
        theme: { ...defaults, ...(saved.theme || {}) }
      };
    }
  } catch {
    return defaultState();
  }
  return defaultState();
}

function saveState() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(state));
  } catch {
    copyStatus.textContent = "Storage blocked";
  }
}
```

Repo conventions to preserve:

- Static HTML artifacts in `docs/` must open directly in a browser with no build step, network request, or external asset.
- Use exact project vocabulary from `CONTEXT.md`: Debrief Board, Coaching Tile, Tile Source Snapshot, Tile Display Text, Edited Coaching Tile.
- Do not modify Python analysis code for this UI-only feature.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| HTML parse | `python3 -m html.parser docs/crt_terminal_text.html` | exit 0 |
| Python tests | `uv run pytest` | all tests pass |
| Lint | `uv run ruff check .` | exit 0, no violations |

## Scope

**In scope**:

- `docs/crt_terminal_text.html`
- `CONTEXT.md` only if a small vocabulary addition is required
- `docs/DECISIONS.md` only if you introduce a durable board JSON schema decision

**Out of scope**:

- `simcoach/**`
- `scripts/**`
- `schema/**` unless the maintainer explicitly decides Debrief Board JSON is a canonical analysis artifact. This plan treats board JSON as a portable presentation artifact, not deterministic analysis truth.
- Adding a build system, package manager dependency, server, or remote storage.

## Git workflow

- Branch: `codex/001-durable-debrief-board-import-export`
- Commit style: use the repo's conventional-ish style, for example `feat: add debrief board import export`
- Do not add generated `.scratch/` screenshots unless the operator explicitly requests them.

## Steps

### Step 1: Add a board artifact envelope

Define a `boardArtifactVersion` constant in the script, for example `const boardArtifactVersion = 1;`. Implement two pure functions:

- `serializeBoardArtifact()` returns a JSON-safe object with:
  - `artifact_type: "open_race_coach_debrief_board"`
  - `artifact_version: 1`
  - `created_at`: `new Date().toISOString()`
  - `layout`
  - `theme`
  - `tiles`, including each tile's `id`, `tile_type`, `source_type`, `source_snapshot`, and `display_text`
- `parseBoardArtifact(text)` parses JSON, validates the envelope, validates that `tiles` is a non-empty array, merges `theme` with `defaults`, and returns a state object compatible with the existing `state`.

Validation must reject unknown `artifact_type`, unsupported `artifact_version`, missing tiles, and tile objects without string `tile_type`, `source_type`, `source_snapshot`, or `display_text`.

**Verify**: `python3 -m html.parser docs/crt_terminal_text.html` exits 0.

### Step 2: Add import/export controls to the editor panel

Add a new control group near the reset button or panel title:

- `Export Board` button
- `Import Board` button
- hidden `<input type="file" accept="application/json,.json">`

Export behavior:

- Create a `Blob` from `JSON.stringify(serializeBoardArtifact(), null, 2)`.
- Create an object URL.
- Download with a deterministic-ish filename such as `open-race-coach-debrief-board-YYYYMMDD-HHMMSS.json`.
- Revoke the object URL after triggering the download.
- Set the status text to `Board exported`.

Import behavior:

- `Import Board` clicks the hidden file input.
- On file selection, read the file as text with `FileReader`.
- Call `parseBoardArtifact`.
- Replace `state`, set `activeTileId` to the first tile, save to localStorage, apply theme, render, sync editor.
- Set status text to `Board imported`.
- On parse/validation failure, leave the current state untouched and set status text to `Import failed`.

**Verify**: `python3 -m html.parser docs/crt_terminal_text.html` exits 0.

### Step 3: Make localStorage an autosave cache, not the only save path

Keep existing autosave behavior, but update nearby labels/status so the UI does not imply browser storage is a durable project artifact. Suggested labels:

- Button: `Export Board`
- Button: `Import Board`
- Status line after autosave failures: `Browser autosave blocked`

Do not remove `localStorage`; it is useful as a session cache.

**Verify**: `rg -n "Storage blocked|Board exported|Board imported|Import failed|autosave" docs/crt_terminal_text.html` shows the new status strings and no stale `Storage blocked` text.

### Step 4: Add a tiny in-browser verification harness

Because this is a standalone HTML file, avoid a heavy JS test framework. Add a developer-only self-test hook that runs only when the URL contains `?selftest=1`. It should:

- Serialize the current board artifact.
- Parse it back.
- Confirm the parsed layout equals the original layout.
- Confirm the parsed tile count equals the original tile count.
- Set `window.__orcSelfTest = { ok: true }` on success or `{ ok: false, error: "..." }` on failure.

Do not render self-test UI in the normal page.

**Verify**: Open the file in a browser with `?selftest=1` and confirm in DevTools that `window.__orcSelfTest.ok === true`. If a browser is unavailable, report that this manual browser gate was not run.

## Test plan

- Manual browser test: edit tile text, export JSON, reset, import JSON, confirm the edited Tile Display Text returns.
- Manual browser test: import malformed JSON and confirm the current board remains unchanged.
- Manual browser test: import JSON with wrong `artifact_type` and confirm it fails.
- Existing repo tests remain Python-only; still run them because this repo expects `uv run pytest` as the full regression gate.

## Done criteria

- [ ] `python3 -m html.parser docs/crt_terminal_text.html` exits 0.
- [ ] `uv run pytest` exits 0.
- [ ] `uv run ruff check .` exits 0.
- [ ] The editor can export a board JSON file and import it back without a server.
- [ ] Failed imports do not mutate the active board.
- [ ] `localStorage` still works as autosave, but it is no longer the only durable save path.
- [ ] No files outside the in-scope list are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- The HTML has already gained import/export controls and this plan's target behavior is obsolete.
- Browser security restrictions prevent direct file export/import when opened as a local `file://` URL.
- The maintainer wants Debrief Board JSON to become a canonical Analysis Run artifact; that requires schema docs and a separate design decision.

## Maintenance notes

Reviewers should scrutinize artifact validation. A weak importer that accepts arbitrary shapes will become a quiet corruption path for board state. Keep the board artifact separate from deterministic Analysis Run artifacts unless the maintainer explicitly promotes it.
