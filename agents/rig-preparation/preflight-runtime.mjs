import { createHash, randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { createSession, defineDoc, defineDocFamily, defineEntry } from "@earendil-works/pi-durable";
import { openNodeSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/node";

import { defineRigPreparation, idlePreflightEvidence, rigInputEvidence } from "./definitions.mjs";

const registry = defineDoc({
  kind: "orc.idle-preflight.registry", version: 1, scope: "session",
  initial: () => ({ chief: null, rig: null }), checkpointWhen: () => true,
});
const reviewIndex = kind => defineDocFamily({
  kind, version: 1, scope: "conversation",
  history: "latest", fork: "initial", family: true,
  initial: () => ({ entryId: null }), checkpointWhen: () => true,
});
const idlePreflight = {
  scope: "idle_preflight",
  field: "preflight",
  evidence: idlePreflightEvidence,
  index: reviewIndex("orc.idle-preflight.reviews"),
  rigResult: defineEntry("orc.idle-preflight.rig-result.v1"),
  chiefReport: defineEntry("orc.idle-preflight.chief-report.v1"),
  request: "last_idle_preflight",
  reader: "readLastPreflight",
  maxReportLength: 1024,
};
const rigInputs = {
  scope: "rig_inputs",
  field: "inputs",
  evidence: rigInputEvidence,
  index: reviewIndex("orc.rig-inputs.reviews"),
  rigResult: defineEntry("orc.rig-inputs.rig-result.v1"),
  chiefReport: defineEntry("orc.rig-inputs.chief-report.v1"),
  request: "last_rig_inputs",
  reader: "readLastInputs",
  maxReportLength: 2048,
};

function preflightHandler(reader, readEvidence) {
  const definitions = defineRigPreparation({
    chiefModel: "unconfigured_chief",
    readReceipt: () => { throw new Error("Receipt lookup is unavailable in the idle preflight host"); },
    [reader]: readEvidence,
  });
  const { race_chief: chief, rig_engineer: rig } = definitions.agents;
  if (!isDeepStrictEqual(chief.sideA.subagents, [{
    agent: rig.name, maxInstances: 1, blocking: false, parentCommunication: "explicit",
  }]) || !isDeepStrictEqual(rig.sideA, { handler: "review_preparation" })
      || rig.primary !== false || Object.keys(definitions.tools ?? {}).length > 0) {
    throw new Error("Unsupported SAS idle preflight definition");
  }
  return definitions.handlers.review_preparation.execute;
}

// One local writer owns this database. The host delivers a fixed SAS message to
// a deterministic handler backed only by the selected, immutable observation.
export async function openPreflightRuntime(dbPath) {
  const session = createSession(await openNodeSqliteStorage(dbPath));
  try {
    const { chief, rig } = await session.commit(async tx => {
      const state = await tx.doc(registry);
      if (state.chief === null && state.rig === null) {
        const chief = await tx.createConversation({ ownership: { kind: "ownerless" } });
        const rig = await tx.createConversation({ ownership: { kind: "ownerless" } });
        state.chief = chief.id;
        state.rig = { conversationId: rig.id, threadId: randomUUID() };
      } else if (!state.rig?.threadId || state.chief === state.rig.conversationId
          || !await tx.conversation(state.chief) || !await tx.conversation(state.rig.conversationId)) {
        throw new Error("Invalid idle preflight conversation binding");
      }
      return { chief: state.chief, rig: { ...state.rig } };
    }, BACKGROUND_CONTEXT);

    async function reviewEvidence(contract, evidence) {
      const serialized = JSON.stringify(contract.evidence.parse(evidence));
      const selected = JSON.parse(serialized);
      const reviewId = createHash("sha256").update(serialized).digest("hex");
      const report = { scope: contract.scope, outcome: "observed", [contract.field]: selected };
      return session.commit(async tx => {
        const index = await tx.doc(contract.index, chief, reviewId, null);
        if (index.entryId !== null) {
          const prior = await tx.entry(contract.chiefReport, index.entryId);
          if (prior?.conversationId !== chief || prior.data.reviewId !== reviewId
              || prior.data.childId !== rig.threadId || !isDeepStrictEqual(prior.data.report, report)) {
            throw new Error("Preparation report does not match its selected observation");
          }
          return prior.data.report;
        }

        const execute = preflightHandler(contract.reader, (threadId, signal) => {
          signal.throwIfAborted();
          if (threadId !== rig.threadId) throw new Error("Unbound Rig Engineer");
          return selected;
        });
        let result;
        await execute({ threadId: rig.threadId, stopSession: async ({ result: text, notifyParent }) => {
          if (result !== undefined || typeof text !== "string"
              || text.length > contract.maxReportLength || text !== notifyParent) {
            throw new Error("Invalid preparation stop payload");
          }
          result = JSON.parse(text);
        } }, { role: "user", content: JSON.stringify({ review: contract.request }), attachments: null });
        if (!isDeepStrictEqual(result, report)) throw new Error("Invalid preparation review result");

        // Result, explicit parent report, and deduplication become visible together.
        const data = { reviewId, childId: rig.threadId, report: result };
        const closure = await tx.appendEntry(contract.rigResult, rig.conversationId, { data });
        const entry = await tx.appendEntry(contract.chiefReport, chief, {
          data: { ...data, childEntryId: closure.id },
        });
        index.entryId = entry.id;
        return result;
      }, BACKGROUND_CONTEXT);
    }

    return {
      review: cycle => reviewEvidence(idlePreflight, cycle),
      reviewInputs: evidence => reviewEvidence(rigInputs, evidence),
      close: () => session.close(BACKGROUND_CONTEXT),
    };
  } catch (error) {
    await session.close(BACKGROUND_CONTEXT);
    throw error;
  }
}
