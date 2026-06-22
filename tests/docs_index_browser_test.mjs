import { existsSync, readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";

const repoRoot = resolve(import.meta.dirname, "..");
const pageUrl = `file://${resolve(repoRoot, "docs/index.html")}`;
const userDataDir = await mkdtemp(join(tmpdir(), "orc-index-chrome-"));
const chromePath = findChrome();

let chrome;
let client;

class CdpClient {
  constructor(webSocketUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(webSocketUrl);
    this.opened = new Promise((resolveOpened, rejectOpened) => {
      this.socket.addEventListener("open", resolveOpened, { once: true });
      this.socket.addEventListener("error", rejectOpened, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) {
        return;
      }
      const waiter = this.pending.get(message.id);
      if (!waiter) {
        return;
      }
      this.pending.delete(message.id);
      if (message.error) {
        waiter.reject(new Error(`${message.error.message}: ${message.error.data || ""}`));
      } else {
        waiter.resolve(message.result);
      }
    });
  }

  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const result = new Promise((resolveResult, rejectResult) => {
      this.pending.set(id, { resolve: resolveResult, reject: rejectResult });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  close() {
    this.socket.close();
  }
}

function findExecutable(command) {
  const result = spawnSync("sh", ["-lc", `command -v ${command}`], {
    encoding: "utf8",
  });
  return result.status === 0 ? result.stdout.trim() : null;
}

function findChrome() {
  const candidates = [
    process.env.ORC_CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    findExecutable("google-chrome"),
    findExecutable("google-chrome-stable"),
    findExecutable("chromium"),
    findExecutable("chromium-browser"),
  ].filter(Boolean);

  const chrome = candidates.find((candidate) => existsSync(candidate));
  if (!chrome) {
    throw new Error(
      "Chrome/Chromium is required for docs/index.html browser tests. " +
      "Set ORC_CHROME_PATH to the browser executable."
    );
  }
  return chrome;
}

function assert(condition, message, details = undefined) {
  if (!condition) {
    const error = new Error(message);
    error.details = details;
    throw error;
  }
}

async function sleep(ms) {
  await new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

async function waitForFile(path, timeoutMs = 8000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      return readFileSync(path, "utf8");
    } catch {
      await sleep(50);
    }
  }
  throw new Error(`Timed out waiting for ${path}`);
}

async function waitForPageTarget(port, timeoutMs = 8000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`);
      const targets = await response.json();
      const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
      if (page) {
        return page.webSocketDebuggerUrl;
      }
    } catch {
      // Chrome may not have opened its DevTools endpoint yet.
    }
    await sleep(50);
  }
  throw new Error("Timed out waiting for Chrome page target");
}

async function evaluate(expression) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return result.result.value;
}

async function setViewport(width, height) {
  await client.send("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 700,
  });
  await sleep(50);
}

async function waitForReady() {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 8000) {
    const ready = await evaluate(`
      Boolean(document.getElementById("terminalInput")) && document.readyState === "complete"
    `);
    if (ready) {
      return;
    }
    await sleep(50);
  }
  throw new Error("Timed out waiting for terminal page readiness");
}

async function runInPage(script) {
  return evaluate(`(async () => { ${script} })()`);
}

async function terminateChrome() {
  if (!chrome || chrome.exitCode !== null) {
    return;
  }

  const exited = new Promise((resolveExited) => {
    chrome.once("exit", resolveExited);
  });
  chrome.kill("SIGTERM");
  await Promise.race([exited, sleep(2000)]);
  if (chrome.exitCode === null) {
    chrome.kill("SIGKILL");
    await Promise.race([exited, sleep(2000)]);
  }
}

async function main() {
  chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    `--user-data-dir=${userDataDir}`,
    pageUrl,
  ], { stdio: "ignore" });

  chrome.on("exit", (code, signal) => {
    if (code !== null && code !== 0 && !client) {
      throw new Error(`Chrome exited before tests started: ${code} ${signal || ""}`);
    }
  });

  const portFile = await waitForFile(join(userDataDir, "DevToolsActivePort"));
  const [port] = portFile.trim().split(/\n/);
  client = new CdpClient(await waitForPageTarget(port));
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await setViewport(1440, 900);
  await waitForReady();

  const results = await runInPage(`
    const sleep = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
    const settle = async () => {
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await sleep(20);
    };
    const lines = () => Array.from(document.querySelectorAll("#terminalOutput .terminal-output-line"))
      .map((line) => line.textContent);
    const view = () => document.querySelector(".screen").dataset.view;
    const activeLink = () => document.querySelector(".footer-links a[data-active='true']")
      ?.dataset.viewLink || null;
    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const submit = async (command) => {
      const input = document.getElementById("terminalInput");
      input.value = command;
      document.getElementById("terminalForm")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await settle();
      return {
        command,
        view: view(),
        hash: window.location.hash,
        output: lines(),
        status: document.querySelector(".terminal-status").textContent,
        profile: document.documentElement.dataset.displayProfile,
        raster: document.documentElement.dataset.rasterMode,
        character: document.documentElement.dataset.characterLevel,
        textLevel: document.documentElement.dataset.textWarpLevel,
        frameLevel: document.documentElement.dataset.frameWarpLevel,
        activeLink: activeLink(),
        phosphor: cssVar("--phosphor"),
        scanlineOpacity: cssVar("--scanline-opacity"),
        rasterOpacity: cssVar("--raster-opacity"),
        gridOpacity: cssVar("--terminal-grid-opacity"),
      };
    };

    await settle();
    const initial = {
      view: view(),
      profile: document.documentElement.dataset.displayProfile,
      raster: document.documentElement.dataset.rasterMode,
      character: document.documentElement.dataset.characterLevel,
      status: document.querySelector(".terminal-status").textContent,
      activeLink: activeLink(),
      focusedInput: document.activeElement?.id === "terminalInput",
      decisionsHasContent: document.getElementById("decisionsText").innerText
        .includes("Live Simulator Validation"),
      sourceHasDocument: document.getElementById("sourceText").innerText.includes("<!doctype html"),
      reportCaveat: document.querySelector(".report-printout").innerText
        .includes("live AMS2/ACC validation\\n  is not proven by this repo."),
      traceCaveat: document.querySelector("[data-module='trace']").innerText.includes("not live telemetry"),
      inputAutocomplete: document.getElementById("terminalInput").getAttribute("autocomplete"),
      overlayPointerEvents: {
        grid: getComputedStyle(document.querySelector(".terminal-grid")).pointerEvents,
        ghost: getComputedStyle(document.getElementById("phosphorGhost")).pointerEvents,
      },
      screenTransform: getComputedStyle(document.querySelector(".screen")).transform,
      screenFilter: getComputedStyle(document.querySelector(".screen")).filter,
    };

    const viewCommands = [];
    for (const command of ["laps", "trace", "map", "notes", "help", "decisions", "source", "report"]) {
      viewCommands.push(await submit(command));
    }

    await submit("help");
    const profileCommands = [];
    for (const profile of ["amber", "green", "vga", "lcd"]) {
      profileCommands.push(await submit("profile " + profile));
    }
    const invalidProfile = await submit("profile blue");
    const profileAfterInvalid = document.documentElement.dataset.displayProfile;

    await submit("report");
    const rasterCommands = [];
    for (const raster of ["grid", "scanline", "pixel", "clean"]) {
      rasterCommands.push(await submit("raster " + raster));
    }
    const invalidRaster = await submit("raster vector");
    const rasterAfterInvalid = document.documentElement.dataset.rasterMode;

    const characterFive = await submit("character 5");
    const characterDown = await submit("character down");
    const crtCharacter = await submit("crt character 2");
    const invalidCharacter = await submit("character 9");
    const characterAfterInvalid = document.documentElement.dataset.characterLevel;

    const adjustText = await submit("adjust text 5");
    const adjustBloomUp = await submit("adjust bloom up");
    const adjustJitterDown = await submit("adjust jitter down");
    const adjustArtifactZero = await submit("adjust artifact 0");
    const adjustFrameReset = await submit("adjust frame reset");
    const adjustReset = await submit("adjust reset");
    const invalidAdjust = await submit("adjust color 5");
    const statusCommand = await submit("status");
    const systemCommand = await submit("system");

    window.__orcOpened = [];
    window.open = (url, target, features) => {
      window.__orcOpened.push({ url, target, features });
      return null;
    };
    const github = await submit("github");
    const site = await submit("site");
    const opened = window.__orcOpened;

    const unknown = await submit("bogus command");
    const beforeClearCount = lines().length;
    const clearCommand = await submit("clear");
    const afterClearOutput = lines();
    const clsCommand = await submit("cls");

    await submit("report");
    await submit("help");
    const input = document.getElementById("terminalInput");
    input.value = "";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true }));
    const historyUp = input.value;
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    const historyDown = input.value;

    input.blur();
    document.querySelector(".report").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    await settle();
    const clickToFocus = document.activeElement?.id === "terminalInput";

    const link = document.querySelector("[data-view-link='map']");
    link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    await settle();
    const navClick = { view: view(), hash: window.location.hash, activeLink: activeLink(), output: lines() };

    await submit("report");
    const ghost = document.getElementById("phosphorGhost");
    await submit("laps");
    const ghostAfterViewChange = {
      active: ghost.dataset.active,
      ariaHidden: ghost.getAttribute("aria-hidden"),
      textLength: ghost.textContent.length,
    };

    return {
      initial,
      viewCommands,
      profileCommands,
      invalidProfile,
      profileAfterInvalid,
      rasterCommands,
      invalidRaster,
      rasterAfterInvalid,
      characterFive,
      characterDown,
      crtCharacter,
      invalidCharacter,
      characterAfterInvalid,
      adjustText,
      adjustBloomUp,
      adjustJitterDown,
      adjustArtifactZero,
      adjustFrameReset,
      adjustReset,
      invalidAdjust,
      statusCommand,
      systemCommand,
      github,
      site,
      opened,
      unknown,
      beforeClearCount,
      clearCommand,
      afterClearOutput,
      clsCommand,
      historyUp,
      historyDown,
      clickToFocus,
      navClick,
      ghostAfterViewChange,
    };
  `);

  assert(results.initial.view === "report", "initial view should be report", results.initial);
  assert(results.initial.profile === "AMBER", "initial profile should be AMBER", results.initial);
  assert(results.initial.raster === "GRID", "initial raster should be GRID", results.initial);
  assert(results.initial.character === "3", "initial character level should be 3", results.initial);
  assert(results.initial.focusedInput, "terminal input should be focused on load", results.initial);
  assert(results.initial.decisionsHasContent, "decisions render should include decision content", results.initial);
  assert(results.initial.sourceHasDocument, "source render should include page source", results.initial);
  assert(results.initial.reportCaveat, "report view should preserve live-validation caveat", results.initial);
  assert(results.initial.traceCaveat, "trace view should preserve not-live-telemetry caveat", results.initial);
  assert(results.initial.inputAutocomplete === "off", "terminal input autocomplete should be off", results.initial);
  assert(results.initial.overlayPointerEvents.grid === "none", "raster overlay must not catch input", results.initial);
  assert(results.initial.overlayPointerEvents.ghost === "none", "ghost overlay must not catch input", results.initial);
  assert(results.initial.screenTransform === "none", "interactive screen content plane must stay flat", results.initial);
  assert(results.initial.screenFilter === "none", "interactive screen content plane must stay unfiltered", results.initial);

  for (const commandResult of results.viewCommands) {
    const expectedView = commandResult.command;
    assert(commandResult.view === expectedView, `${expectedView} command should switch view`, commandResult);
    assert(commandResult.hash === `#${expectedView}`, `${expectedView} command should update hash`, commandResult);
    assert(commandResult.activeLink === expectedView, `${expectedView} command should activate footer link`, commandResult);
  }

  for (const commandResult of results.profileCommands) {
    const expectedProfile = commandResult.command.split(" ")[1].toUpperCase();
    assert(commandResult.profile === expectedProfile, `${commandResult.command} should set profile`, commandResult);
    assert(commandResult.view === "help", `${commandResult.command} should preserve current view`, commandResult);
    assert(commandResult.output.some((line) => line.includes(`PROFILE ${expectedProfile} READY`)), `${commandResult.command} should print ready`, commandResult);
  }
  assert(new Set(results.profileCommands.map((item) => item.phosphor)).size >= 3, "profiles should change computed phosphor color", results.profileCommands);
  assert(results.invalidProfile.output.some((line) => line.includes("PROFILE ERROR: BLUE")), "invalid profile should print an error", results.invalidProfile);
  assert(results.profileAfterInvalid === "LCD", "invalid profile must leave current profile unchanged", results.invalidProfile);

  for (const commandResult of results.rasterCommands) {
    const expectedRaster = commandResult.command.split(" ")[1].toUpperCase();
    assert(commandResult.raster === expectedRaster, `${commandResult.command} should set raster`, commandResult);
    assert(commandResult.view === "report", `${commandResult.command} should preserve current view`, commandResult);
    assert(commandResult.output.some((line) => line.includes(`RASTER ${expectedRaster} READY`)), `${commandResult.command} should print ready`, commandResult);
  }
  const clean = results.rasterCommands.find((item) => item.raster === "CLEAN");
  const scanline = results.rasterCommands.find((item) => item.raster === "SCANLINE");
  const pixel = results.rasterCommands.find((item) => item.raster === "PIXEL");
  assert(clean.scanlineOpacity === "0", "RASTER CLEAN should zero scanlines", clean);
  assert(clean.rasterOpacity === "0", "RASTER CLEAN should zero raster opacity", clean);
  assert(scanline.scanlineOpacity !== "0", "RASTER SCANLINE should apply scanline opacity", scanline);
  assert(pixel.rasterOpacity !== "0", "RASTER PIXEL should apply raster opacity", pixel);
  assert(results.invalidRaster.output.some((line) => line.includes("RASTER ERROR: VECTOR")), "invalid raster should print an error", results.invalidRaster);
  assert(results.rasterAfterInvalid === "CLEAN", "invalid raster must leave current raster unchanged", results.invalidRaster);

  assert(results.characterFive.character === "5", "CHARACTER 5 should set level 5", results.characterFive);
  assert(results.characterDown.character === "4", "CHARACTER DOWN should decrement", results.characterDown);
  assert(results.crtCharacter.character === "2", "CRT CHARACTER 2 should set level 2", results.crtCharacter);
  assert(results.invalidCharacter.output.some((line) => line.includes("USE CHARACTER 0-5")), "invalid character should explain accepted range", results.invalidCharacter);
  assert(results.characterAfterInvalid === "2", "invalid character must leave level unchanged", results.invalidCharacter);

  assert(results.adjustText.textLevel === "5", "ADJUST TEXT 5 should set text adjustment", results.adjustText);
  assert(results.adjustBloomUp.output.some((line) => line.includes("BLOOM 03 WORN READY")), "ADJUST BLOOM UP should print active bloom level", results.adjustBloomUp);
  assert(results.adjustJitterDown.output.some((line) => line.includes("JITTER 01 STABLE READY")), "ADJUST JITTER DOWN should decrement jitter", results.adjustJitterDown);
  assert(results.adjustArtifactZero.output.some((line) => line.includes("ARTIFACT 00 SERVICE READY")), "ADJUST ARTIFACT 0 should set artifact", results.adjustArtifactZero);
  assert(results.adjustFrameReset.output.some((line) => line.includes("FRAME FOLLOWS")), "ADJUST FRAME RESET should reset frame override", results.adjustFrameReset);
  assert(results.adjustReset.output.some((line) => line.includes("ALL ADJUSTMENTS FOLLOW")), "ADJUST RESET should reset all overrides", results.adjustReset);
  assert(results.invalidAdjust.output.some((line) => line.includes("ADJUST ERROR")), "invalid adjust should print an error", results.invalidAdjust);

  assert(results.statusCommand.output.some((line) => line.includes("PROFILE LCD / RASTER CLEAN")), "STATUS should report visual state", results.statusCommand);
  assert(results.systemCommand.output.some((line) => line.includes("OPENRACECOACH.COM")), "SYSTEM should include repo/site status", results.systemCommand);

  assert(results.opened.length === 2, "GITHUB and SITE should call window.open twice", results.opened);
  assert(results.opened[0].url === "https://github.com/jaredkirby/open-race-coach", "GITHUB should open public repo", results.opened);
  assert(results.opened[1].url === "https://openracecoach.com", "SITE should open public home", results.opened);
  assert(results.opened.every((entry) => entry.target === "_blank"), "external links should open new tabs", results.opened);
  assert(results.opened.every((entry) => entry.features === "noopener,noreferrer"), "external links should use noopener,noreferrer", results.opened);
  assert(results.github.output.some((line) => line.includes("OPENING GITHUB")), "github command should print response", results.github);
  assert(results.site.output.some((line) => line.includes("OPENING OPENRACECOACH.COM")), "site command should print response", results.site);

  assert(results.unknown.output.some((line) => line.includes("UNKNOWN COMMAND: BOGUS COMMAND")), "unknown commands should be reported", results.unknown);
  assert(results.beforeClearCount <= 3, "terminal output should retain no more than three lines", results.beforeClearCount);
  assert(results.afterClearOutput.length === 1 && results.afterClearOutput[0] === "OPEN RACE COACH TERMINAL READY", "CLEAR should reset terminal output", results.afterClearOutput);
  assert(results.clsCommand.output.length === 1 && results.clsCommand.output[0] === "OPEN RACE COACH TERMINAL READY", "CLS should reset terminal output", results.clsCommand);
  assert(results.historyUp === "help", "ArrowUp should recall previous command", { historyUp: results.historyUp });
  assert(results.historyDown === "", "ArrowDown should move past latest command to blank input", { historyDown: results.historyDown });
  assert(results.clickToFocus, "clicking non-interactive terminal area should focus command line");
  assert(results.navClick.view === "map" && results.navClick.hash === "#map" && results.navClick.activeLink === "map", "footer link should navigate through terminal command path", results.navClick);
  assert(results.ghostAfterViewChange.active === "true", "view changes should activate phosphor ghost", results.ghostAfterViewChange);
  assert(results.ghostAfterViewChange.ariaHidden === "true", "phosphor ghost must remain aria-hidden", results.ghostAfterViewChange);
  assert(results.ghostAfterViewChange.textLength > 0, "phosphor ghost should clone terminal text briefly", results.ghostAfterViewChange);

  const viewportResults = [];
  for (const [width, height] of [[1440, 900], [1728, 1117], [390, 844]]) {
    await setViewport(width, height);
    const layout = await runInPage(`
      const input = document.getElementById("terminalInput");
      input.value = "report";
      document.getElementById("terminalForm")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      document.getElementById("terminalScrollport").scrollTop = 0;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const screen = document.querySelector(".crt-screen").getBoundingClientRect();
      const scrollport = document.getElementById("terminalScrollport").getBoundingClientRect();
      const inputRect = document.getElementById("terminalInput").getBoundingClientRect();
      const report = document.querySelector(".report-printout").getBoundingClientRect();
      const status = document.querySelector(".terminal-status").getBoundingClientRect();
      return {
        viewport: [window.innerWidth, window.innerHeight],
        bodyOverflowX: document.documentElement.scrollWidth - window.innerWidth,
        screenWidth: screen.width,
        screenHeight: screen.height,
        inputWidth: inputRect.width,
        inputInsideScrollport: inputRect.left >= scrollport.left && inputRect.right <= scrollport.right + 1,
        reportVisible: report.bottom > scrollport.top && report.top < scrollport.bottom,
        statusInsideScrollport: status.left >= scrollport.left && status.right <= scrollport.right + 1,
        caveatVisible: document.querySelector(".report-printout").innerText.includes("is not proven by this repo."),
      };
    `);
    const screenshot = await client.send("Page.captureScreenshot", { format: "png" });
    layout.screenshotBytes = Buffer.from(screenshot.data, "base64").length;
    viewportResults.push(layout);
  }

  for (const layout of viewportResults) {
    assert(layout.bodyOverflowX <= 1, "viewport should not create horizontal page overflow", layout);
    assert(layout.screenWidth > 250 && layout.screenHeight > 500, "CRT screen should remain visible", layout);
    assert(layout.inputWidth > 20, "command input should remain usable", layout);
    assert(layout.inputInsideScrollport, "command input should stay inside the terminal display", layout);
    assert(layout.reportVisible, "report content should be visible in terminal viewport", layout);
    assert(layout.statusInsideScrollport, "status line should stay inside terminal display", layout);
    assert(layout.caveatVisible, "validation caveat should remain present", layout);
    assert(layout.screenshotBytes > 10000, "viewport screenshot should be non-empty", layout);
  }

  await client.send("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: "reduce" }],
  });
  const reducedMotion = await runInPage(`
    const ghost = document.getElementById("phosphorGhost");
    const input = document.getElementById("terminalInput");
    input.value = "report";
    document.getElementById("terminalForm")
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => requestAnimationFrame(resolve));
    input.value = "laps";
    document.getElementById("terminalForm")
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 150));
    return {
      reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
      ghostActive: ghost.dataset.active,
      ghostTextLength: ghost.textContent.length,
    };
  `);
  assert(reducedMotion.reduced, "reduced-motion media query should be emulated", reducedMotion);
  assert(reducedMotion.ghostActive === "false", "reduced-motion ghost should decay quickly", reducedMotion);
  assert(reducedMotion.ghostTextLength === 0, "reduced-motion ghost text should be cleared quickly", reducedMotion);

  console.log(JSON.stringify({
    ok: true,
    assertions: 94,
    viewports: viewportResults.map((layout) => layout.viewport),
  }, null, 2));
}

try {
  await main();
} finally {
  if (client) {
    client.close();
  }
  if (chrome) {
    await terminateChrome();
  }
  await rm(userDataDir, { recursive: true, force: true });
}
