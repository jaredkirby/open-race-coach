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
    const pixelSignature = () => {
      const cells = Array.from(document.querySelectorAll("#pixelRender .pixel-cell"));
      const columns = getComputedStyle(document.getElementById("pixelRender"))
        .gridTemplateColumns
        .split(" ")
        .filter(Boolean)
        .length;
      const lit = cells
        .map((cell, index) => ({ cell, index }))
        .filter(({ cell }) => cell.dataset.lit !== "0")
        .map(({ index }) => ({
          x: index % columns,
          y: Math.floor(index / columns),
        }));
      return {
        columns,
        rows: columns ? Math.ceil(cells.length / columns) : 0,
        litCount: lit.length,
        minX: Math.min(...lit.map((point) => point.x)),
        maxX: Math.max(...lit.map((point) => point.x)),
        minY: Math.min(...lit.map((point) => point.y)),
        maxY: Math.max(...lit.map((point) => point.y)),
        topRightLit: lit.some((point) => point.x >= 47 && point.y <= 9),
        lowerLeftLit: lit.some((point) => point.x <= 17 && point.y >= 24),
        lowerRightLit: lit.some((point) => point.x >= 48 && point.y >= 27),
      };
    };
    const pixelMarkerStyles = () => {
      const rgbChannels = (value) => (value.match(/\\d+/g) || []).slice(0, 3).map(Number);
      const luminance = (value) => {
        const [red = 0, green = 0, blue = 0] = rgbChannels(value);
        return red * 0.2126 + green * 0.7152 + blue * 0.0722;
      };
      const markers = Array.from(document.querySelectorAll("#pixelRender .pixel-marker"));
      return markers.map((marker) => {
        const style = getComputedStyle(marker);
        const rect = marker.getBoundingClientRect();
        return {
          text: marker.textContent,
          color: style.color,
          backgroundColor: style.backgroundColor,
          contrastDelta: luminance(style.backgroundColor) - luminance(style.color),
          width: rect.width,
          height: rect.height,
        };
      });
    };
    const pixelImageSignature = (name) => {
      const image = terminalPixelImages[name];
      return {
        columns: image.columns,
        height: image.height || image.rows.length,
        path: image.path || [],
        markers: (image.markers || []).map((marker) => [marker.number, marker.x, marker.y, marker.name]),
      };
    };
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
        pixelMeta: document.getElementById("pixelMeta").innerText,
        pixelCells: document.querySelectorAll("#pixelRender .pixel-cell").length,
        pixelMarkers: document.querySelectorAll("#pixelRender .pixel-marker").length,
        pixelMarkerStyles: pixelMarkerStyles(),
        litPixelCells: document.querySelectorAll("#pixelRender .pixel-cell:not([data-lit='0'])").length,
        pixelSignature: pixelSignature(),
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
      pixelMeta: document.getElementById("pixelMeta").innerText,
      pixelCells: document.querySelectorAll("#pixelRender .pixel-cell").length,
      pixelMarkers: document.querySelectorAll("#pixelRender .pixel-marker").length,
      litPixelCells: document.querySelectorAll("#pixelRender .pixel-cell:not([data-lit='0'])").length,
      pixelSignature: pixelSignature(),
      inputAutocomplete: document.getElementById("terminalInput").getAttribute("autocomplete"),
      overlayPointerEvents: {
        grid: getComputedStyle(document.querySelector(".terminal-grid")).pointerEvents,
        ghost: getComputedStyle(document.getElementById("phosphorGhost")).pointerEvents,
      },
      screenTransform: getComputedStyle(document.querySelector(".screen")).transform,
      screenFilter: getComputedStyle(document.querySelector(".screen")).filter,
      imageSignatures: {
        BRANDS: pixelImageSignature("BRANDS"),
        MONZA: pixelImageSignature("MONZA"),
        SPA: pixelImageSignature("SPA"),
      },
    };

    const viewCommands = [];
    for (const command of ["laps", "trace", "map", "notes", "pixels", "help", "decisions", "source", "report"]) {
      viewCommands.push(await submit(command));
    }
    const imageAlias = await submit("image orc");
    const trackBrands = await submit("track brands");
    const trackBrandsLabels = await submit("track brands labels");
    const trackMonza = await submit("track monza");
    const trackMonzaLabels = await submit("track monza labels");
    const trackSpa = await submit("track spa");
    const trackSpaLabels = await submit("track spa labels");

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
      imageAlias,
      trackBrands,
      trackBrandsLabels,
      trackMonza,
      trackMonzaLabels,
      trackSpa,
      trackSpaLabels,
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
  assert(results.initial.raster === "SCANLINE", "initial raster should be SCANLINE", results.initial);
  assert(results.initial.character === "3", "initial character level should be 3", results.initial);
  assert(results.initial.focusedInput, "terminal input should be focused on load", results.initial);
  assert(results.initial.decisionsHasContent, "decisions render should include decision content", results.initial);
  assert(results.initial.sourceHasDocument, "source render should include page source", results.initial);
  assert(results.initial.reportCaveat, "report view should preserve live-validation caveat", results.initial);
  assert(results.initial.traceCaveat, "trace view should preserve not-live-telemetry caveat", results.initial);
  assert(results.initial.pixelCells === 2304, "pixel renderer should create the expected track bitmap cell grid", results.initial);
  assert(results.initial.litPixelCells > 100, "pixel renderer should include lit phosphor cells", results.initial);
  assert(results.initial.pixelMeta.includes("BRANDS HATCH INDY"), "initial pixel image should be Brands Hatch Indy", results.initial);
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
  assert(results.imageAlias.view === "pixels", "IMAGE ORC should switch to the pixel image view", results.imageAlias);
  assert(results.imageAlias.hash === "#pixels", "IMAGE ORC should update hash to pixels", results.imageAlias);
  assert(results.imageAlias.pixelCells === 644, "IMAGE ORC should render the ORC logo cell grid", results.imageAlias);
  assert(results.imageAlias.output.some((line) => line.includes("OPEN RACE COACH LOGO PIXEL IMAGE READY")), "IMAGE ORC should print ready", results.imageAlias);
  assert(results.trackBrands.pixelMeta.includes("BRANDS HATCH INDY"), "TRACK BRANDS should render Brands Hatch", results.trackBrands);
  assert(results.trackBrands.pixelMeta.includes("Brands_Hatch_Indy_Circuit.svg"), "TRACK BRANDS should include source URL", results.trackBrands);
  assert(results.trackBrands.pixelMeta.includes("Hand-authored terminal approximation"), "TRACK BRANDS should include honest provenance detail", results.trackBrands);
  assert(!results.trackBrands.pixelMeta.includes("DISPLAY LABEL CUES"), "TRACK BRANDS should keep clean metadata", results.trackBrands);
  assert(results.trackBrands.pixelMarkers === 0, "TRACK BRANDS should not render markers", results.trackBrands);
  assert(results.initial.imageSignatures.BRANDS.columns === 72, "Brands data should use the improved 72-column candidate", results.initial);
  assert(results.initial.imageSignatures.BRANDS.height === 32, "Brands data should use the improved 32-row candidate", results.initial);
  assert(JSON.stringify(results.initial.imageSignatures.BRANDS.path.slice(0, 6)) === JSON.stringify([[31, 29], [45, 29], [51, 27], [58, 21], [60, 18], [59, 14]]), "Brands path should keep the accepted candidate opening", results.initial);
  assert(JSON.stringify(results.initial.imageSignatures.BRANDS.path.slice(-5)) === JSON.stringify([[11, 20], [12, 23], [14, 26], [18, 28], [31, 29]]), "Brands path should keep the accepted candidate closing", results.initial);
  assert(results.trackBrands.pixelCells === 2304, "TRACK BRANDS should use the improved Brands raster grid size", results.trackBrands);
  assert(results.trackBrandsLabels.pixelMeta.includes("BRANDS HATCH INDY LABELED TEST"), "TRACK BRANDS LABELS should render the labeled test image", results.trackBrandsLabels);
  assert(results.trackBrandsLabels.pixelMeta.includes("DISPLAY LABEL CUES"), "TRACK BRANDS LABELS should include marker legend", results.trackBrandsLabels);
  assert(results.trackBrandsLabels.pixelMeta.includes("named-location display approximations"), "TRACK BRANDS LABELS should state approximation limits", results.trackBrandsLabels);
  assert(results.trackBrandsLabels.pixelMeta.includes("Open Race Coach Corner Segments"), "TRACK BRANDS LABELS should not imply Corner Segment authority", results.trackBrandsLabels);
  assert(results.trackBrandsLabels.pixelMarkers === 6, "TRACK BRANDS LABELS should render six numbered markers", results.trackBrandsLabels);
  for (const label of ["Brabham Straight", "Paddock Hill", "Druids", "Graham Hill", "McLaren", "Clearways/Clark"]) {
    assert(results.trackBrandsLabels.pixelMeta.includes(label), `TRACK BRANDS LABELS should include ${label}`, results.trackBrandsLabels);
  }
  assert(results.trackMonza.pixelMeta.includes("MONZA"), "TRACK MONZA should render Monza", results.trackMonza);
  assert(results.trackMonza.pixelMeta.includes("Monza_track_map.svg"), "TRACK MONZA should include source URL", results.trackMonza);
  assert(!results.trackMonza.pixelMeta.includes("DISPLAY LABEL CUES"), "TRACK MONZA should keep clean metadata", results.trackMonza);
  assert(results.trackMonza.pixelMarkers === 0, "TRACK MONZA should not render markers", results.trackMonza);
  assert(results.initial.imageSignatures.MONZA.columns === 80, "Monza data should use the improved 80-column candidate", results.initial);
  assert(results.initial.imageSignatures.MONZA.height === 32, "Monza data should use the improved 32-row candidate", results.initial);
  assert(JSON.stringify(results.initial.imageSignatures.MONZA.path.slice(0, 6)) === JSON.stringify([[48, 29], [33, 29], [33, 28], [29, 29], [26, 29], [23, 29]]), "Monza path should keep the accepted candidate opening", results.initial);
  assert(JSON.stringify(results.initial.imageSignatures.MONZA.path.slice(-5)) === JSON.stringify([[67, 23], [67, 25], [64, 28], [58, 28], [48, 29]]), "Monza path should keep the accepted candidate closing", results.initial);
  assert(results.trackMonza.pixelCells === 2560, "TRACK MONZA should use the larger Monza raster grid", results.trackMonza);
  assert(results.trackMonza.pixelSignature.columns === 80, "TRACK MONZA should render 80 columns", results.trackMonza);
  assert(results.trackMonza.pixelSignature.rows === 32, "TRACK MONZA should render 32 rows", results.trackMonza);
  assert(results.trackMonza.pixelSignature.maxX - results.trackMonza.pixelSignature.minX >= 50, "TRACK MONZA should span the circuit width", results.trackMonza);
  assert(results.trackMonza.pixelSignature.maxY - results.trackMonza.pixelSignature.minY >= 26, "TRACK MONZA should span the circuit height", results.trackMonza);
  assert(results.trackMonzaLabels.pixelMeta.includes("MONZA LABELED TEST"), "TRACK MONZA LABELS should render the labeled test image", results.trackMonzaLabels);
  assert(results.trackMonzaLabels.pixelMeta.includes("DISPLAY LABEL CUES"), "TRACK MONZA LABELS should include marker legend", results.trackMonzaLabels);
  assert(results.trackMonzaLabels.pixelMeta.includes("official turn numbers"), "TRACK MONZA LABELS should disclaim official turn numbering", results.trackMonzaLabels);
  assert(results.trackMonzaLabels.pixelMarkers === 6, "TRACK MONZA LABELS should render six numbered markers", results.trackMonzaLabels);
  for (const label of ["Start/finish straight", "Rettifilo", "Curva Grande", "Roggia/Lesmo side", "Ascari", "Parabolica/Alboreto"]) {
    assert(results.trackMonzaLabels.pixelMeta.includes(label), `TRACK MONZA LABELS should include ${label}`, results.trackMonzaLabels);
  }
  assert(results.trackSpa.pixelMeta.includes("SPA-FRANCORCHAMPS"), "TRACK SPA should render Spa", results.trackSpa);
  assert(!results.trackSpa.pixelMeta.includes("DISPLAY LABEL CUES"), "TRACK SPA should keep the clean raster metadata", results.trackSpa);
  assert(results.trackSpa.pixelMarkers === 0, "TRACK SPA should not render corner markers", results.trackSpa);
  assert(results.trackSpa.pixelMeta.includes("Spa-Francorchamps_of_Belgium.svg"), "TRACK SPA should include source URL", results.trackSpa);
  assert(results.initial.imageSignatures.SPA.columns === 80, "Spa data should use the improved 80-column candidate", results.initial);
  assert(results.initial.imageSignatures.SPA.height === 32, "Spa data should use the improved 32-row candidate", results.initial);
  assert(JSON.stringify(results.initial.imageSignatures.SPA.path.slice(0, 6)) === JSON.stringify([[25, 22], [17, 27], [20, 21], [27, 13], [36, 8], [52, 2]]), "Spa path should keep the accepted candidate opening", results.initial);
  assert(JSON.stringify(results.initial.imageSignatures.SPA.path.slice(-5)) === JSON.stringify([[39, 18], [35, 20], [29, 21], [28, 20], [25, 22]]), "Spa path should keep the accepted candidate closing", results.initial);
  assert(results.trackSpa.pixelCells === 2560, "TRACK SPA should use the wider Spa raster grid", results.trackSpa);
  assert(results.trackSpa.pixelSignature.columns === 80, "TRACK SPA should render 80 columns", results.trackSpa);
  assert(results.trackSpa.pixelSignature.rows === 32, "TRACK SPA should render 32 rows", results.trackSpa);
  assert(results.trackSpa.pixelSignature.maxX - results.trackSpa.pixelSignature.minX >= 42, "TRACK SPA should span the circuit width", results.trackSpa);
  assert(results.trackSpa.pixelSignature.maxY - results.trackSpa.pixelSignature.minY >= 26, "TRACK SPA should span the circuit height", results.trackSpa);
  assert(results.trackSpa.pixelSignature.topRightLit, "TRACK SPA should keep the upper-right sweep", results.trackSpa);
  assert(results.trackSpa.pixelSignature.lowerLeftLit, "TRACK SPA should keep the lower-left return", results.trackSpa);
  assert(results.trackSpa.pixelSignature.lowerRightLit, "TRACK SPA should keep the lower-right return", results.trackSpa);
  assert(results.trackSpaLabels.view === "pixels", "TRACK SPA LABELS should switch to the pixel image view", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMeta.includes("SPA-FRANCORCHAMPS LABELED TEST"), "TRACK SPA LABELS should render the labeled test image", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMeta.includes("MARKERS   named-location display approximations for this CRT test only"), "TRACK SPA LABELS should state marker approximation limits", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMeta.includes("Open Race Coach Corner Segments"), "TRACK SPA LABELS should not imply Corner Segment authority", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMeta.includes("official turn numbers"), "TRACK SPA LABELS should disclaim official turn numbering", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMeta.includes("Will Pittenger"), "TRACK SPA LABELS should include source attribution detail", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMeta.includes("commons.wikimedia.org/wiki/File:Spa-Francorchamps_of_Belgium.svg"), "TRACK SPA LABELS should include source URL", results.trackSpaLabels);
  for (const label of ["La Source", "Eau Rouge/Raidillon", "Les Combes", "Bruxelles", "Pouhon", "Fagnes", "Stavelot", "Blanchimont", "Bus Stop"]) {
    assert(results.trackSpaLabels.pixelMeta.includes(label), `TRACK SPA LABELS should include ${label}`, results.trackSpaLabels);
  }
  assert(results.trackSpaLabels.pixelMarkers === 9, "TRACK SPA LABELS should render nine numbered markers", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMarkerStyles.length === 9, "TRACK SPA LABELS should expose marker styles", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMarkerStyles.every((marker) => marker.color === "rgb(17, 16, 11)"), "TRACK SPA LABELS should use a readable marker numeral color", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMarkerStyles.every((marker) => marker.contrastDelta > 180), "TRACK SPA LABELS should use a bright readable marker background", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelMarkerStyles.every((marker) => marker.width >= 10 && marker.height >= 8), "TRACK SPA LABELS markers should have readable badge dimensions", results.trackSpaLabels);
  assert(results.trackSpaLabels.pixelCells === 2560, "TRACK SPA LABELS should preserve the Spa raster grid size", results.trackSpaLabels);
  assert(results.trackSpaLabels.output.some((line) => line.includes("SPA-FRANCORCHAMPS LABELED TEST PIXEL IMAGE READY")), "TRACK SPA LABELS should print ready", results.trackSpaLabels);
  for (const labeledResult of [results.trackBrandsLabels, results.trackMonzaLabels, results.trackSpaLabels]) {
    assert(labeledResult.pixelMarkerStyles.every((marker) => marker.color === "rgb(17, 16, 11)"), "labeled track markers should use a readable numeral color", labeledResult);
    assert(labeledResult.pixelMarkerStyles.every((marker) => marker.contrastDelta > 180), "labeled track markers should use a bright readable marker background", labeledResult);
    assert(labeledResult.pixelMarkerStyles.every((marker) => marker.width >= 10 && marker.height >= 8), "labeled track markers should have readable badge dimensions", labeledResult);
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
    assertions: 194,
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
