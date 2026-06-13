# CRT Dashboard Rewrite Spec

## Purpose

Rewrite `docs/oldschool_driver_debrief.html` into a first-class CRT terminal dashboard for Open Race Coach. This is not a theme polish task. The current implementation is visually wrong because it applies CRT effects globally over a paperwork-style dashboard. The rewrite must rebuild the dashboard composition around a bounded CRT screen surface and use the referenced techniques only where they serve terminal readability.

Confidence: high. The failure has been reproduced in Chrome at desktop and mobile sizes.

## References

- Alec Lownes, "Using CSS to create a CRT": https://aleclownes.com/2017/02/01/crt-display.html
- xybre Obsidian adaptation, "CRT Monitor Styled Codeblocks using CSS": https://publish.obsidian.md/xybre/permalink/6b8a4441-c676-40bb-a422-74179e6759f6
- Visual target already in repo: `docs/reference/ChatGPT Image Jun 13, 2026, 09_34_08 AM.png`
- Current dashboard implementation: `docs/oldschool_driver_debrief.html`

## Diagnosis To Preserve

The current dashboard fails for structural reasons:

- It puts animated color separation on `body`, so every label, table cell, button, and paragraph inherits chromatic blur.
- It puts scanline and flicker overlays on the full viewport, so the effect flattens UI hierarchy instead of reading as a bounded screen.
- It changes `.report-lines` to `white-space: pre`, creating horizontal scrolling inside the report preview. Chrome measured roughly `1790px` content inside a roughly `432px` desktop box and a roughly `302px` mobile box.
- It keeps the old "timing stand paperwork" layout and tries to skin it as a CRT. That composition cannot reach the target image. The rewrite must change the information architecture.

Do not solve this by tweaking colors or opacity alone.

## Product Intent

The page is a local, static, driver-facing dashboard for one Open Race Coach Analysis Run. It should feel like a pit-wall terminal or lap-time improvement console, not like paper artifacts arranged on a desk.

The dashboard must answer, at a glance:

1. What should I do next lap?
2. How much time is at stake?
3. Where on the lap is the loss?
4. What evidence supports the instruction?
5. What artifacts were loaded and whether the result is reportable?

The product rule from the repo still applies: Open Race Coach gives at most one data-supported Coaching Instruction per Analysis Run.

## Scope

Rewrite `docs/oldschool_driver_debrief.html` as a self-contained static HTML dashboard.

Preserve:

- The artifact loader.
- The reset behavior.
- The existing sample data semantics.
- The existing IDs or update the JavaScript consistently if markup changes.
- Driver-facing wording based on Analysis Run artifacts.
- Accessibility basics: semantic regions, buttons, labels, visible focus, no keyboard traps.

Change freely:

- Layout.
- Class names.
- CSS structure.
- Theme model.
- Visual hierarchy.
- Report preview presentation.
- The current paper/receipt/pit-board metaphor.

Do not touch Python analysis code for this rewrite unless the HTML currently depends on impossible data shapes. If data shape changes are needed, stop and document the exact blocker.

## Non-Goals

- Do not add live telemetry.
- Do not invent official corner names.
- Do not imply theoretical optimal pace.
- Do not add charts that require external libraries.
- Do not add a build system.
- Do not fetch remote assets.
- Do not make a marketing landing page.

## Design Direction

Build the dashboard as one bounded CRT screen, with the content inside it arranged like an instrument terminal.

Use a green phosphor base with amber/red warning accents. The target should resemble the reference image more than the current amber paperwork version:

- Dominant phosphor: green.
- Alert/accent: amber.
- Critical/loss: red-orange.
- Background: near black, not brown paper.
- Borders: thin terminal grid lines, not card shadows.
- Typography: monospace-first. Use large blocky numerals for lap time, loss, deficit, and selected corner.

The first viewport should feel dense, operational, and immediately useful. No hero section. No explanatory feature copy.

## Information Architecture

Desktop layout should be a full-screen dashboard with these zones:

1. Header/status strip
   - Product name: `OPEN RACE COACH`
   - Session metadata: session type, track, car, date, reference mode, validity/reportability state
   - Artifact controls: load, reset, loaded status

2. Primary instruction block
   - Largest text on the page after the product name.
   - Shows the one Coaching Instruction.
   - Shows selected Corner Segment ID and short evidence summary.
   - Must be readable in under two seconds.

3. Time-loss block
   - Median loss, deficit, reference metric, comparison metric.
   - Use large numeric treatment.
   - Amber/red for loss, green for clean/reference values.

4. Lap-distance or track-loss visualization
   - Do not pretend to know the real track map unless the data provides it.
   - Prefer a lap-distance rail/tape with Corner Segments and the selected segment highlighted.
   - If position geometry is available in current JS data, a simplified track path is allowed. If not, use the distance rail.

5. Evidence table
   - Comparison laps, valid status, selected delta, repeatability, checked areas.
   - Compact and scannable.

6. Report/artifact panel
   - Shows canonical input names and rendered Coach Report preview.
   - Report text must wrap by default. No horizontal scrolling for normal prose.
   - It is acceptable to provide a `nowrap` toggle only if the default remains wrapped.

Mobile layout should stack zones in the same priority order:

1. Header/status.
2. Primary instruction.
3. Time-loss metrics.
4. Lap-distance visualization.
5. Evidence.
6. Report/artifacts.

## CRT Technique Requirements

Use Alec Lownes's model correctly:

- `.crt-screen::before` creates the screen-door or scanline overlay.
- `.crt-screen::after` creates the flicker/vignette overlay.
- Text-shadow based color separation is applied selectively to high-impact terminal glyphs, not globally.

Use the Obsidian adaptation correctly:

- It is useful as evidence for amber/monochrome code-block styling.
- It is not a license to apply code-block behavior to the whole dashboard.
- Do not combine every snippet everywhere.

Required CSS structure:

- A bounded `.crt-shell` or `.crt-screen` element owns the CRT overlays.
- `body` must not have `animation: crtTextShadow`.
- `body` must not have global chromatic `text-shadow`.
- The screen overlays must be `pointer-events: none`.
- Overlay z-index must not hide controls or make text selection/clicking unreliable.
- Respect `prefers-reduced-motion: reduce` by disabling flicker and text-shadow animation.

Recommended CSS layers:

```css
.crt-screen {
  position: relative;
  isolation: isolate;
}

.crt-screen::before {
  /* screen-door and scanlines */
}

.crt-screen::after {
  /* flicker wash and vignette */
}

.crt-glow-text,
.metric-major,
.terminal-title {
  /* selective color-separation or glow */
}
```

Do not copy the current failure pattern:

```css
:root[data-theme="crt"] body {
  animation: crtTextShadow 1.6s infinite;
  text-shadow: ...;
}
```

## Theme Policy

Preferred: make the rewritten file a CRT dashboard by default and remove the light/dark/system theme selector from the primary UI.

Acceptable fallback: keep theme selection only if:

- CRT is the default.
- Other themes do not constrain the CRT layout.
- The CRT layout is not implemented as a thin override on top of the old paper layout.

## Layout Rules

- Use stable grid dimensions and responsive constraints.
- Avoid cards inside cards.
- Avoid oversized decorative panels that waste first-viewport space.
- Use thin terminal panels with clear labels, not paper shadows.
- No text may overlap at desktop or mobile widths.
- No dashboard-level horizontal scroll at `390px`, `768px`, `1440px`, or `1728px` widths.
- The report preview must not force horizontal scroll for normal Markdown prose.

## Data And Rendering Rules

The existing JS must still render sample data and loaded artifacts. Preserve these behaviors:

- `Load Artifacts` reads multiple local files.
- `Reset` restores sample data.
- Artifact status updates after load/reset.
- Error box reports parse/contract failures.
- The dashboard can render without network access.

Rendering must remain honest:

- If `analysis_status` is not reportable, the primary instruction block must say there is no data-supported Coaching Instruction.
- If lap distance in meters is unavailable, do not show meter-specific coaching language.
- Do not invent sector timing or official corner labels.
- Corner Segment IDs remain analysis-derived labels like `C2`, not official turn names.

## Concrete Implementation Steps

1. Read `CONTEXT.md`, `docs/SPEC_v0.1.md` sections on Coach Report and Analysis Run artifacts, and the current `docs/oldschool_driver_debrief.html`.
2. Create a fresh structural layout inside `docs/oldschool_driver_debrief.html`. Keep the file self-contained.
3. Replace the current theme CSS with a CRT-first design system.
4. Implement `.crt-screen` overlays following Alec Lownes's separation: screen-door, flicker, color separation.
5. Move color separation off `body` and onto a small set of major terminal glyphs.
6. Rework the markup around the six dashboard zones listed above.
7. Update JavaScript selectors only as needed to match the new markup.
8. Preserve artifact parsing and render functions.
9. Verify desktop and mobile screenshots.
10. Remove throwaway debug files unless they are intentionally saved under `.scratch/`.

## Acceptance Criteria

Functional:

- Opening `docs/oldschool_driver_debrief.html` directly in Chrome works.
- Sample data renders without errors.
- Load/reset controls still work.
- Report preview wraps by default.
- No normal state requires horizontal scrolling of the full page.

Visual:

- The page reads as a CRT terminal dashboard on first glance.
- The CRT effect is bounded to the terminal screen.
- Small labels and dense tables remain legible.
- Primary instruction and time-loss values dominate the hierarchy.
- The result is closer to `docs/reference/ChatGPT Image Jun 13, 2026, 09_34_08 AM.png` than to the current paperwork dashboard.

Technical:

- `body` has no CRT text-shadow animation.
- `.crt-screen::before` and `.crt-screen::after` own the overlays.
- `prefers-reduced-motion: reduce` disables flicker and text-shadow animation.
- The file remains self-contained.
- No external JS, CSS, or web fonts are introduced.

## Required Verification

Run a browser verification pass with Playwright or system Chrome:

1. Desktop screenshot at `1440x900`.
2. Wide desktop screenshot at `1728x1117` or similar.
3. Mobile screenshot at `390x844`.
4. DOM assertions:
   - `document.documentElement.scrollWidth <= document.documentElement.clientWidth` at mobile width.
   - `.report-lines.scrollWidth <= .report-lines.clientWidth + acceptableScrollbarAllowance` for default wrapped mode.
   - `getComputedStyle(document.body).animationName` is not the CRT text-shadow animation.
   - `getComputedStyle(document.body).textShadow` is `none` or a non-chromatic non-animated base shadow.
   - `.crt-screen::before` and `.crt-screen::after` have `content` set and `pointer-events: none`.

Also run:

```bash
git diff --check
```

If Python tests are not relevant to the HTML-only rewrite, say so in the final report. If any JS behavior changes, test the changed behavior manually in the browser and describe exactly what was tested.

## Final Report Expected From Dev Agent

The final response should include:

- What changed.
- Which current failure modes were removed.
- Screenshot paths.
- Verification commands and results.
- Any remaining risks.

Do not claim the dashboard is "CRT" merely because it is green, amber, flickering, or scanlined. The pass condition is that the whole interface has been recomposed as a terminal display while keeping the coaching artifact behavior intact.
