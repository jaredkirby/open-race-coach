import { SPEC_VERSION, defineAgent, defineHandler, definePrompt } from "@standardagents/spec";
import { z } from "zod";

const commandId = z.string().regex(/^[a-f0-9]{32}$/).length(32);
const fingerprint = z.string().regex(/^[a-f0-9]{64}$/).length(64);
const reviewRequest = z.strictObject({ command_id: commandId, fingerprint });
const preflightRequest = z.strictObject({ review: z.literal("last_idle_preflight") });
const inputRequest = z.strictObject({ review: z.literal("last_rig_inputs") });
const requestSchema = z.union([reviewRequest, preflightRequest, inputRequest]);
const receipt = z.object({
  id: commandId,
  fingerprint,
  state: z.enum(["queued", "dispatched", "verified", "skipped", "refused",
    "cancelled", "unknown", "reconciled"]),
}).nullable();
const reasonCode = z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/).max(64);
const observation = z.object({
  state: z.enum(["ready", "blocked", "unknown"]),
  reason: reasonCode,
});
const preflightCycle = z.object({
  checkedAt: z.iso.datetime({ offset: true }).max(40),
  reason: reasonCode.optional(),
});
export const idlePreflightEvidence = z.discriminatedUnion("status", [
  preflightCycle.extend({
    status: z.literal("completed"),
    result: z.object({
      steam: observation.extend({ buildId: z.string().regex(/^[0-9]{1,32}$/).max(32).optional() }),
      moza: observation,
      controls: observation.extend({ needsDrivingCheck: z.boolean().optional() }).optional(),
      repair: z.object({ status: z.enum(["repaired", "already_present", "deferred", "unavailable", "failed"]),
        reason: reasonCode }).optional(),
    }),
  }),
  preflightCycle.extend({
    status: z.enum(["deferred", "failed"]),
    result: z.never().optional(),
  }),
]);
const preflight = idlePreflightEvidence.nullable();
const inputChannel = z.enum(["steering", "throttle", "brake"]);
const inputControl = z.object({
  in_game: z.enum(["not_observed", "observed"]),
  physical: z.enum(["not_observed", "driver_confirmed_gesture", "timing_correlated"]),
  axis: z.enum(["x", "y", "z", "r", "u", "v"]).nullable(),
  axis_source: z.enum(["wheel", "pedals"]).optional(),
  windows_response: z.enum(["observed", "ambiguous", "not_observed", "unavailable"]).optional(),
  other_game_controls: z.array(inputChannel).max(2).optional(),
});
export const rigInputEvidence = z.object({
  checkedAt: z.iso.datetime({ offset: true }).max(40),
  schema_version: z.literal(1),
  source: z.literal("ams2_shared_memory"),
  mode: z.enum(["passive", "parked"]),
  status: z.enum(["unavailable", "incomplete", "observed"]),
  reason: z.enum([
    "mapping_unavailable", "observer_unavailable", "driver_confirmation_required", "ams2_process_unconfirmed",
    "windows_required", "source_lost",
    "mapping_unreadable", "source_stalled", "view_unavailable_or_mismatched",
    "source_not_advancing", "source_or_view_changed", "control_response_incomplete", "unexpected_control_response",
    "local_player_unconfirmed", "control_responses_observed", "coherent_frame_unavailable",
    "unsupported_shared_memory_version", "not_in_live_cockpit", "invalid_control_values",
    "parked_check_requires_stationary_neutral_practice_or_test", "stationary_preparation_incomplete",
  ]),
  participant_index: z.int().min(0).max(63).nullable(),
  player_attribution: z.enum(["unconfirmed", "driver_confirmed_view"]),
  physical_attribution: z.enum(["not_observed", "driver_confirmed_gesture", "timing_correlated"]),
  controller_status: z.enum(["not_requested", "unavailable", "lost_or_changed", "sampled"]),
  frame_count: z.int().nonnegative(),
  controls: z.object({ steering: inputControl, throttle: inputControl, brake: inputControl }),
}).refine(evidence => {
  const controls = Object.values(evidence.controls);
  const correlated = controls.filter(control => control.physical === "timing_correlated");
  const hasGesture = controls.some(control => control.physical !== "not_observed");
  if (controls.some(control => control.other_game_controls?.length) &&
      (evidence.status !== "incomplete" || evidence.reason !== "unexpected_control_response")) return false;
  const physicalAttribution = correlated.length === controls.length
    ? "timing_correlated" : hasGesture ? "driver_confirmed_gesture" : "not_observed";
  if (evidence.physical_attribution !== physicalAttribution) return false;
  if (hasGesture && (evidence.mode !== "parked"
      || evidence.player_attribution !== "driver_confirmed_view")) return false;
  if (correlated.length > 0 && (evidence.controller_status !== "sampled"
      || new Set(correlated.map(control => (control.axis_source ?? "shared") + ":" + control.axis)).size !== correlated.length)) return false;
  const split = correlated.some(control => control.axis_source !== undefined);
  if (split && correlated.some(control => control.axis_source === undefined)) return false;
  if (Object.entries(evidence.controls).some(([name, control]) => control.axis_source !== undefined &&
      (control.physical !== "timing_correlated" || control.axis_source !== (name === "steering" ? "wheel" : "pedals")))) return false;
  if (evidence.status === "observed" && (evidence.participant_index === null
      || evidence.player_attribution !== "driver_confirmed_view"
      || controls.some(control => control.in_game !== "observed"))) return false;
  return Object.entries(evidence.controls).every(([channel, control]) => {
    const others = control.other_game_controls ?? [];
    const timingCorrelated = control.physical === "timing_correlated";
    return (control.axis !== null) === timingCorrelated
      && (control.physical === "not_observed" || control.in_game === "observed")
      && ((control.windows_response === undefined && control.other_game_controls === undefined)
        || evidence.mode === "parked")
      && (!timingCorrelated || control.windows_response === undefined
        || control.windows_response === "observed")
      && !others.includes(channel) && new Set(others).size === others.length;
  });
}, "Inconsistent rig input attribution");
const rigInputs = rigInputEvidence.nullable();
const evidenceReadTimeoutMs = 2000;

async function readWithinDeadline(read) {
  const controller = new AbortController();
  let timer;
  try {
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        reject(new Error("Preparation evidence read timed out"));
        controller.abort();
      }, evidenceReadTimeoutMs);
    });
    const lookup = Promise.resolve().then(() => read(controller.signal));
    return await Promise.race([lookup, deadline]);
  } finally {
    clearTimeout(timer);
  }
}

function parseRequest(message) {
  if (typeof message.content !== "string" || message.content.length > 256
    || message.attachments?.length || message.tool_calls) {
    return null;
  }
  try {
    const parsed = requestSchema.safeParse(JSON.parse(message.content));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

// The trusted host scopes evidence readers to the child, and the receipt reader
// to the selected command. Readers should cancel outstanding I/O on abort.
// The handler receives neither a gateway credential nor a mutation operation.
export function defineRigPreparation({ chiefModel, readReceipt, readLastPreflight, readLastInputs }) {
  if (SPEC_VERSION !== "0.2.0" || typeof readReceipt !== "function") {
    throw new Error("Rig preparation requires SAS 0.2.0 and a scoped receipt reader");
  }
  if (readLastPreflight !== undefined && typeof readLastPreflight !== "function") {
    throw new Error("Rig preparation requires a scoped preflight reader when configured");
  }
  if (readLastInputs !== undefined && typeof readLastInputs !== "function") {
    throw new Error("Rig preparation requires a scoped rig input reader when configured");
  }

  const chief = definePrompt({
    name: "race_chief",
    model: chiefModel,
    toolDescription: "Coordinate simulator preparation and report its evidence.",
    prompt: "You are the Race Chief. Protect the driver's time. Reuse the Rig Engineer "
      + "child thread and use subagent_message. For a receipt review, send only a JSON object "
      + "with command_id and fingerprint supplied by the trusted host for the selected attempt. "
      + "Match both fields to the outstanding review before using the report. "
      + (readLastPreflight
        ? 'For the last recorded idle preflight, send only {"review":"last_idle_preflight"}. '
          + "A recorded result describes only that check at its recorded time. "
        : "")
      + (readLastInputs
        ? 'For the last recorded rig input observation, send only {"review":"last_rig_inputs"}. '
          + "Respect the recorded mode, control coverage and attribution. "
        : "")
      + "The local coordinator "
      + "owns all input authority. Report simulator readiness only from independent, current rig evidence.",
  });

  return {
    agents: {
      race_chief: defineAgent({
        name: "race_chief",
        sideA: {
          prompt: chief.name,
          subagents: [{
            agent: "rig_engineer",
            maxInstances: 1,
            blocking: false,
            parentCommunication: "explicit",
          }],
        },
        sideB: { human: true },
      }),
      rig_engineer: defineAgent({
        name: "rig_engineer",
        primary: false,
        exposeAsTool: true,
        toolDescription: "Review scoped preparation evidence for the Race Chief.",
        sideA: { handler: "review_preparation" },
        sideB: { human: true },
      }),
    },
    prompts: { race_chief: chief },
    handlers: {
      review_preparation: defineHandler({
        description: "Read a scoped preparation record and explicitly return it to the Chief.",
        execute: async (state, message) => {
          if (message.role !== "user") return;
          const request = parseRequest(message);
          if (request && "review" in request) {
            const [scope, field, schema, read] = request.review === "last_rig_inputs"
              ? ["rig_inputs", "inputs", rigInputs, readLastInputs]
              : ["idle_preflight", "preflight", preflight, readLastPreflight];
            const report = { scope, outcome: field + "_unavailable" };
            try {
              const threadId = state.threadId;
              if (read && typeof threadId === "string" && threadId.length > 0) {
                const observed = schema.parse(
                  await readWithinDeadline(signal => read(threadId, signal)),
                );
                report[field] = observed;
                report.outcome = observed === null ? "missing" : "observed";
              }
            } catch {
              // Preserve the bounded unavailable result without exposing transport details.
            }
            const result = JSON.stringify(report);
            await state.stopSession({ result, notifyParent: result });
            return;
          }
          const report = { scope: "synthetic_input", outcome: "invalid_review" };
          if (request) {
            Object.assign(report, request, { outcome: "receipt_unavailable" });
            try {
              const threadId = state.threadId;
              if (typeof threadId === "string" && threadId.length > 0) {
                const observed = receipt.parse(
                  await readWithinDeadline(signal => readReceipt(threadId, request.command_id, signal)),
                );
                if (observed === null || (observed.id === request.command_id
                  && observed.fingerprint === request.fingerprint)) {
                  report.receipt = observed;
                  report.outcome = observed === null ? "missing" : "observed";
                }
              }
            } catch {
              // Keep transport details private and leave the outcome unavailable.
            }
          }
          const result = JSON.stringify(report);
          await state.stopSession({ result, notifyParent: result });
        },
      }),
    },
  };
}
