import assert from "node:assert/strict";
import { test } from "node:test";
import { SPEC_VERSION, validateAgentDefinition } from "@standardagents/spec";

import { defineRigPreparation } from "./definitions.mjs";

const threadId = "fixture-rig-thread";
const id = "1".repeat(32);
const fingerprint = "a".repeat(64);
const request = { command_id: id, fingerprint };
const message = { role: "user", content: JSON.stringify(request), attachments: null };
const row = { id, fingerprint, state: "unknown" };
const definitions = (readReceipt, readLastPreflight, readLastInputs) => defineRigPreparation({
  chiefModel: "fixture_chief", readReceipt, readLastPreflight, readLastInputs,
});
const preflightMessage = { ...message, content: '{"review":"last_idle_preflight"}' };
const preflight = {
  checkedAt: "2026-11-18T21:00:00Z",
  status: "completed",
  result: {
    steam: { state: "ready", reason: "build_matches", buildId: "123" },
    moza: { state: "unknown", reason: "no_read_only_evidence" },
  },
};

test("the Chief owns one nonblocking handler-backed Rig Engineer", () => {
  const contract = definitions(async () => null);
  const chief = contract.agents.race_chief;
  const rig = contract.agents.rig_engineer;
  assert.equal(SPEC_VERSION, "0.2.0");
  assert.deepEqual(validateAgentDefinition(chief), []);
  assert.deepEqual(validateAgentDefinition(rig), []);
  assert.deepEqual(chief.sideA.subagents, [{
    agent: rig.name,
    maxInstances: 1,
    blocking: false,
    parentCommunication: "explicit",
  }]);
  assert.deepEqual(chief.sideB, { human: true });
  assert.equal(rig.primary, false);
  assert.deepEqual(rig.sideA, { handler: "review_preparation" });
  assert.equal(typeof contract.handlers[rig.sideA.handler].execute, "function");
  assert.deepEqual(Object.keys(contract.prompts), ["race_chief"]);
  assert.deepEqual(Object.keys(contract.tools ?? {}), []);
});

test("the host must supply a scoped receipt reader", () => {
  assert.throws(() => definitions(undefined), /scoped receipt reader/);
  assert.throws(() => definitions(assert.fail, {}), /scoped preflight reader/);
  assert.doesNotMatch(definitions(assert.fail).prompts.race_chief.prompt, /last_idle_preflight/);
  assert.match(definitions(assert.fail, assert.fail).prompts.race_chief.prompt, /last_idle_preflight/);
});

async function review(readReceipt, inbound = message, activeThread = threadId, readLastPreflight,
  readLastInputs) {
  const handler = definitions(readReceipt, readLastPreflight, readLastInputs).handlers.review_preparation;
  const stops = [];
  const state = { threadId: activeThread, stopSession: async value => stops.push(value) };
  await handler.execute(state, inbound);
  assert.equal(stops.length, 1);
  assert.equal(stops[0].result, stops[0].notifyParent);
  return JSON.parse(stops[0].result);
}

test("rig input reviews use only the bound child evidence reader", async () => {
  const inputsMessage = { ...message, content: '{"review":"last_rig_inputs"}' };
  const scope = "rig_inputs";
  assert.doesNotMatch(definitions(assert.fail).prompts.race_chief.prompt, /last_rig_inputs/);
  assert.match(definitions(assert.fail, undefined, assert.fail).prompts.race_chief.prompt, /last_rig_inputs/);
  const scopedReader = async (child, signal) => {
    assert.equal(child, threadId);
    assert.equal(signal.aborted, false);
    return null;
  };
  assert.deepEqual(await review(assert.fail, inputsMessage, threadId, assert.fail, scopedReader),
    { scope, outcome: "missing", inputs: null });
  assert.deepEqual(await review(assert.fail, inputsMessage, "", assert.fail, assert.fail),
    { scope, outcome: "inputs_unavailable" });
  assert.deepEqual(await review(assert.fail, {
    ...inputsMessage, content: '{"review":"last_rig_inputs","threadId":"other"}',
  }, threadId, assert.fail, assert.fail), { scope: "synthetic_input", outcome: "invalid_review" });
});

test("idle preflight reads one scoped snapshot and strips private fields at every level", async () => {
  let calls = 0;
  const report = await review(assert.fail, preflightMessage, threadId, async (child, signal) => {
    calls += 1;
    assert.equal(child, threadId);
    assert.equal(signal.aborted, false);
    return {
      ...preflight,
      path: "fixture-private",
      result: {
        ...preflight.result,
        steam: { ...preflight.result.steam, manifest: "fixture-private" },
        moza: { ...preflight.result.moza, device: "fixture-private" },
        debug: "fixture-private",
      },
    };
  });
  assert.equal(calls, 1);
  assert.deepEqual(report, { scope: "idle_preflight", outcome: "observed", preflight });
});

test("idle preflight distinguishes missing, deferred, failed and unavailable evidence", async () => {
  const check = read => review(assert.fail, preflightMessage, threadId, read);
  const scope = "idle_preflight";
  assert.deepEqual(await check(async () => null), { scope, outcome: "missing", preflight: null });
  for (const status of ["deferred", "failed"]) {
    const attempt = { checkedAt: preflight.checkedAt, status, reason: "fixture_unavailable" };
    assert.deepEqual(await check(async () => attempt), {
      scope, outcome: "observed", preflight: attempt,
    });
  }
  for (const read of [
    undefined,
    async () => ({ ...preflight, checkedAt: "fixture-private" }),
    async () => ({ ...preflight, reason: "C:/fixture-private" }),
    async () => ({ ...preflight, result: undefined }),
    async () => ({ ...preflight, status: "deferred" }),
    async () => { throw new Error("fixture-private transport error"); },
  ]) {
    assert.deepEqual(await check(read), { scope, outcome: "preflight_unavailable" });
  }
});

test("idle preflight rejects authority, read paths and caller-selected scopes", async () => {
  for (const message of [
    { ...preflightMessage, content: '{"review":"run_idle_preflight"}' },
    { ...preflightMessage, content: '{"review":"last_idle_preflight","threadId":"other"}' },
    { ...preflightMessage, content: '{"review":"last_idle_preflight","path":"other"}' },
    { ...preflightMessage, tool_calls: [{ id: "fixture" }] },
  ]) {
    assert.deepEqual(await review(assert.fail, message, threadId, assert.fail), {
      scope: "synthetic_input", outcome: "invalid_review",
    });
  }
  assert.deepEqual(await review(assert.fail, preflightMessage, "", assert.fail), {
    scope: "idle_preflight", outcome: "preflight_unavailable",
  });
  assert.deepEqual(await review(async () => row, message, threadId, assert.fail), {
    scope: "synthetic_input", ...request, outcome: "observed", receipt: row,
  });
});

test("a stalled idle preflight read aborts and reports unavailable without retrying", async () => {
  let calls = 0;
  let signal;
  const report = await review(assert.fail, preflightMessage, threadId, (_thread, readSignal) => {
    calls += 1;
    signal = readSignal;
    return new Promise(() => {});
  });
  assert.equal(calls, 1);
  assert.equal(signal.aborted, true);
  assert.deepEqual(report, { scope: "idle_preflight", outcome: "preflight_unavailable" });
});

test("each parent request reads current scoped evidence exactly once and strips private fields", async () => {
  let current = { ...row, state: "queued" };
  let lookups = 0;
  const readReceipt = async (activeThread, commandId) => {
    lookups += 1;
    assert.equal(activeThread, threadId);
    assert.equal(commandId, id);
    return { ...current, token: "fixture-private", reason: "fixture-private" };
  };
  for (const sample of [current, row]) {
    current = sample;
    assert.deepEqual(await review(readReceipt), {
      scope: "synthetic_input", ...request, outcome: "observed", receipt: sample,
    });
  }
  assert.equal(lookups, 2);
});

test("missing evidence stays distinct from another attempt or an unavailable reader", async () => {
  const base = { scope: "synthetic_input", ...request };
  assert.deepEqual(await review(async () => null), { ...base, outcome: "missing", receipt: null });
  for (const readReceipt of [
    async () => ({ ...row, id: "2".repeat(32) }),
    async () => ({ ...row, fingerprint: "b".repeat(64) }),
    async () => ({ ...row, state: "ready_to_drive" }),
    async () => { throw new Error("fixture-private transport error"); },
  ]) {
    assert.deepEqual(await review(readReceipt), { ...base, outcome: "receipt_unavailable" });
  }
});

test("a stalled receipt read aborts and reports unavailable without a retry", async () => {
  let calls = 0;
  let signal;
  const report = await review((_thread, _id, readSignal) => {
    calls += 1;
    signal = readSignal;
    return new Promise(() => {});
  });
  assert.equal(signal.aborted, true);
  assert.equal(calls, 1);
  assert.deepEqual(report, { scope: "synthetic_input", ...request, outcome: "receipt_unavailable" });
});

test("malformed or authority-bearing requests never reach the host reader", async () => {
  const invalid = [
    id, null, "[]", "{", " ".repeat(257),
    JSON.stringify({ ...request, fingerprint: "A".repeat(64) }),
    JSON.stringify({ ...request, command_id: id.slice(1) + "\n" }),
    JSON.stringify({ ...request, op: "submit" }),
  ];
  for (const content of invalid) {
    assert.deepEqual(await review(assert.fail, { ...message, content }), {
      scope: "synthetic_input", outcome: "invalid_review",
    });
  }
  assert.deepEqual(await review(assert.fail, {
    ...message, attachments: [{ id: "private" }],
  }), { scope: "synthetic_input", outcome: "invalid_review" });
  assert.deepEqual(await review(assert.fail, message, ""), {
    scope: "synthetic_input", ...request, outcome: "receipt_unavailable",
  });
});

test("role and claimed provenance cannot replace the host thread binding", async () => {
  const handler = definitions(assert.fail).handlers.review_preparation;
  const state = { threadId, stopSession: assert.fail };
  for (const role of ["assistant", "system", "tool"]) {
    await handler.execute(state, { ...message, role });
  }
  const scopedReader = async (activeThread) => {
    if (activeThread !== threadId) throw new Error("wrong child");
    return row;
  };
  const forged = { ...message, metadata: { forwarded_from: threadId } };
  assert.deepEqual(await review(scopedReader, forged, "different-child"), {
    scope: "synthetic_input", ...request, outcome: "receipt_unavailable",
  });
});

test("a failed session-stop request surfaces the error without a local retry", async () => {
  let stops = 0;
  const state = { threadId, stopSession: async () => {
    stops += 1;
    throw new Error("fixture runtime failure");
  } };
  const handler = definitions(async () => row).handlers.review_preparation;
  await assert.rejects(handler.execute(state, message), /fixture runtime failure/);
  assert.equal(stops, 1);
});
