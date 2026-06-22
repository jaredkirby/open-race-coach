from __future__ import annotations

import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "docs" / "index.html"
DECISIONS_MD = REPO_ROOT / "docs" / "DECISIONS.md"
BROWSER_TEST = Path(__file__).with_name("docs_index_browser_test.mjs")


class LandingPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)

    @property
    def text(self) -> str:
        return "\n".join(self.text_parts)


def page_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def parsed_page() -> LandingPageParser:
    parser = LandingPageParser()
    parser.feed(page_html())
    return parser


def attrs_for(tag_name: str) -> list[dict[str, str | None]]:
    return [attrs for tag, attrs in parsed_page().tags if tag == tag_name]


def test_docs_index_is_valid_self_contained_static_html() -> None:
    parser = parsed_page()
    html = page_html()

    assert parser.getpos()
    assert "<!doctype html>" in html.lower()
    assert not [attrs for attrs in attrs_for("script") if attrs.get("src")]
    assert not [
        attrs
        for attrs in attrs_for("link")
        if attrs.get("rel") in {"stylesheet", "preload", "modulepreload"}
    ]
    assert "@import" not in html
    assert "openracecoach.com" in parser.text


def test_landing_page_preserves_open_race_coach_honesty_contract() -> None:
    text = parsed_page().text

    required_phrases = [
        "OPEN RACE COACH",
        "LOCAL-FIRST POST-SESSION SIM-RACING COACH",
        "Recorded Session -> Analysis Run -> one",
        "data-supported Coaching Instruction.",
        "PRE-ALPHA / live AMS2/ACC validation",
        "is not proven by this repo.",
        "Deterministic analysis owns evidence:",
        "Reference Lap, Comparison Laps,",
        "Corner Segment, Reportable Delta,",
        "and Lap Loss Cause.",
        "Coach Refinement rewrites prose only.",
        "Modules are static post-session",
        "analysis surfaces, not live telemetry.",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_logo_and_view_markup_are_addressable_by_terminal_commands() -> None:
    html = page_html()
    parser = parsed_page()
    anchors = [attrs for tag, attrs in parser.tags if tag == "a"]

    assert "┌─┐┌─┐┌─┐\n└─┘┴└─└─" in html
    for view in [
        "report",
        "laps",
        "trace",
        "map",
        "notes",
        "pixels",
        "help",
        "decisions",
        "source",
    ]:
        assert f'data-view-link="{view}"' in html
        assert f'"{view}"' in html

    assert 'id="terminalForm"' in html
    assert 'id="terminalInput"' in html
    assert 'autocomplete="off"' in html
    assert 'aria-live="polite"' in html
    assert {anchor["href"] for anchor in anchors if anchor.get("data-view-link")} == {
        "#report",
        "#laps",
        "#trace",
        "#map",
        "#notes",
        "#pixels",
        "#help",
        "#decisions",
        "#source",
    }


def test_help_text_documents_the_terminal_feature_surface() -> None:
    text = parsed_page().text

    documented_commands = [
        "REPORT",
        "LAPS",
        "TRACE",
        "MAP",
        "NOTES",
        "TRACKS",
        "TRACK <BRANDS|MONZA|SPA>",
        "TRACK <NAME> LABELS",
        "IMAGE ORC",
        "DECISIONS",
        "SOURCE",
        "CODE",
        "GITHUB/REPO",
        "SITE/OPENRACECOACH.COM",
        "PROFILE AMBER|GREEN|VGA|LCD",
        "RASTER GRID|SCANLINE|PIXEL|CLEAN",
        "CHARACTER 0-5|UP|DOWN",
        "CRT CHARACTER 0-5",
        "ADJUST ARTIFACT 0-5|UP|DOWN",
        "ADJUST BLOOM    0-5|UP|DOWN",
        "ADJUST JITTER   0-5|UP|DOWN",
        "ADJUST TEXT     0-5|UP|DOWN",
        "ADJUST FRAME    0-5|UP|DOWN",
        "ADJUST RESET",
        "STATUS/SYSTEM Show full system / repo status",
        "CLEAR/CLS",
        "LS/PWD/CD/CAT/LESS/MAN/HISTORY",
        "WHOAMI/DATE/UPTIME/ECHO/GREP",
        "RESET/EXIT/LOGOUT/SUDO",
        "LAP/DELTA/COACH/SEGMENT/COMPARE",
    ]

    for command in documented_commands:
        assert command in text


def test_command_grammar_and_visual_states_are_defined_in_page_code() -> None:
    html = page_html()

    for profile in ["AMBER", "GREEN", "VGA", "LCD"]:
        assert re.search(rf"\b{profile}:\s*\{{", html)
    for raster in ["GRID", "SCANLINE", "PIXEL", "CLEAN"]:
        assert re.search(rf"\b{raster}:\s*\{{", html)
    for view_command in [
        "report",
        "laps",
        "trace",
        "map",
        "notes",
        "pixels",
        "help",
        "decisions",
        "source",
        "lap",
        "delta",
        "coach",
        "segment",
        "compare",
    ]:
        assert view_command in html

    required_code_paths = [
        "const terminalStartedAt",
        "let terminalCwd",
        "const pseudoViewTargets",
        "const commandManual",
        "function resolvePseudoViewTarget(target)",
        "function getPseudoTargetText(target)",
        "function printLs()",
        "function runCd(tokens)",
        "function runCatLike(verb, tokens)",
        "function runMan(tokens)",
        "function runHistory()",
        "function runGrep(tokens)",
        "function runMotorsportAlias(command)",
        "const compactHelpText",
        "SHELL: LS/PWD/CD/CAT/LESS/MAN/HISTORY/WHOAMI/DATE/UPTIME/ECHO/GREP",
        "VISUAL/RACE: PROFILE/RASTER/CHARACTER/ADJUST/STATUS/LAP/DELTA/COACH/SEGMENT/COMPARE",
        'logout: "LOGOUT/EXIT',
        "const terminalPixelImages",
        "terminalPixelImages.BRANDS_LABELS",
        "terminalPixelImages.MONZA_LABELS",
        "terminalPixelImages.SPA_LABELS",
        "function getPixelMarkerMap(image)",
        "function renderPixelImage(target, image)",
        "function resolvePixelImageCommand(command, tokens)",
        '"brands labels": "BRANDS_LABELS"',
        '"monza labels": "MONZA_LABELS"',
        '"spa labels": "SPA_LABELS"',
        "Open Race Coach Corner Segments",
        "function applyDisplayProfile(profileName)",
        "rootElement.dataset.displayProfile = profile.label",
        "function applyRasterMode(modeName)",
        "rootElement.dataset.rasterMode = rasterMode.label",
        "function applyCharacterLevel(nextLevel, options = {})",
        "function applyAdjustmentLevel(controlName, nextLevel)",
        "function getVisualStatus()",
        'window.open(url, "_blank", "noopener,noreferrer")',
        'writeTerminal(`UNKNOWN COMMAND:',
        "commandHistory.push(command)",
        'window.addEventListener("hashchange"',
        'document.addEventListener("pointerdown", handleTerminalFocusGesture)',
        'document.addEventListener("keydown", handleFontScaleShortcut)',
    ]

    for code_path in required_code_paths:
        assert code_path in html

    assert "CORNERS" not in html


def test_source_and_decisions_views_have_file_url_fallback_content() -> None:
    html = page_html()
    decisions_fallback = re.search(
        r'<script type="text/plain" id="decisionsSource">(.*?)</script>',
        html,
        re.DOTALL,
    )

    assert 'id="decisionsSource"' in html
    assert "fetch(\"./DECISIONS.md\")" in html
    assert "fetch(\"./index.html\")" in html
    assert "getCurrentDocumentSource()" in html
    assert "DOCS/DECISIONS.MD READY" in html
    assert "DOCS/INDEX.HTML SOURCE READY" in html
    assert "2026-06-13 | Run first rig testing as gated AMS2 Live Simulator Validation" in html
    assert decisions_fallback is not None
    assert decisions_fallback.group(1).strip() == DECISIONS_MD.read_text(encoding="utf-8").strip()


def test_actual_terminal_text_plane_stays_flat_for_clickable_content() -> None:
    html = page_html()

    assert re.search(r"\.screen\s*\{[^}]*\bfilter\s*:", html, re.DOTALL) is None
    assert re.search(r"\.screen\s*\{[^}]*\btransform\s*:", html, re.DOTALL) is None
    assert "function scheduleScreenArchRefresh() {\n      // Rows stay flat;" in html
    assert "[data-arch-char]" in html
    assert "crt-text-warp" not in html
    assert "--line-x" not in html
    assert "--line-y" not in html


def test_external_links_are_new_tab_noopener_links_when_present() -> None:
    anchors = [attrs for tag, attrs in parsed_page().tags if tag == "a"]
    blank_links = [attrs for attrs in anchors if attrs.get("target") == "_blank"]

    for attrs in blank_links:
        rel_tokens = set((attrs.get("rel") or "").split())
        assert {"noopener", "noreferrer"} <= rel_tokens


def test_docs_index_browser_features_work_in_headless_chrome() -> None:
    if os.environ.get("ORC_SKIP_DOCS_INDEX_BROWSER_TEST") == "1":
        pytest.skip("ORC_SKIP_DOCS_INDEX_BROWSER_TEST=1")

    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "Node is required for docs/index.html browser tests. "
            "Set ORC_SKIP_DOCS_INDEX_BROWSER_TEST=1 only for an explicitly static-only run."
        )

    result = subprocess.run(
        [node, str(BROWSER_TEST)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
