import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { readControlProfile } from "./control-profile.mjs";
import { acceptControlProfile, readIdleMonitorState, runIdleMonitor } from "./idle-monitor.mjs";

async function stateFile(t) {
  const directory = await mkdtemp(join(tmpdir(), "orc-idle-monitor-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return join(directory, "monitor.json");
}

const idle = async () => ({ state: "idle", reason: "console_idle" });
const result = (buildId, moza = "ready", steam = "ready") => ({
  steam: { state: steam, reason: "test_observation", buildId },
  moza: { state: moza, reason: "test_observation" },
});

test("active, unknown and failed activity observations defer before collection", async (t) => {
  const statePath = await stateFile(t);
  const collect = t.mock.fn();
  assert.equal(await readIdleMonitorState(statePath), null);

  for (const state of ["active", "unknown", "error"]) {
    const cycle = await runIdleMonitor({
      statePath,
      readActivity: async () => {
        if (state === "error") throw new Error("input unavailable");
        return { state, reason: state };
      },
      collect,
    });
    assert.equal(cycle.status, "deferred");
    assert.equal(cycle.reason, state === "error" ? "activity_observation_failed" : state);
    assert.deepEqual(await readIdleMonitorState(statePath), {
      schemaVersion: 1, baseline: {}, lastCycle: cycle, lastCompleted: null,
    });
  }
  assert.equal(collect.mock.callCount(), 0);
});

test("tracks installed build and device changes across unknown or failed results", async (t) => {
  const statePath = await stateFile(t);
  const run = (collect) => runIdleMonitor({ statePath, readActivity: idle, collect });
  const initial = await run(async () => result("100"));
  assert.deepEqual(initial.changes, []);

  const unknown = await run(async () => result(undefined, "unknown", "unknown"));
  assert.equal(unknown.status, "completed");
  assert.deepEqual(unknown.changes, []);

  const failed = await run(async () => { throw new Error("private test detail"); });
  assert.equal(failed.status, "failed");
  assert.equal(failed.reason, "preflight_collection_failed");
  const state = await readIdleMonitorState(statePath);
  assert.deepEqual(state.baseline, {
    "steam.state": "ready", "steam.buildId": "100", "moza.state": "ready",
  });
  assert.deepEqual(state.lastCycle, failed);
  assert.equal(state.lastCompleted.result.steam.state, "unknown");

  const changed = await run(async () => result("101", "blocked"));
  assert.deepEqual(changed.changes, [
    { item: "moza.state", previous: "ready", current: "blocked" },
    { item: "steam.buildId", previous: "100", current: "101" },
  ]);
  const deferred = await runIdleMonitor({
    statePath, readActivity: async () => ({ state: "active", reason: "sim_running" }),
    collect: async () => assert.fail("active simulator admits no preflight"),
  });
  assert.deepEqual((await readIdleMonitorState(statePath)).lastCompleted, changed);
  assert.deepEqual((await readIdleMonitorState(statePath)).lastCycle, deferred);
});

test("a return to the rig or lost activity observation discards in-flight probe results", async (t) => {
  const statePath = await stateFile(t);
  const initial = await runIdleMonitor({ statePath, readActivity: idle, collect: async () => result("100") });

  for (const state of ["active", "unknown"]) {
    let probed = false;
    const activity = { state, reason: "post_probe_activity" };
    const cycle = await runIdleMonitor({
      statePath,
      readActivity: async () => (probed ? activity : idle()),
      collect: async () => {
        probed = true;
        return result("101", "blocked");
      },
    });
    assert.equal(cycle.status, "deferred");
    assert.deepEqual(cycle.activity, activity);
    assert.equal(cycle.reason, activity.reason);
    assert.equal(cycle.result, undefined);
    assert.deepEqual(cycle.changes, []);
    const saved = await readIdleMonitorState(statePath);
    assert.deepEqual(saved.lastCompleted, initial);
    assert.equal(saved.baseline["steam.buildId"], "100");
  }
});

test("profile drift stays visible against the first observed bytes across runs and missing files", async (t) => {
  const statePath = await stateFile(t);
  const run = (controls) => runIdleMonitor({
    statePath, readActivity: idle, collect: async () => ({ ...result("100"), controls }),
  });
  const ready = (fingerprint) => ({ state: "ready", reason: "controller_profile_fingerprinted", fingerprint });
  const first = "1".repeat(64);
  const changed = "2".repeat(64);
  await run(undefined);
  assert.equal((await run(ready(first))).result.controls.needsDrivingCheck, false);
  for (let runNumber = 0; runNumber < 2; runNumber++) {
    const cycle = await run(ready(changed));
    assert.equal(cycle.result.controls.needsDrivingCheck, true);
    assert.deepEqual(cycle.changes, [{ item: "controls.fingerprint", previous: first, current: changed }]);
  }

  const missing = await run({ state: "blocked", reason: "controller_profile_missing" });
  assert.equal(missing.result.controls.needsDrivingCheck, true);
  assert.deepEqual(missing.changes, []);
  const saved = await readIdleMonitorState(statePath);
  assert.equal(saved.baseline["controls.fingerprint"], first);
  assert.equal(saved.needsDrivingCheck, true);
  assert.equal((await run(ready(first))).result.controls.needsDrivingCheck, false);
  assert.equal((await readIdleMonitorState(statePath)).needsDrivingCheck, false);
});

test("accepting a profile requires matching last-completed evidence and a fresh read", async (t) => {
  const statePath = await stateFile(t);
  const controllerProfilePath = join(dirname(statePath), "bindings.sav");
  const run = () => runIdleMonitor({
    statePath, readActivity: idle,
    collect: async () => ({ ...result("100"), controls: await readControlProfile(controllerProfilePath) }),
  });
  const accept = (fingerprint) => acceptControlProfile({ statePath, controllerProfilePath, fingerprint });
  await writeFile(controllerProfilePath, "first");
  const first = await run();
  await writeFile(controllerProfilePath, "second");
  const changed = await run();
  const { fingerprint } = changed.result.controls;
  await assert.rejects(accept(fingerprint.slice(0, -1)), /full controller profile fingerprint/);
  await assert.rejects(accept(first.result.controls.fingerprint), /last completed idle preflight/);
  await writeFile(controllerProfilePath, "third");
  await assert.rejects(accept(fingerprint), /current controller profile/);
  assert.equal((await readIdleMonitorState(statePath)).needsDrivingCheck, true);

  await writeFile(controllerProfilePath, "second");
  assert.deepEqual(await accept(fingerprint), { fingerprint, needsDrivingCheck: false });
  const accepted = await readIdleMonitorState(statePath);
  assert.equal(accepted.baseline["controls.fingerprint"], fingerprint);
  assert.deepEqual(accepted.lastCompleted, changed);
  assert.deepEqual(accepted.lastCycle, changed);
  assert.equal((await run()).result.controls.needsDrivingCheck, false);
});

test("rejects non-code probe reasons and malformed build IDs", async (t) => {
  const statePath = await stateFile(t);
  for (const reading of [
    { ...result("100"), moza: { state: "ready", reason: 1 } },
    { ...result("100"), moza: { state: "ready", reason: "unbounded free text" } },
    result("not_numeric"),
  ]) {
    const cycle = await runIdleMonitor({ statePath, readActivity: idle, collect: async () => reading });
    assert.equal(cycle.status, "failed");
    assert.equal(cycle.result, undefined);
  }
  assert.equal((await readIdleMonitorState(statePath)).lastCompleted, null);
});

test("watch evaluates startup and repeats until cancelled", async (t) => {
  const statePath = await stateFile(t);
  const controller = new AbortController();
  const cycles = [];
  let polls = 0;
  const last = await runIdleMonitor({
    statePath, watch: true, intervalMs: 1, minimumIdleSeconds: 10,
    signal: controller.signal,
    readActivity: async ({ minimumIdleSeconds, signal }) => {
      assert.equal(minimumIdleSeconds, 10);
      assert.equal(signal, controller.signal);
      if (++polls === 1) return { state: "active", reason: "recent_input" };
      return idle();
    },
    collect: async ({ signal }) => {
      assert.equal(signal, controller.signal);
      return result("200");
    },
    onCycle(cycle) {
      cycles.push(cycle);
      if (cycles.length === 2) controller.abort();
    },
  });
  assert.deepEqual(cycles.map((cycle) => cycle.status), ["deferred", "completed"]);
  assert.equal(last, cycles[1]);
  assert.deepEqual((await readIdleMonitorState(statePath)).lastCycle, last);
});
