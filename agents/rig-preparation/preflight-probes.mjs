import { execFile } from "node:child_process";
import { win32 } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const probeScript = fileURLToPath(new URL("./windows-preflight.ps1", import.meta.url));
const appId = "1066890";
const manifestName = `appmanifest_${appId}.acf`;
const manifestLimit = 1024 * 1024;

async function windowsProbe(probe, signal, probeEnv = {}) {
  if (signal?.aborted || process.platform !== "win32" || !process.env.SystemRoot) return null;
  const powershell = win32.join(process.env.SystemRoot,
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  try {
    const { stdout } = await execute(powershell,
      ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", probeScript, "-Probe", probe], {
        windowsHide: true,
        signal,
        timeout: probe === "activity" ? 5_000 : 10_000,
        maxBuffer: 3 * manifestLimit,
        env: {
          ...process.env,
          ORC_PREFLIGHT_STEAM_MANIFEST: "",
          ORC_PREFLIGHT_MOZA_IDS: "",
          ...probeEnv,
        },
      });
    return JSON.parse(stdout);
  } catch {
    return null;
  }
}

export function classifyWindowsActivity(observation, minimumIdleSeconds) {
  if (!Number.isInteger(minimumIdleSeconds) || minimumIdleSeconds < 1 || minimumIdleSeconds > 86_400) {
    return { state: "unknown", reason: "invalid_idle_threshold" };
  }
  if (observation?.simulatorRunning === true) {
    return { state: "active", reason: observation.ams2Running === true ? "ams2_running" : "simulator_running" };
  }
  const elapsed = observation?.idleMilliseconds;
  if (observation?.simulatorRunning !== false || !Number.isSafeInteger(elapsed) || elapsed < 0) {
    return { state: "unknown", reason: "activity_unavailable" };
  }
  return elapsed >= minimumIdleSeconds * 1000
    ? { state: "idle", reason: "idle_threshold_met" }
    : { state: "active", reason: "recent_input" };
}

export async function readWindowsActivity({ minimumIdleSeconds = 300, signal } = {}) {
  return classifyWindowsActivity(await windowsProbe("activity", signal), minimumIdleSeconds);
}

export async function readWindowsAms2Owner(signal) {
  const value = await windowsProbe("ams2owner", signal);
  return typeof value?.owner === "string" && /^[0-9]+-[0-9]+$/.test(value.owner) ? value.owner : null;
}

// Steam app manifests use the quoted text subset of Valve KeyValues. Require one
// complete tree with unique keys, including in nested directories and depot lists.
function parseValveManifest(source) {
  const tokenPattern = /\s+|\/\/[^\r\n]*|"(?:[^"\\\r\n]|\\[^\r\n])*"|[{}]/gy;
  let offset = 0;
  function next() {
    while (offset < source.length) {
      tokenPattern.lastIndex = offset;
      const token = tokenPattern.exec(source)?.[0];
      if (!token) throw new Error("Invalid manifest token");
      offset = tokenPattern.lastIndex;
      if (token[0] === '"') return { text: token.slice(1, -1).replace(/\\(["\\])/g, "$1") };
      if (token === "{" || token === "}") return token;
    }
    return null;
  }

  function section(depth) {
    if (depth > 16) throw new Error("Manifest nesting exceeds limit");
    const entries = new Map();
    while (true) {
      const key = next();
      if (key === "}") return entries;
      if (typeof key?.text !== "string") throw new Error("Invalid manifest key");
      const name = key.text.toLowerCase();
      if (entries.has(name)) throw new Error("Duplicate manifest key");
      const value = next();
      if (value === "{") entries.set(name, section(depth + 1));
      else if (typeof value?.text === "string") entries.set(name, value.text);
      else throw new Error("Invalid manifest value");
    }
  }

  if (next()?.text?.toLowerCase() !== "appstate" || next() !== "{") {
    throw new Error("Missing AppState");
  }
  const result = section(0);
  if (next() !== null) throw new Error("Trailing manifest data");
  return result;
}

function unsignedField(manifest, key) {
  const value = manifest.get(key);
  if (typeof value !== "string" || !/^(?:0|[1-9][0-9]{0,19})$/.test(value)) {
    throw new Error("Invalid numeric manifest field");
  }
  return BigInt(value);
}

export function classifySteamManifest(source) {
  const invalid = { state: "unknown", reason: "manifest_invalid" };
  if (typeof source !== "string" || Buffer.byteLength(source) > manifestLimit) return invalid;
  try {
    const manifest = parseValveManifest(source);
    if (manifest.get("appid") !== appId) return invalid;
    const flags = unsignedField(manifest, "stateflags");
    // FullyInstalled alone can be ready. Any other recognized state bit blocks;
    // a bit outside the observed Steam state model leaves the result unknown.
    const knownFlags = 0xff1fffn;
    if ((flags & ~knownFlags) !== 0n) return invalid;
    const blocked = { state: "blocked", reason: "update_or_install_pending" };
    if (flags !== 4n) return blocked;
    const build = unsignedField(manifest, "buildid");
    if (build === 0n || unsignedField(manifest, "sizeondisk") === 0n) return invalid;
    const downloaded = unsignedField(manifest, "bytesdownloaded");
    const toDownload = unsignedField(manifest, "bytestodownload");
    const staged = unsignedField(manifest, "bytesstaged");
    const toStage = unsignedField(manifest, "bytestostage");
    const target = unsignedField(manifest, "targetbuildid");
    const updateResult = unsignedField(manifest, "updateresult");
    if (downloaded > toDownload || staged > toStage) return invalid;
    if (downloaded < toDownload || staged < toStage || updateResult !== 0n
      || (target !== 0n && target !== build)) return blocked;
    return { state: "ready", reason: "installed_without_pending_work", buildId: build.toString() };
  } catch {
    return invalid;
  }
}

async function readSteam(steamManifestPath, signal) {
  if (steamManifestPath !== undefined && (typeof steamManifestPath !== "string"
    || steamManifestPath.length > 1024 || !/^[a-z]:[\\/]/i.test(steamManifestPath)
    || steamManifestPath.includes("\0") || win32.basename(steamManifestPath).toLowerCase() !== manifestName)) {
    return { state: "unknown", reason: "manifest_unavailable" };
  }
  const observed = await windowsProbe("steam", signal, { ORC_PREFLIGHT_STEAM_MANIFEST: steamManifestPath ?? "" });
  if (!observed) return { state: "unknown", reason: "steam_probe_unavailable" };
  if (observed.manifest === null) return { state: "unknown", reason: "manifest_unavailable" };
  return classifySteamManifest(observed.manifest);
}

export function classifyMozaPresence(observation, expectedCount) {
  const states = observation?.deviceStates;
  if (!Number.isInteger(expectedCount) || expectedCount < 1 || expectedCount > 16
    || !Array.isArray(states) || states.length !== expectedCount
    || states.some((state) => !["present", "missing", "problem"].includes(state))) {
    return { state: "unknown", reason: "pnp_unavailable" };
  }
  if (states.includes("problem")) return { state: "blocked", reason: "device_problem" };
  if (states.includes("missing")) return { state: "blocked", reason: "awaiting_connection" };
  return { state: "ready", reason: "expected_devices_present" };
}

async function readMoza(expectedMozaDevices, signal) {
  if (!Array.isArray(expectedMozaDevices) || expectedMozaDevices.length < 1 || expectedMozaDevices.length > 16
    || Array.from(expectedMozaDevices).some((device) => typeof device?.label !== "string"
      || !device.label.trim() || device.label.length > 100 || typeof device.instanceId !== "string"
      || !/^[^\\\x00-\x1f]+\\[^\\\x00-\x1f]+\\[^\\\x00-\x1f]+$/.test(device.instanceId)
      || device.instanceId.length > 200)
    || new Set(expectedMozaDevices.map((device) => device.instanceId.toUpperCase())).size !== expectedMozaDevices.length) {
    return { state: "unknown", reason: "expected_devices_unconfigured" };
  }
  const instanceIds = expectedMozaDevices.map((device) => device.instanceId);
  const observed = await windowsProbe("moza", signal, { ORC_PREFLIGHT_MOZA_IDS: JSON.stringify(instanceIds) });
  return classifyMozaPresence(observed, instanceIds.length);
}

export async function collectWindowsPreflight({ steamManifestPath, expectedMozaDevices, signal } = {}) {
  const [steam, moza] = await Promise.all([readSteam(steamManifestPath, signal), readMoza(expectedMozaDevices, signal)]);
  return { steam, moza };
}
