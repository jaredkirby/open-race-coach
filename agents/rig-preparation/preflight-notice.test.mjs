import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { publishPreflightNotice } from "./preflight-notice.mjs";

const ready = { state: "ready" };
const unknown = { state: "unknown" };
const blocked = { state: "blocked" };
const powerOff = { ...blocked, reason: "awaiting_connection" };
const deviceProblem = { ...blocked, reason: "device_problem" };
const profileMissing = { ...blocked, reason: "controller_profile_missing" };
const profileDrift = { ...ready, needsDrivingCheck: true };
const profileBaseline = { ...ready, needsDrivingCheck: false };

function cycle(result, status = "completed", activity = "idle") {
  return { status, activity: { state: activity }, result };
}

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "orc-preflight-notice-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return join(directory, "notices.json");
}

test("notice coalesces actionable categories and skips power-off waiting and interrupted cycles", async (t) => {
  const statePath = await fixture(t);
  const messages = [];
  const notify = ({ body }) => { messages.push(body); return "submitted"; };
  const publish = (cycle) => publishPreflightNotice({ statePath, notify, cycle });
  assert.equal((await publish(cycle({ moza: powerOff }))).state, "unchanged");
  assert.equal((await publish(cycle({ steam: blocked }, "deferred", "active"))).state, "skipped");
  assert.equal((await publish(cycle({ steam: blocked, controls: profileMissing }))).state, "submitted");
  assert.deepEqual(messages, ["Steam reported AMS2 install or update work. The configured controller profile file is missing."]);
});

test("unreadable, interrupted, and awaiting evidence retain previous issues across monitor calls", async (t) => {
  const statePath = await fixture(t);
  const events = [];
  const notify = ({ body }) => { events.push(body); return "submitted"; };
  const publish = (cycle) => publishPreflightNotice({ statePath, notify, cycle });
  await publish(cycle({ steam: blocked, moza: deviceProblem, controls: profileDrift }));
  await publish(cycle({ steam: unknown, moza: powerOff, controls: unknown }));
  assert.equal((await publish(cycle({ steam: ready, moza: ready, controls: profileBaseline }, "failed"))).state, "skipped");
  assert.equal((await publish(cycle({ steam: blocked, moza: deviceProblem, controls: profileDrift }))).state, "unchanged");
  await publish(cycle({ steam: ready, moza: ready, controls: profileBaseline }));
  assert.equal((await publish(cycle({ steam: blocked, moza: deviceProblem, controls: profileDrift }))).state, "submitted");
  assert.equal(events.length, 2);
  assert.equal(events[0], events[1]);
});

test("definitive non-submission retries and uncertain delivery is deduplicated", async (t) => {
  const statePath = await fixture(t);
  const observation = cycle({ steam: blocked });
  assert.equal((await publishPreflightNotice({ statePath, cycle: observation, notify: () => "unavailable" })).state, "unavailable");
  assert.equal((await publishPreflightNotice({ statePath, cycle: observation, notify: () => {
    throw new Error("No delivery confirmation");
  } })).state, "ambiguous");
  assert.equal((await publishPreflightNotice({ statePath, cycle: observation, notify: () => {
    assert.fail("Uncertain delivery must not retry automatically");
  } })).state, "unchanged");
});
