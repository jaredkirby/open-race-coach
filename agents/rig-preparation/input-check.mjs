import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { rigInputEvidence } from "./definitions.mjs";
import { readControlProfile } from "./control-profile.mjs";
import { readWindowsAms2Owner } from "./preflight-probes.mjs";

const project = fileURLToPath(new URL("../../", import.meta.url));
const python = join(project, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

export async function invokeInputHelper({ parked = false, list = false, controllerId, pedalControllerId, controllerName, signal, textOnly,
  prompt = text => process.stderr.write(text) } = {}) {
  const args = ["-E", "-s", "-B", "-m", "scripts.verify_rig_inputs"];
  if (list) args.push("--list-controllers");
  if (parked) args.push("--mode", "parked", "--confirm-player-view", "--confirm-physical-controls");
  if (controllerId !== undefined) args.push("--controller-id", String(controllerId));
  else if (parked && controllerName) args.push("--controller-name", controllerName);
  if (pedalControllerId !== undefined) args.push("--pedal-controller-id", String(pedalControllerId));
  if (textOnly) args.push("--text-only");
  return new Promise((resolve, reject) => {
    const child = execFile(python, args, {
      cwd: project, timeout: 45_000, windowsHide: true, signal, maxBuffer: 16384,
    }, (error, stdout) => {
      if (error) return reject(new Error("The AMS2 input observer could not run."));
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error("Invalid AMS2 input observer output.")); }
    });
    if (parked) child.stderr.on("data", data => prompt(String(data)));
  });
}

export async function readInputHistory(directory) {
  try {
    return JSON.parse(await readFile(join(directory, "input-check.json"), "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

export async function checkInputs({ directory, controllerProfilePath, parked, controllerId, pedalControllerId, controllerName, textOnly,
  signal, run = invokeInputHelper, readOwner = readWindowsAms2Owner, runtime }) {
  const before = await readControlProfile(controllerProfilePath);
  const owner = await readOwner(signal);
  let raw;
  let unavailable = "ams2_process_unconfirmed";
  try {
    if (owner) {
      raw = await run({ parked, controllerId, pedalControllerId, controllerName, textOnly, signal });
      if (await readOwner(signal) !== owner) raw = null;
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    unavailable = "observer_unavailable";
  }
  signal?.throwIfAborted();
  if (!raw) {
    raw = { schema_version: 1, source: "ams2_shared_memory", mode: parked ? "parked" : "passive",
      status: "unavailable", reason: unavailable, participant_index: null,
      player_attribution: "unconfirmed", physical_attribution: "not_observed", frame_count: 0,
      controller_status: controllerId === undefined && pedalControllerId === undefined && !controllerName ? "not_requested" : "unavailable",
      controls: Object.fromEntries(["steering", "throttle", "brake"].map(name =>
        [name, { in_game: "not_observed", physical: "not_observed", axis: null }])) };
  }
  const after = await readControlProfile(controllerProfilePath);
  const evidence = rigInputEvidence.parse({ ...raw, checkedAt: new Date().toISOString() });
  if (evidence.mode !== (parked ? "parked" : "passive")) throw new Error("Input check mode mismatch");
  const fingerprint = before.state === "ready" && after.fingerprint === before.fingerprint
    ? before.fingerprint : null;
  const entry = { evidence, fingerprint };
  const previous = await readInputHistory(directory);
  const verified = parked && evidence.status === "observed" && evidence.player_attribution === "driver_confirmed_view"
    && ["driver_confirmed_gesture", "timing_correlated"].includes(evidence.physical_attribution)
    && Object.values(evidence.controls).every(channel => channel.in_game === "observed" && !channel.other_game_controls?.length);
  const history = { last: entry, lastDriverCheck: verified ? entry : previous?.lastDriverCheck ?? null };
  const destination = join(directory, "input-check.json");
  const temporary = destination + "." + randomUUID() + ".tmp";
  try {
    await writeFile(temporary, JSON.stringify(history) + "\n", { flag: "wx", mode: 0o600 });
    await rename(temporary, destination);
  } finally { await rm(temporary, { force: true }); }
  const report = await runtime.reviewInputs(evidence);
  return { report, verified, fingerprint };
}

export function formatInputCheck(entry) {
  const e = entry.evidence;
  const header = `AMS2 ${e.mode} check at ${e.checkedAt}`;
  if (e.reason === "ams2_process_unconfirmed") return header + " — waiting for a single identifiable AMS2 session.";
  if (e.reason === "mapping_unavailable") return header + " — waiting for AMS2 shared memory.";
  if (e.status === "unavailable") return header + " — unable to check (" + e.reason + ").";
  const channels = Object.entries(e.controls).map(([name, value]) => {
    if (value.other_game_controls?.length) {
      return `${name}: AMS2 also showed ${value.other_game_controls.join(" and ")} during this step; check assignments`;
    }
    if (value.physical === "timing_correlated") {
      return `${name}: Windows ${value.axis_source ? value.axis_source + " " : ""}axis ${value.axis} and AMS2 responded together`;
    }
    if (value.in_game === "observed") return `${name}: AMS2 responded`;
    if (value.windows_response === "observed") return `${name}: Windows saw movement, AMS2 did not show the requested response; check the in-game binding`;
    return `${name}: response unconfirmed`;
  });
  if (e.player_attribution === "unconfirmed") channels.push("Viewed car only; local driver identity unconfirmed.");
  else if (e.mode === "parked" && e.physical_attribution !== "timing_correlated") {
    channels.push(`Driver-guided check. Windows correlation: ${e.controller_status}.`);
  }
  return [header, ...channels].join("\n");
}
