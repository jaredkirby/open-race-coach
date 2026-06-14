# Plan 004: Harden editor panel focus, keyboard, and mobile behavior

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report; do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 859bdfd..HEAD -- docs/crt_terminal_text.html`
> If any in-scope file changed since this plan was written, compare the "Current state" excerpts against the live code before proceeding. If they do not match in meaning, stop and report.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `859bdfd`, 2026-06-13

## Why this matters

The editor panel is visually closed by `transform: translateX(100%)`, but its buttons, selects, color input, sliders, and textareas remain in the document focus order. Keyboard users can tab into an offscreen editor; mobile users get a dimmed board behind the editor without a robust modal/focus model. This is a real editing bug, not polish. Confidence: high.

## Current state

The panel is always present and only translated:

```html
docs/crt_terminal_text.html:557
<button class="editor-toggle" id="editorToggle" type="button" aria-controls="editorPanel" aria-expanded="false">Edit</button>
...
<aside class="editor-panel" id="editorPanel" aria-label="Debrief board editor">
```

```css
docs/crt_terminal_text.html:339
.editor-panel {
  position: fixed;
  z-index: 10;
  top: 0;
  right: 0;
  width: var(--editor-width);
  height: 100%;
  ...
  transform: translateX(100%);
  transition: transform 180ms ease;
}

body.editor-open .editor-panel {
  transform: translateX(0);
}
```

The toggle currently changes only body class and `aria-expanded`:

```js
docs/crt_terminal_text.html:731
editorToggle.addEventListener("click", () => {
  const open = document.body.classList.toggle("editor-open");
  editorToggle.setAttribute("aria-expanded", String(open));
});
```

Mobile open state dims the board but does not formalize focus behavior:

```css
docs/crt_terminal_text.html:508
body.editor-open .debrief-board {
  right: 10px;
}

body.editor-open .terminal-frame {
  opacity: 0.32;
}
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

- Redesigning the CRT visual style
- Adding external accessibility libraries
- Replacing the editor panel with a different application architecture

## Git workflow

- Branch: `codex/004-harden-editor-focus-and-mobile-shell`
- Commit style: `fix: harden debrief editor focus behavior`

## Steps

### Step 1: Introduce explicit editor open/close functions

Replace the inline toggle with functions:

- `setEditorOpen(open)`
- `openEditor()`
- `closeEditor()`

`setEditorOpen` must:

- Toggle `body.editor-open`.
- Set `editorToggle.aria-expanded`.
- Set `editorPanel.inert = !open` when supported.
- Set `editorPanel.setAttribute("aria-hidden", String(!open))`.
- When opening, move focus to the first useful editor control, preferably the tile text editor or tile list if Plan 002 has landed.
- When closing, return focus to `editorToggle`.

Initialize the closed state after element lookup: `setEditorOpen(false)`.

**Verify**: `python3 -m html.parser docs/crt_terminal_text.html` exits 0.

### Step 2: Add Escape close behavior

Add a `keydown` listener:

- If `event.key === "Escape"` and the editor is open, close it and prevent default.
- Do not intercept Escape when the editor is already closed.

**Verify**: In a browser, open the editor, focus inside the textarea, press Escape, and confirm focus returns to the `Edit` button.

### Step 3: Prevent offscreen focus when inert is unavailable

For browsers without `HTMLElement.prototype.inert`, add a small fallback:

- Collect focusable controls inside `editorPanel`.
- When closed, set `tabindex="-1"` on focusable descendants while remembering any original `tabindex`.
- When open, restore original tabindex values.

Keep this local to the static file; do not add a dependency.

**Verify**: In a browser console after closing the editor, run:

```js
Array.from(document.querySelectorAll("#editorPanel button, #editorPanel input, #editorPanel select, #editorPanel textarea"))
  .every((node) => node.closest("#editorPanel").inert || node.tabIndex === -1)
```

Expected result: `true`.

### Step 4: Make mobile open state deliberate

On mobile widths, the panel behaves like a modal drawer. Add enough state for clarity:

- Give `.editor-panel` full viewport height as it has now.
- Keep the board dimmed, but ensure board tiles cannot be clicked behind the open editor. A simple CSS rule is acceptable: `body.editor-open .terminal-frame { pointer-events: none; }` and `.editor-toggle` remains accessible only if outside that disabled subtree. If that conflicts with the current button placement inside `.terminal-frame`, use JS focus/close behavior instead and avoid disabling the toggle.
- Prefer adding a `Close` button inside the editor panel title so mobile users do not have to find the dimmed top-right `Edit` button behind the drawer.

**Verify**: At 390px viewport width, open and close the editor with pointer and keyboard. Confirm there is no hidden horizontal page scroll and the editor controls remain reachable.

## Test plan

- Manual browser test: Tab through the page while the editor is closed; focus must not enter hidden panel controls.
- Manual browser test: open editor, tab through controls, close with Escape, confirm focus returns to the toggle.
- Manual browser test: mobile-width open/close works without horizontal scrolling.
- Manual browser test: screen-reader attributes reflect state: `aria-expanded=true/false`, `aria-hidden=false/true`.

## Done criteria

- [ ] `python3 -m html.parser docs/crt_terminal_text.html` exits 0.
- [ ] `uv run pytest` exits 0.
- [ ] `uv run ruff check .` exits 0.
- [ ] Closed editor controls are not keyboard-focusable.
- [ ] Escape closes the editor and returns focus.
- [ ] Mobile drawer has an obvious close path.
- [ ] No files outside the in-scope list are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- The editor panel has already been replaced by a different component model.
- The chosen focus approach requires a third-party library.
- Disabling the board behind the editor blocks the only available close control. Add an internal close button instead of trapping the user.

## Maintenance notes

Reviewers should test keyboard behavior, not just screenshots. A drawer that looks closed but remains in the tab order is broken for editing workflows.
