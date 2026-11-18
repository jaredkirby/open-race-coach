import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { runCommand } from "./preflight.mjs";

test("the CLI binds its Pi report, persisted baseline and follow-up to the selected rig", async t => {
  const directory = await mkdtemp(join(tmpdir(), "orc-preflight-cli-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const bindings = join(directory, "controls.sav");
  const profile = join(directory, "profile.json");
  await writeFile(bindings, "fixture 1");
  await writeFile(profile, JSON.stringify({ controllerProfilePath: bindings }));
  let activity = { state: "idle", reason: "idle_threshold_met" };
  let output;
  let notices = 0;
  const deps = {
    readActivity: async () => activity,
    collect: async () => ({
      steam: { state: "ready", reason: "installed_without_pending_work", buildId: "315" },
      moza: { state: "blocked", reason: "awaiting_connection" },
    }),
    publish: async () => { notices++; return { state: "skipped" }; },
    print: value => { output = value; },
  };
  async function invoke(command, ...extra) {
    await runCommand([command, "--data-dir", directory, ...extra], deps);
    return output;
  }
  assert.match(await invoke("status"), /No idle preflight/);
  assert.equal(JSON.parse(await invoke("once", "--json")).report.preflight.status, "completed");
  await writeFile(bindings, "fixture 2");
  await invoke("once");
  activity = { state: "active", reason: "simulator_running" };
  await invoke("once");
  assert.equal(notices, 3);
  const state = JSON.parse(await invoke("status", "--json"));
  assert.equal(state.lastCycle.status, "deferred");
  assert.equal(state.lastCompleted.result.controls.needsDrivingCheck, true);
  assert.match(await invoke("status"), /Verify steering and pedal/);
  await invoke("accept-controls", "--fingerprint", state.lastCompleted.result.controls.fingerprint);
  assert.doesNotMatch(await invoke("status"), /Verify steering and pedal/);
  await writeFile(profile, JSON.stringify({ controllerProfilePath: join(directory, "different.sav") }));
  assert.match(await invoke("status"), /No idle preflight/);
  assert.ok((await readdir(directory)).includes("writer.sqlite"));
});

test("two CLI processes cannot advance the same baseline at once", async t => {
  const dir = await mkdtemp(join(tmpdir(), "orc-preflight-lock-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  let release, started;
  const startedSignal = new Promise(resolve => { started = resolve; });
  const guard = new Promise(resolve => { release = resolve; });
  const args = ["once", "--data-dir", dir];
  const deps = {
    readActivity: async () => { started(); await guard; return { state: "active", reason: "recent_input" }; },
    publish: async () => ({ state: "skipped" }), print() {},
  };
  const first = runCommand(args, deps);
  try {
    await Promise.race([startedSignal, first]);
    await assert.rejects(runCommand(args, deps), /Another Rig Engineer/);
  } finally {
    release();
    await first;
  }
});

test("scheduled observation, driver check and missing-only repair share the real CLI and durable state", async t => {
  const dir = await mkdtemp(join(tmpdir(), "orc-rig-recovery-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const file = join(dir, "controls.sav");
  await writeFile(join(dir, "profile.json"), JSON.stringify({ controllerProfilePath: file }));
  await writeFile(file, "fixture bindings");
  let reason = "idle_threshold_met";
  let output;
  const deps = {
    readActivity: async () => ({ state: reason === "idle_threshold_met" ? "idle" : "active", reason }),
    readOwner: async () => "fixture-owner",
    collect: async () => ({ steam: { state: "ready", reason: "installed_without_pending_work" },
      moza: { state: "ready", reason: "expected_devices_present" } }),
    publish: async () => ({ state: "unchanged" }), print: text => { output = text; },
    readInputs: async ({ parked }) => ({
      schema_version: 1, source: "ams2_shared_memory", mode: parked ? "parked" : "passive",
      status: parked ? "observed" : "incomplete", reason: parked ? "control_responses_observed" : "local_player_unconfirmed",
      controller_status: "not_requested", frame_count: 50, participant_index: 0,
      player_attribution: parked ? "driver_confirmed_view" : "unconfirmed",
      physical_attribution: parked ? "driver_confirmed_gesture" : "not_observed",
      controls: Object.fromEntries(["steering", "throttle", "brake"].map(name =>
        [name, { in_game: "observed", physical: parked ? "driver_confirmed_gesture" : "not_observed", axis: null }])),
    }),
  };
  const call = async (...args) => { await runCommand([...args, "--data-dir", dir], deps); return output; };
  await call("once");
  reason = "ams2_running";
  const passive = JSON.parse(await call("once", "--json"));
  assert.equal(passive.report.preflight.status, "deferred");
  assert.equal(passive.inputs.inputs.player_attribution, "unconfirmed");
  assert.equal(JSON.parse(await call("status", "--json")).repairFingerprint, undefined);
  assert.equal(JSON.parse(await call("check-inputs", "--parked-driver", "--enable-repair", "--json")).armed, true);
  await rm(file);
  await call("once");
  await assert.rejects(readFile(file), { code: "ENOENT" });
  reason = "idle_threshold_met";
  const repaired = JSON.parse(await call("once", "--json")).report.preflight.result;
  assert.equal(repaired.controls.state, "ready");
  assert.equal(repaired.repair.status, "repaired");
  assert.equal(await readFile(file, "utf8"), "fixture bindings");
  await call("disable-repair");
  await rm(file);
  await call("once");
  await assert.rejects(readFile(file), { code: "ENOENT" });
  const state = JSON.parse(await call("status", "--json"));
  assert.equal(state.lastRepair.status, "repaired");
  assert.equal(state.inputs.lastDriverCheck.evidence.status, "observed");
});
