# Plan 005: Make the CRT terminal visual system durable

> **Executor instructions**: Follow this plan step by step. Run every verification gate and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report; do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git status --short --branch` and `git diff --stat -- docs/index.html plans/README.md plans/005-crt-terminal-visual-system.md`
> If `docs/index.html` has unrelated dirty changes, preserve them or stop before editing. If the landing page no longer contains the command terminal, `docs/DECISIONS.md` rendering surface, or static no-build structure described below, compare this plan against the live file before proceeding. If the plan no longer matches in meaning, stop and report.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: visual-system durability
- **Planned at**: commit `be5dfe4`, 2026-06-20
- **Confidence**: high that the requested behavior belongs in `docs/index.html`; moderate on exact CSS implementation details until browser screenshots prove legibility.

## Why this matters

The landing page is trying to evoke cool-retro-term, but a convincing terminal surface is not achieved by adding more glow or scanlines. The visual system needs named, command-addressable states that a user can inspect and switch while preserving Open Race Coach's core honesty: this is a static, pre-alpha public page for post-session Analysis Run concepts, not live telemetry and not proof of live AMS2/ACC validation. Confidence: high.

The blunt requirement: implement a restrained terminal display system, not a novelty filter. If the text gets harder to read, the pass failed.

## Current state

- `docs/index.html` is a self-contained static landing page that opens directly in a browser.
- The page already has a bounded `.crt-screen`, terminal command input, views for report/laps/trace/map/notes/help/decisions, a `docs/DECISIONS.md` fetch with embedded fallback text, and external links using `_blank` plus `noopener`/`noreferrer`.
- The live worktree at planning time had an existing dirty `docs/index.html` from a prior visual pass. Treat that file as the future implementation surface, but do not assume its current diff is already complete or correct.
- `README.md` states the project is pre-alpha and that AMS2/ACC live simulator validation is not yet proven. That caveat must remain visible in the landing page.
- `CONTEXT.md` and `schema/*.md` reserve deterministic truth for Recorded Session, Analysis Run, Reference Lap, Comparison Lap, Corner Segment, Reportable Delta, Lap Loss Cause, Coach Report, Coach Refinement, and Coaching Instruction artifacts. This plan must not change those meanings.

Repo conventions to preserve:

- Static `docs/index.html` must remain no-build, self-contained, and useful when opened directly.
- Deterministic analysis owns statuses, selected deltas, evidence, reportability, and the at-most-one Coaching Instruction rule.
- Coach Refinement may rewrite prose only; it must not reinterpret telemetry or select new evidence.
- Live Simulator Validation requires Windows plus configured simulators. Public fixtures, static page examples, and unit tests are not proof of live simulator validation.
- Completed Analysis Runs are immutable timestamped canonical outputs. This visual pass must not modify schemas or durable Analysis Run artifacts.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| HTML parse | `python3 -m html.parser docs/index.html` | exit 0 |
| Diff whitespace check | `git diff --check -- docs/index.html plans/README.md plans/005-crt-terminal-visual-system.md` | exit 0 |
| External link check | `rg -n 'target="_blank"' docs/index.html` plus manual inspection | each `_blank` link has `rel` containing `noopener` |
| Command grammar check | browser console or manual command entry | profile/raster/status commands respond visibly |
| Screenshot evidence | browser screenshots at desktop and mobile widths | text is legible, not overlapped, and caveat is visible |

Python tests and ruff are not required for this HTML-only landing-page visual pass unless implementation touches Python, schemas, scripts, or generated artifacts. If those files change, stop; that is outside this plan.

## Scope

**In scope**:

- `docs/index.html`
- `plans/README.md` status row when the plan is completed
- Screenshot evidence under `.scratch/` only if the operator explicitly wants local evidence retained

**Out of scope**:

- `docs/crt_terminal_text.html`
- `docs/oldschool_driver_debrief.html`
- `simcoach/**`
- `scripts/**`
- `schema/**`
- `data/**`
- Package installs, build stack migration, bundlers, web-font fetches, npm tooling, or a dev server
- Changes to deterministic analysis, schemas, Coach Refinement semantics, or durable Analysis Run artifacts

## Non-goals

- Do not copy code, assets, fonts, shaders, palettes, or configuration files directly from cool-retro-term.
- Do not import risky GPL assets or GPL-licensed font files from cool-retro-term into this repo.
- Do not turn the static landing page into a React/Vite or package-managed app.
- Do not add live telemetry, live capture controls, or claims of live AMS2/ACC validation.
- Do not invent new telemetry, Corner Segment labels, Reportable Deltas, Lap Loss Causes, or Coaching Instructions beyond the static example copy already present.

## Implementation requirements

### Requirement 1: Era-specific licensed font strategy

Use an explicit static-page font strategy:

- Keep the default terminal font stack local and system-safe unless a font license is reviewed and compatible.
- If adding a downloadable font, use a permissively licensed font with provenance recorded in comments near the `@font-face` or asset reference.
- Prefer names and CSS variables that describe the era and role, such as `terminalFont`, `terminalFontEra`, or profile-level font sizing, instead of implying exact emulation of a proprietary terminal.
- Do not import or copy cool-retro-term font assets. License uncertainty is a STOP condition.

The implementation should explain its choice in code comments only where necessary. Do not add visible landing-page prose about font licensing.

### Requirement 2: Profile switching commands

Add command-line profile switching with at least these commands:

- `PROFILE AMBER`
- `PROFILE GREEN`
- `PROFILE VGA`
- `PROFILE LCD`

Each command must:

- Switch a named display profile by setting CSS variables and an inspectable state, such as `document.documentElement.dataset.displayProfile`.
- Preserve terminal content and current view.
- Write a terminal response that names the active profile, for example `PROFILE AMBER READY`.
- Avoid one-note palette drift. Profiles should be visually distinct: amber phosphor, green phosphor, VGA-style color, and flatter LCD treatment.
- Keep all Open Race Coach caveats and evidence labels legible.

### Requirement 3: Real raster modes

Add raster mode commands with at least:

- `RASTER GRID`
- `RASTER SCANLINE`
- `RASTER PIXEL`
- `RASTER CLEAN`

Each mode must:

- Change a real rendering layer or CSS state, not just print a message.
- Be inspectable through state, such as `document.documentElement.dataset.rasterMode`.
- Write a terminal response, for example `RASTER SCANLINE READY`.
- Keep overlays `pointer-events: none`.
- Keep text legible. `RASTER CLEAN` must remove decorative raster interference while preserving the terminal layout.

### Requirement 4: Text bloom from actual terminal content

Add text bloom based on duplicated or shadowed terminal glyph content, not a uniform page glow:

- Bloom should come from real visible terminal text, such as a cloned text layer, `text-shadow` scoped to terminal text classes, or pseudo-elements carrying actual text where that is maintainable.
- Do not create a global radial glow that makes empty screen areas glow as if text existed there.
- Scope stronger bloom to high-impact terminal glyphs, command output, active labels, and major numbers. Dense evidence text, decisions text, and caveat text must remain readable.
- Reduced-motion users should not get animated bloom shimmer.

### Requirement 5: Phosphor persistence and burn-in ghosting

Add subtle command/view-change persistence:

- On view changes or visual commands, leave a short-lived ghost of the previous terminal state or command response.
- Keep it subtle: it should read as phosphor persistence, not duplicated content.
- It must decay automatically and must not become part of the accessible name or copied text.
- Respect `prefers-reduced-motion: reduce`; reduce duration sharply or disable animation.
- Do not persist ghost layers across page reloads.

### Requirement 6: Glass reflection and frame shine

Add restrained glass/frame surface treatment:

- Reflections and frame shine belong to `.crt-screen`, frame, or glass layers, not to content text.
- They must not obscure racing telemetry text, the validation caveat, terminal commands, or `docs/DECISIONS.md` rendering.
- Keep overlay opacity low and `pointer-events: none`.
- Test on desktop and mobile. A reflection that looks good on desktop but washes out mobile copy is a failure.

### Requirement 7: Slight curvature illusion

Add a slight curvature illusion without breaking interaction:

- Prefer non-layout-breaking CSS treatments: border radius, vignette shaping, inset shadows, subtle transform on non-interactive overlay layers, or background distortion.
- State clearly in code or plan comments that hit targets remain flat unless pointer correction is implemented.
- Do not transform the actual interactive surface in a way that makes clicking links, text selection, command input, or focus outlines misalign with what the user sees.
- If actual content warping is attempted, implement and verify pointer correction first. Otherwise stop.

### Requirement 8: Command-line visual state responses

Extend command handling and help text so users can discover and confirm visual state:

- `STATUS`, `SYSTEM`, or equivalent must report current profile and raster mode.
- Help text must include profile and raster commands.
- Unknown profile or raster values must produce a clear terminal error and leave the current visual state unchanged.
- Command history navigation must still work after the new commands.
- Existing commands for `REPORT`, `LAPS`, `TRACE`, `MAP`, `NOTES`, `DECISIONS`, `GITHUB`, `SITE`, `HELP`, and `CLEAR` must continue to work.

## Steps

### Step 1: Preserve the current contract before editing

Run:

```bash
git status --short --branch
git diff --stat -- docs/index.html plans/README.md plans/005-crt-terminal-visual-system.md
```

If files outside `docs/index.html`, `plans/README.md`, or this plan are dirty, inspect before editing. If an unrelated dirty file would be overwritten, stop.

### Step 2: Define display profiles and raster modes as data

Create profile and raster definitions near the existing command code. Use data objects or maps instead of scattered conditionals. Each profile should own only visual variables and labels. Each raster mode should own overlay/display variables and labels.

Keep names user-facing and command-friendly:

- `AMBER`
- `GREEN`
- `VGA`
- `LCD`
- `GRID`
- `SCANLINE`
- `PIXEL`
- `CLEAN`

### Step 3: Implement state application functions

Add small functions such as:

- `applyDisplayProfile(profileName)`
- `applyRasterMode(modeName)`
- `getVisualStatus()`

These functions must update CSS variables, data attributes, and terminal response text. They must not change the current view or mutate analysis copy.

### Step 4: Implement visual layers

Add the minimum CSS/DOM needed for:

- content-derived text bloom,
- raster overlays,
- glass/frame shine,
- phosphor persistence ghosting,
- slight curvature illusion.

Keep layers named and separated. Do not mix state, content rendering, and decorative overlays into one opaque block of CSS.

### Step 5: Wire commands and help text

Extend `runCommand` and the help module:

- Parse `PROFILE <name>`.
- Parse `RASTER <mode>`.
- Parse `STATUS` and/or `SYSTEM`.
- Keep command normalization consistent with the existing lowercase/space-collapsing behavior.
- Add examples to the command list.

### Step 6: Re-check honesty and caveats

Before visual verification, confirm the page still says:

- Open Race Coach is pre-alpha or in-progress.
- Live AMS2/ACC validation is not proven.
- The page is static post-session analysis, not live telemetry.
- There is at most one data-supported Coaching Instruction.

If any caveat disappeared or became visually hidden, restore it before continuing.

## Verification gates

Run or perform all of these before marking the plan DONE:

- [ ] `python3 -m html.parser docs/index.html` exits 0.
- [ ] `git diff --check -- docs/index.html plans/README.md plans/005-crt-terminal-visual-system.md` exits 0.
- [ ] Command navigation still works for `REPORT`, `LAPS`, `TRACE`, `MAP`, `NOTES`, `DECISIONS`, `HELP`, `CLEAR`, `GITHUB`, and `SITE`.
- [ ] New commands work: `PROFILE AMBER`, `PROFILE GREEN`, `PROFILE VGA`, `PROFILE LCD`, `RASTER GRID`, `RASTER SCANLINE`, `RASTER PIXEL`, `RASTER CLEAN`, and `STATUS` or `SYSTEM`.
- [ ] Unknown profile/raster commands leave state unchanged and print a clear error.
- [ ] Every `target="_blank"` link has `rel` containing `noopener`; `noreferrer` is preferred.
- [ ] `docs/DECISIONS.md` rendering still works from fetch when available and from embedded fallback when fetch fails.
- [ ] `prefers-reduced-motion: reduce` disables or sharply reduces flicker, raster jitter, bloom animation, and ghost persistence.
- [ ] Desktop layout at roughly `1440x900` has no incoherent overlap and all terminal text is legible.
- [ ] Wide desktop layout at roughly `1728x1117` has no incoherent overlap and the terminal is framed intentionally.
- [ ] Mobile layout at roughly `390x844` has no horizontal page scroll, no clipped command input, and no obscured caveat text.
- [ ] Screenshot evidence exists for desktop and mobile verification, with paths reported in the final implementation note if retained.

Suggested browser console assertions:

```js
document.documentElement.scrollWidth <= document.documentElement.clientWidth
getComputedStyle(document.querySelector(".crt-screen"), "::before").pointerEvents === "none"
getComputedStyle(document.querySelector(".crt-screen"), "::after").pointerEvents === "none"
document.documentElement.dataset.displayProfile
document.documentElement.dataset.rasterMode
```

## Done criteria

- [ ] All implementation requirements are satisfied.
- [ ] All verification gates pass or any unrun gate is explicitly reported with a reason.
- [ ] No files outside the in-scope list are modified.
- [ ] No cool-retro-term code, assets, or GPL font files are copied into the repo.
- [ ] `plans/README.md` status row updated to `DONE` only after live verification passes.

## STOP conditions

Stop and report if:

- Font, asset, shader, palette, or code license provenance is uncertain.
- Any required terminal command breaks existing command navigation or command history.
- Layout overflows, overlaps, clips, or hides meaningful terminal text at mobile or desktop widths.
- The live AMS2/ACC validation caveat is missing, hidden, or visually demoted into unreadability.
- `docs/DECISIONS.md` rendering breaks.
- External links lose `_blank` safety via `noopener`.
- Reduced-motion mode still animates flicker, jitter, bloom shimmer, or ghost persistence aggressively.
- Implementing curvature requires transforming actual hit targets without pointer correction.
- The change requires a build stack, package install, remote asset, or network dependency.
- Any file outside the in-scope list would need editing.

## Maintenance notes

Reviewers should be hostile to theatrical CRT effects that degrade the coaching surface. The visual system is acceptable only if it makes the static landing page feel more like a terminal while preserving the factual contract: post-session artifacts, deterministic analysis evidence, at most one Coaching Instruction, and no claim of proven live simulator validation.
