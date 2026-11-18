import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { setTimeout as wait } from "node:timers/promises";
import { readControlProfile } from "./control-profile.mjs";

const schemaVersion = 1;
const reasonCode = /^[a-z][a-z0-9_-]{0,63}$/;
const steamBuildId = /^[0-9]{1,32}$/;
const controlFingerprint = /^[a-f0-9]{64}$/;
const controlBaselineKey = "controls.fingerprint";

export async function readIdleMonitorState(statePath) {
  let source;
  try {
    source = await readFile(statePath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  const state = JSON.parse(source);
  if (state?.schemaVersion !== schemaVersion || !state.lastCycle
    || !state.baseline || typeof state.baseline !== "object" || Array.isArray(state.baseline)) {
    throw new Error("Invalid idle monitor state");
  }
  return state;
}

async function writeState(statePath, state) {
  await mkdir(dirname(statePath), { recursive: true });
  const temporary = `${statePath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { flag: "wx", mode: 0o600 });
    await rename(temporary, statePath);
  } finally {
    await rm(temporary, { force: true });
  }
}

export async function disableProfileRepair(statePath) {
  const state = await readIdleMonitorState(statePath);
  if (state?.repairFingerprint) await writeState(statePath, { ...state, repairFingerprint: undefined });
}

export async function acceptControlProfile({ statePath, controllerProfilePath, fingerprint, saveBackup,
  liveVerifiedFingerprint }) {
  if (typeof fingerprint !== "string" || !controlFingerprint.test(fingerprint)) {
    throw new Error("A full controller profile fingerprint is required");
  }
  const state = await readIdleMonitorState(statePath);
  const lastObserved = state?.lastCompleted?.result?.controls;
  if (!state || (liveVerifiedFingerprint !== fingerprint &&
      (lastObserved?.state !== "ready" || lastObserved.fingerprint !== fingerprint))) {
    throw new Error("Fingerprint does not match the last completed idle preflight");
  }
  const current = await readControlProfile(controllerProfilePath);
  if (current.state !== "ready" || current.fingerprint !== fingerprint) {
    throw new Error("Fingerprint does not match the current controller profile");
  }

  if (saveBackup) await saveBackup();
  await writeState(statePath, {
    ...state,
    baseline: { ...state.baseline, [controlBaselineKey]: fingerprint },
    needsDrivingCheck: false,
    repairFingerprint: saveBackup || state.repairFingerprint === fingerprint ? fingerprint : undefined,
  });
  return { fingerprint, needsDrivingCheck: false };
}

function knownObservations(result) {
  const observations = {};
  for (const item of ["steam", "moza"]) {
    const status = result[item]?.state;
    if (status === "ready" || status === "blocked") observations[`${item}.state`] = status;
  }
  // Steam can report a last installed build while an update is pending.
  if (result.steam.buildId !== undefined) {
    observations["steam.buildId"] = result.steam.buildId;
  }
  return observations;
}

async function observe(readActivity, options) {
  try {
    const activity = await readActivity(options);
    if (["idle", "active", "unknown"].includes(activity?.state)
      && typeof activity.reason === "string" && reasonCode.test(activity.reason)) return activity;
  } catch {
    // A failed observation cannot admit a run.
  }
  return { state: "unknown", reason: "activity_observation_failed" };
}

function validObservation(observation) {
  return ["ready", "blocked", "unknown"].includes(observation?.state)
    && typeof observation.reason === "string" && reasonCode.test(observation.reason);
}

function validControlProfile(controls) {
  if (controls === undefined) return true;
  if (!validObservation(controls)) return false;
  if (controls.state !== "ready") return controls.fingerprint === undefined;
  return typeof controls.fingerprint === "string" && controlFingerprint.test(controls.fingerprint);
}

async function checkOnce({ statePath, readActivity, collect, minimumIdleSeconds, signal }) {
  let result;
  let status = "deferred";
  let reason;
  const activityOptions = { minimumIdleSeconds, signal };
  let activity = await observe(readActivity, activityOptions);
  if (signal?.aborted) return null;
  if (activity.state === "idle") {
    try {
      result = await collect({ signal });
      if (!validObservation(result?.steam) || !validObservation(result?.moza)
        || !validControlProfile(result.controls)
        || (result.steam.buildId !== undefined
          && (typeof result.steam.buildId !== "string" || !steamBuildId.test(result.steam.buildId)))) {
        throw new Error("Invalid preflight result");
      }
      if (signal?.aborted) return null;
      activity = await observe(readActivity, activityOptions);
      if (activity.state === "idle") status = "completed";
      else reason = activity.reason;
    } catch {
      status = "failed";
      reason = "preflight_collection_failed";
    }
  } else {
    reason = activity.reason;
  }
  if (signal?.aborted) return null;

  const state = await readIdleMonitorState(statePath);
  const previous = state?.baseline ?? {};
  const current = status === "completed" ? knownObservations(result) : {};
  const changes = Object.entries(current)
    .filter(([item, value]) => Object.hasOwn(previous, item) && previous[item] !== value)
    .map(([item, value]) => ({ item, previous: previous[item], current: value }));
  const baseline = { ...previous, ...current };
  let needsDrivingCheck = state?.needsDrivingCheck;
  const cycle = { checkedAt: new Date().toISOString(), status, activity, changes };
  if (status === "completed") {
    const controls = result.controls;
    if (controls?.state === "ready") {
      baseline[controlBaselineKey] ??= controls.fingerprint;
      needsDrivingCheck = controls.fingerprint !== baseline[controlBaselineKey];
      if (needsDrivingCheck) {
        changes.push({
          item: controlBaselineKey, previous: baseline[controlBaselineKey], current: controls.fingerprint,
        });
      }
    }
    cycle.result = result;
    if (controls && needsDrivingCheck !== undefined) {
      cycle.result = { ...result, controls: { ...controls, needsDrivingCheck } };
    }
  }
  if (reason !== undefined) cycle.reason = reason;
  await writeState(statePath, {
    schemaVersion,
    baseline,
    lastCycle: cycle,
    lastCompleted: status === "completed" ? cycle : (state?.lastCompleted ?? null),
    needsDrivingCheck,
    repairFingerprint: state?.repairFingerprint,
    lastRepair: cycle.result?.repair?.status === "repaired"
      ? { checkedAt: cycle.checkedAt, ...cycle.result.repair } : state?.lastRepair,
  });
  return cycle;
}

export async function runIdleMonitor({
  statePath, readActivity, collect, watch = false, intervalMs = 900_000,
  minimumIdleSeconds = 300, signal, onCycle,
}) {
  if (typeof statePath !== "string" || !statePath || typeof readActivity !== "function"
    || typeof collect !== "function") {
    throw new TypeError("Idle monitor requires a state path, activity reader and preflight collector");
  }
  if (!Number.isSafeInteger(intervalMs) || intervalMs < 1 || intervalMs > 2_147_483_647
    || !Number.isSafeInteger(minimumIdleSeconds) || minimumIdleSeconds < 1) {
    throw new RangeError("Invalid idle monitor interval or minimum idle time");
  }

  let lastCycle = null;
  while (!signal?.aborted) {
    const cycle = await checkOnce({ statePath, readActivity, collect, minimumIdleSeconds, signal });
    if (cycle === null) break;
    lastCycle = cycle;
    await onCycle?.(cycle);
    if (!watch || signal?.aborted) break;
    try {
      await wait(intervalMs, undefined, { signal });
    } catch (error) {
      if (signal?.aborted && error.name === "AbortError") break;
      throw error;
    }
  }
  return lastCycle;
}
