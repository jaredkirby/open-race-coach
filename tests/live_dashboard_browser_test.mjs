import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { spawn } from "node:child_process";

const repo = resolve(import.meta.dirname, "..");
const out = resolve(process.argv[2] || join(repo, ".scratch/live-validation", `dashboard-browser-${Date.now()}`));
mkdirSync(dirname(out), { recursive: true });
mkdirSync(out, { recursive: false });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const result = await fn(); if (result) return result;
    await sleep(100);
  }
  throw new Error(`Timeout: ${label}`);
}
class Cdp {
  constructor(url) {
    this.id = 0; this.pending = new Map(); this.socket = new WebSocket(url);
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", event => {
      const value = JSON.parse(event.data), job = this.pending.get(value.id);
      if (!job) return;
      this.pending.delete(value.id);
      value.error ? job.reject(new Error(value.error.message)) : job.resolve(value.result);
    });
  }
  async send(method, params = {}) {
    await this.opened;
    const id = ++this.id;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return Promise.race([result, new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 10000);
      timer.unref();
    })]);
  }
  async evaluate(expression) {
    const value = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.equal(value.exceptionDetails, undefined, JSON.stringify(value.exceptionDetails));
    return value.result.value;
  }
}
const python = process.env.ORC_PYTHON || join(repo, ".venv/Scripts/python.exe");
const chromePath = [process.env.ORC_CHROME_PATH,
  ...[process.env.PROGRAMFILES, process.env["PROGRAMFILES(X86)"], process.env.LOCALAPPDATA]
    .filter(Boolean).map(base => join(base, "Google/Chrome/Application/chrome.exe")),
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].filter(Boolean).find(existsSync);
assert.ok(chromePath, "Chrome is required");
const fixture = spawn(python, ["-B", "tests/dashboard_browser_fixture.py", out],
  { cwd: repo, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let fixtureOutput = "", chromeOutput = "", chrome, cdp;
fixture.stdout.on("data", c => fixtureOutput += c);
fixture.stderr.on("data", c => fixtureOutput += c);
try {
  const connection = await until(() => existsSync(join(out, "fixture.json")) &&
    JSON.parse(readFileSync(join(out, "fixture.json"), "utf8")), "fixture startup");
  const profile = join(out, "chrome-profile"); mkdirSync(profile);
  chrome = spawn(chromePath, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, `http://127.0.0.1:${connection.port}/`],
    { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  chrome.stdout.on("data", c => chromeOutput += c);
  chrome.stderr.on("data", c => chromeOutput += c);
  const debugPort = await until(() => existsSync(join(profile, "DevToolsActivePort")) &&
    readFileSync(join(profile, "DevToolsActivePort"), "utf8").split(/\r?\n/)[0], "Chrome startup");
  const page = await until(async () => {
    const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
    return pages.find(p => p.type === "page");
  }, "page target");
  cdp = new Cdp(page.webSocketDebuggerUrl);
  await cdp.send("Page.enable");
  await until(() => cdp.evaluate('document.querySelector("#code") !== null'), "page load");
  await cdp.evaluate(`document.querySelector('#code').value=${JSON.stringify(connection.token)};
    document.querySelector('#connect-form').requestSubmit()`);
  const state = () => cdp.evaluate('document.querySelector("#connection").textContent');
  await until(async () => await state() === "FRESH", "fresh display");
  assert.equal(await cdp.evaluate('document.querySelector("#speed").textContent'), "144");
  assert.equal(await cdp.evaluate('document.querySelector("#fuel").textContent'), "4.20");
  assert.match(await cdp.evaluate('document.querySelector("#feedback-summary").textContent'), /4 valid \/ 6 lap records/);
  assert.match(await cdp.evaluate('document.querySelector("#feedback-objective").textContent'), /3 valid laps within 0.50 s/);
  assert.equal(await cdp.evaluate('document.querySelectorAll("#feedback-laps tr").length'), 6);
  assert.equal(await cdp.evaluate('document.querySelector("#supervision").textContent'), "Fuel supervision active");
  for (const [width, height, label] of [[1280, 900, "desktop"], [390, 844, "mobile"]]) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    await sleep(150);
    assert.equal(await cdp.evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
    const shot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(join(out, `${label}.png`), Buffer.from(shot.data, "base64"));
  }
  writeFileSync(join(out, "control"), "transition");
  await until(async () => await state() === "WAITING", "waiting across skipped context packet");
  const clearedAt = performance.now();
  assert.equal(await cdp.evaluate('document.querySelector("#fuel").textContent'), "—");
  assert.equal(await cdp.evaluate('document.querySelector("#supervision").textContent'), "Fuel supervision unverified");
  assert.equal(JSON.parse(readFileSync(join(out, "skipped-context.json"), "utf8")).reason,
    "context_changed");
  await until(async () => await state() === "FRESH", "fresh new context on advancing sample");
  const transitionWaitMs = performance.now() - clearedAt;
  assert.match(await cdp.evaluate('document.querySelector("#context").textContent'), /Alternate Formula/);
  writeFileSync(join(out, "control"), "available");
  await until(async () => await state() === "FRESH" &&
    (await cdp.evaluate('document.querySelector("#context").textContent')).includes("Formula Inter"),
    "restore original context");
  await cdp.evaluate(`(() => {
    const nativeFetch = window.fetch, nativeNow = Date.now;
    const probe = window.dashboardClockTest = {
      releases: [],
      jump: () => { Date.now = () => nativeNow() + 30000; },
      restore: () => { Date.now = nativeNow; window.fetch = nativeFetch; },
    };
    window.fetch = async (...args) => {
      const response = await nativeFetch(...args);
      if (probe.releases.length === 2) return response;
      const body = await response.text();
      return new Promise(resolve => probe.releases.push(() =>
        resolve(new Response(body, { status: response.status }))));
    };
  })()`);
  const held = () => cdp.evaluate("window.dashboardClockTest.releases.length");
  await until(async () => await held() === 1, "captured pre-resume response");
  await cdp.evaluate("window.dashboardClockTest.jump()");
  await until(async () => await state() === "DISCONNECTED", "clock discontinuity clears the display");
  await cdp.evaluate("window.dashboardClockTest.releases[0]()");
  await until(async () => await held() === 2, "captured first post-resume response");
  assert.equal(await state(), "DISCONNECTED");
  assert.equal(await cdp.evaluate('document.querySelector("#speed").textContent'), "—");
  assert.equal(await cdp.evaluate('document.querySelector("#feedback-body").hidden'), true);
  const clockRecoveryStart = performance.now();
  await cdp.evaluate("window.dashboardClockTest.releases[1]()");
  await until(async () => await state() === "WAITING", "first new observation remains waiting");
  await until(async () => await state() === "FRESH", "renewed source progress");
  const clockRecoveryMs = performance.now() - clockRecoveryStart;
  await cdp.evaluate("window.dashboardClockTest.restore()");
  await until(async () => await state() === "FRESH", "original clocks restored");
  writeFileSync(join(out, "control"), "stale");
  await until(async () => await state() === "STALE", "stale display");
  assert.equal(await cdp.evaluate('document.querySelector("#speed").textContent'), "—");
  assert.equal(await cdp.evaluate('document.querySelector("#supervision").textContent'), "Fuel supervision unverified");
  assert.match(await cdp.evaluate('document.querySelector("#feedback-objective").textContent'), /Historical target/);
  writeFileSync(join(out, "control"), "available");
  await until(async () => await state() === "FRESH", "fresh recovery");
  for (const [timing, label] of [["simulator", "Simulator-reported timing"],
                                ["mixed", "Mixed timing sources"]]) {
    writeFileSync(join(out, "control"), timing);
    await until(async () => (await cdp.evaluate('document.querySelector("#feedback-scope").textContent')).includes(label), `timing ${timing}`);
    await cdp.evaluate('document.querySelector("details").open = true');
    if (timing === "mixed") {
      assert.match(await cdp.evaluate('document.querySelector("#feedback-summary").textContent'), /Best — · Median — · Full spread —/);
      assert.match(await cdp.evaluate('document.querySelector("#feedback-timing-groups").textContent'), /Captured: 3 valid/);
      assert.match(await cdp.evaluate('document.querySelector("#feedback-objective").textContent'), /Insufficient evidence/);
    }
    for (const [width, height, size] of [[1280, 900, "desktop"], [390, 844, "mobile"]]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      await sleep(150);
      assert.equal(await cdp.evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
      const shot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      writeFileSync(join(out, `${timing}-${size}.png`), Buffer.from(shot.data, "base64"));
    }
  }
  writeFileSync(join(out, "control"), "stop");
  await until(async () => await state() === "DISCONNECTED", "disconnected display");
  assert.equal(await cdp.evaluate('document.querySelector("#fuel").textContent'), "—");
  assert.equal(await cdp.evaluate('document.querySelector("#feedback-body").hidden'), true);
  writeFileSync(join(out, "result.json"), JSON.stringify({ result: "PASS", input: "synthetic",
    states: ["fresh", "stale", "fresh", "disconnected"], widths: [1280, 390],
    skipped_context_packet: "cleared_then_recovered", local_transition_wait_ms: transitionWaitMs,
    late_pre_change_response_ignored: true, clock_recovery_ms: clockRecoveryMs,
    timing_sources: ["captured", "simulator", "mixed"],
    simulator_access: false, mac_route: "NOT RUN", reviewed_card: true,
    fuel_display: "synthetic injected UI evidence, not real supervision" }, null, 2));
  console.log(`PASS real browser dashboard transitions and screenshots: ${out}`);
} finally {
  writeFileSync(join(out, "control"), "stop");
  if (cdp) cdp.socket.close();
  if (chrome && chrome.exitCode === null) chrome.kill();
  await until(() => fixture.exitCode !== null, "fixture exit");
  writeFileSync(join(out, "fixture.log"), fixtureOutput);
  writeFileSync(join(out, "chrome.log"), chromeOutput);
}
