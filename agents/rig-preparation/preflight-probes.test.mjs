import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { win32 } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import {
  classifyMozaPresence,
  classifySteamManifest,
  classifyWindowsActivity,
  collectWindowsPreflight,
} from "./preflight-probes.mjs";

const execFile = promisify(execFileCallback);
const windowsUnavailable = process.platform !== "win32" || !process.env.SystemRoot;

async function runSyntheticNativeFunction(name, fixtureCode) {
  // Load the bounded probe function without invoking the real device or process providers.
  const command = `
    $source = [IO.File]::ReadAllText($env:ORC_TEST_PREFLIGHT_SCRIPT)
    $target = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$null, [ref]$null).Find({
      param($node)
      $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq '${name}'
    }, $true)
    . ([scriptblock]::Create($target.Extent.Text))
    ${fixtureCode}
  `;
  const { stdout } = await execFile(win32.join(process.env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
    ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")], {
      timeout: 5000,
      windowsHide: true,
      env: { ...process.env, ORC_TEST_PREFLIGHT_SCRIPT: fileURLToPath(new URL("./windows-preflight.ps1", import.meta.url)) },
    });
  return stdout.trim();
}

// Synthetic Steam KeyValues representing common local app manifest states.
function manifest(overrides = {}, extra = '"InstalledDepots" { "200" { "manifest" "123" } }') {
  const fields = {
    appid: "1066890",
    StateFlags: "4",
    buildid: "100",
    SizeOnDisk: "200",
    BytesToDownload: "20",
    BytesDownloaded: "20",
    BytesToStage: "40",
    BytesStaged: "40",
    TargetBuildID: "100",
    UpdateResult: "0",
    ...overrides,
  };
  const entries = Object.entries(fields)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `"${key}" "${value}"`).join("\n");
  return `"AppState" {\n${entries}\n${extra}\n}`;
}

test("Steam projects only a complete installed manifest with no pending work", () => {
  assert.deepEqual(classifySteamManifest(manifest()), {
    state: "ready", reason: "installed_without_pending_work", buildId: "100",
  });
  assert.equal(classifySteamManifest(manifest({ TargetBuildID: "0" })).state, "ready");
  for (const override of [
    { StateFlags: "6" }, { StateFlags: "260" }, { StateFlags: "2097156" },
    { BytesDownloaded: "19" }, { BytesStaged: "39" }, { UpdateResult: "1" }, { TargetBuildID: "101" },
  ]) {
    assert.deepEqual(classifySteamManifest(manifest(override)), {
      state: "blocked", reason: "update_or_install_pending",
    });
  }
});

test("Steam fails unknown on malformed, incomplete, conflicting, and unfamiliar state", () => {
  for (const source of [
    null, "text", manifest().slice(0, -1), `${manifest()} {}`, manifest({ appid: "42" }),
    manifest({ StateFlags: "16777220" }), manifest({ BytesToStage: undefined }),
    manifest({ BytesDownloaded: "21" }), manifest({ buildid: "0" }),
    manifest({ SizeOnDisk: "0" }), manifest({ BytesDownloaded: "NaN" }),
    manifest({}, '"stateFLAGS" "4"'), manifest({}, '"Nested" { "a" "1" "A" "2" }'),
    " ".repeat(1024 * 1024 + 1),
  ]) {
    assert.deepEqual(classifySteamManifest(source), { state: "unknown", reason: "manifest_invalid" });
  }
});

test("MOZA readiness requires every configured logical device in the Windows snapshot", () => {
  assert.deepEqual(classifyMozaPresence({ deviceStates: ["present", "present"] }, 2), {
    state: "ready", reason: "expected_devices_present",
  });
  assert.deepEqual(classifyMozaPresence({ deviceStates: ["present", "missing"] }, 2), {
    state: "blocked", reason: "awaiting_connection",
  });
  assert.deepEqual(classifyMozaPresence({ deviceStates: ["missing", "problem"] }, 2), {
    state: "blocked", reason: "device_problem",
  });
  for (const source of [null, { deviceStates: ["present"] }, { deviceStates: ["unknown", "present"] },
    { deviceStates: ["present", "unsupported"] }]) {
    assert.deepEqual(classifyMozaPresence(source, 2), { state: "unknown", reason: "pnp_unavailable" });
  }
});

test("Windows idle requires a known racing simulator process state and usable input counter", () => {
  assert.deepEqual(classifyWindowsActivity({ simulatorRunning: true }, 300), {
    state: "active", reason: "simulator_running",
  });
  assert.deepEqual(classifyWindowsActivity({ simulatorRunning: false, idleMilliseconds: 299999 }, 300), {
    state: "active", reason: "recent_input",
  });
  assert.deepEqual(classifyWindowsActivity({ simulatorRunning: false, idleMilliseconds: 300000 }, 300), {
    state: "idle", reason: "idle_threshold_met",
  });
  assert.deepEqual(classifyWindowsActivity({ simulatorRunning: false, idleMilliseconds: 300000 }, 0), {
    state: "unknown", reason: "invalid_idle_threshold",
  });
  for (const source of [null, { idleMilliseconds: 300000 },
    { simulatorRunning: false, idleMilliseconds: -1 }, { simulatorRunning: false, idleMilliseconds: "300000" }]) {
    assert.deepEqual(classifyWindowsActivity(source, 300), { state: "unknown", reason: "activity_unavailable" });
  }
});

test("native activity guard recognizes AMS2, AC and ACC process names", {
  skip: windowsUnavailable,
}, async () => {
  const stdout = await runSyntheticNativeFunction("Get-RacingSimulators", `
    function Get-Process { [pscustomobject]@{ ProcessName = $script:fixtureProcess } }
    foreach ($script:fixtureProcess in 'AMS2', 'AMS2AVX', 'acs', 'acs_x86', 'AC2-Win64-Shipping', 'ACC', 'Explorer') {
      @(Get-RacingSimulators).Count -gt 0
    }
  `);
  assert.deepEqual(stdout.split(/\r?\n/), ["True", "True", "True", "True", "True", "True", "False"]);
});

test("Windows PowerShell keeps configured PnP IDs as separate observations", { skip: windowsUnavailable }, async () => {
  const stdout = await runSyntheticNativeFunction("Read-MozaPresence", `
    function Get-PnpDevice { }
    $env:ORC_PREFLIGHT_MOZA_IDS = '["syntheticA", "syntheticB"]'
    ConvertTo-Json -Compress -InputObject (Read-MozaPresence)
  `);
  assert.deepEqual(JSON.parse(stdout), { deviceStates: ["missing", "missing"] });
});

test("invalid Steam locations and duplicate case-insensitive PnP identities fail before native reads", async () => {
  assert.deepEqual(await collectWindowsPreflight({
    steamManifestPath: "\\\\example\\share\\appmanifest_1066890.acf",
    expectedMozaDevices: [
      { label: "first", instanceId: "USB\\VID_0000&PID_0000\\FIXTURE" },
      { label: "second", instanceId: "usb\\vid_0000&pid_0000\\fixture" },
    ],
  }), {
    steam: { state: "unknown", reason: "manifest_unavailable" },
    moza: { state: "unknown", reason: "expected_devices_unconfigured" },
  });
});
