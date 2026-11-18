import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { createSession } from "@earendil-works/pi-durable";
import { openNodeSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/node";

import { rigInputEvidence } from "./definitions.mjs";
import { openPreflightRuntime } from "./preflight-runtime.mjs";

const completed = {
  checkedAt: "2026-11-18T21:00:00Z",
  status: "completed",
  result: {
    steam: { state: "ready", reason: "build_matches", buildId: "123" },
    moza: { state: "unknown", reason: "no_read_only_evidence" },
    controls: { state: "ready", reason: "fixture_observed", needsDrivingCheck: true },
  },
};
const deferred = { checkedAt: "2026-11-18T21:05:00Z", status: "deferred", reason: "rig_active" };
const observed = preflight => ({ scope: "idle_preflight", outcome: "observed", preflight });

async function withDatabase(action, dbPath, use) {
  const runtime = await use(dbPath);
  try {
    return await action(runtime);
  } finally {
    await runtime.close(BACKGROUND_CONTEXT);
  }
}

test("Pi Durable reopens the same scoped Rig Engineer and commits one explicit parent report per cycle", async t => {
  const directory = await mkdtemp(join(tmpdir(), "orc-preflight-runtime-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const dbPath = join(directory, "preflight.sqlite");
  await withDatabase(async runtime => {
    const raw = {
      ...completed,
      activity: { details: "fixture-private" },
      result: {
        ...completed.result,
        controls: { ...completed.result.controls, fingerprint: "fixture-private" },
      },
    };
    assert.deepEqual(await Promise.all([runtime.review(raw), runtime.review(completed)]),
      [observed(completed), observed(completed)]);
    await assert.rejects(runtime.review({ ...completed, checkedAt: "fixture-private" }));
  }, dbPath, openPreflightRuntime);

  await withDatabase(async runtime => {
    assert.deepEqual(await runtime.review(completed), observed(completed));
    assert.deepEqual(await runtime.review({ ...deferred, result: undefined }), observed(deferred));
    assert.deepEqual(await runtime.review(completed), observed(completed));
  }, dbPath, openPreflightRuntime);

  await withDatabase(session => session.commit(async tx => {
    const { items: conversations } = await tx.scanConversations({}, 10);
    assert.equal(conversations.length, 2);
    const histories = await Promise.all(conversations.map(({ id }) =>
      tx.scanEntries({ conversationId: id, order: "ascending" }, 10)));
    const chief = histories.find(({ items }) => items[0]?.kind === "orc.idle-preflight.chief-report.v1").items;
    const rig = histories.find(({ items }) => items[0]?.kind === "orc.idle-preflight.rig-result.v1").items;
    assert.equal(chief.length, 2);
    assert.equal(rig.length, 2);
    assert.equal(typeof rig[0].data.childId, "string");
    assert.equal(rig[1].data.childId, rig[0].data.childId);
    for (const [index, cycle] of [completed, deferred].entries()) {
      assert.deepEqual(rig[index].data.report, observed(cycle));
      assert.deepEqual(chief[index].data, { ...rig[index].data, childEntryId: rig[index].id });
    }
  }, BACKGROUND_CONTEXT), dbPath, async path => createSession(await openNodeSqliteStorage(path)));
});

test("rig inputs deduplicate the projected observation in the existing scoped child", async t => {
  const directory = await mkdtemp(join(tmpdir(), "orc-rig-input-runtime-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const dbPath = join(directory, "preflight.sqlite");
  const control = (axis, others = []) => ({
    in_game: "observed", physical: "timing_correlated", axis,
    windows_response: "observed", other_game_controls: others,
  });
  const inputs = {
    checkedAt: completed.checkedAt,
    schema_version: 1,
    source: "ams2_shared_memory",
    mode: "parked",
    status: "observed",
    reason: "control_responses_observed",
    participant_index: 0,
    player_attribution: "driver_confirmed_view",
    physical_attribution: "timing_correlated",
    controller_status: "sampled",
    frame_count: 100,
    controls: { steering: control("x"), throttle: control("z"), brake: control("r") },
  };
  const expected = { scope: "rig_inputs", outcome: "observed", inputs };
  await withDatabase(async runtime => {
    await runtime.review(completed);
    const raw = {
      ...inputs, controller_id: 0, fingerprint: "fixture-private", path: "fixture-private",
      controls: { ...inputs.controls, brake: { ...inputs.controls.brake, path: "fixture-private" } },
    };
    assert.deepEqual(await Promise.all([runtime.reviewInputs(raw), runtime.reviewInputs(inputs)]),
      [expected, expected]);
    for (const invalid of [
      { ...inputs, mode: "passive" },
      { ...inputs, reason: "a".repeat(64) },
      { ...inputs, controls: { ...inputs.controls, brake: control("z") } },
      { ...inputs, controls: { ...inputs.controls, brake: control("r", ["brake"]) } },
      { ...inputs, controls: { ...inputs.controls, throttle: control("z", ["brake"]) } },
      { ...inputs, controls: { ...inputs.controls, brake: control("r", ["steering", "steering"]) } },
      { ...inputs, controller_status: "unavailable" },
    ]) {
      await assert.rejects(runtime.reviewInputs(invalid));
    }
  }, dbPath, openPreflightRuntime);
  await withDatabase(async runtime => {
    assert.deepEqual(await runtime.reviewInputs(inputs), expected);
    assert.deepEqual(await runtime.review(completed), observed(completed));
  }, dbPath, openPreflightRuntime);

  await withDatabase(session => session.commit(async tx => {
    const { items: conversations } = await tx.scanConversations({}, 10);
    assert.equal(conversations.length, 2);
    const histories = await Promise.all(conversations.map(({ id }) =>
      tx.scanEntries({ conversationId: id, order: "ascending" }, 10)));
    const chief = histories.find(({ items }) => items[0]?.kind === "orc.idle-preflight.chief-report.v1").items;
    const rig = histories.find(({ items }) => items[0]?.kind === "orc.idle-preflight.rig-result.v1").items;
    assert.equal(chief.length, 2);
    assert.equal(rig.length, 2);
    assert.equal(chief[1].kind, "orc.rig-inputs.chief-report.v1");
    assert.equal(rig[1].kind, "orc.rig-inputs.rig-result.v1");
    assert.equal(rig[1].data.childId, rig[0].data.childId);
    assert.deepEqual(rig[1].data.report, expected);
    assert.deepEqual(chief[1].data, { ...rig[1].data, childEntryId: rig[1].id });
  }, BACKGROUND_CONTEXT), dbPath, async path => createSession(await openNodeSqliteStorage(path)));
});

test("the observer host can report that the helper was unavailable", () => {
  const control = { in_game: "not_observed", physical: "not_observed", axis: null };
  const inputs = {
    checkedAt: completed.checkedAt,
    schema_version: 1,
    source: "ams2_shared_memory",
    mode: "passive",
    status: "unavailable",
    reason: "observer_unavailable",
    participant_index: null,
    player_attribution: "unconfirmed",
    physical_attribution: "not_observed",
    controller_status: "not_requested",
    frame_count: 0,
    controls: { steering: control, throttle: control, brake: control },
  };
  assert.deepEqual(rigInputEvidence.parse(inputs), inputs);
});
