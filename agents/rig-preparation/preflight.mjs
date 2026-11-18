import { createHash } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { setTimeout as wait } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";

import { readControlProfile } from "./control-profile.mjs";
import { acceptControlProfile, disableProfileRepair, readIdleMonitorState, runIdleMonitor } from "./idle-monitor.mjs";
import { checkInputs, formatInputCheck, invokeInputHelper, readInputHistory } from "./input-check.mjs";
import { repairMissingProfile, saveProfileBackup } from "./profile-repair.mjs";
import { collectWindowsPreflight, readWindowsActivity } from "./preflight-probes.mjs";
import { openPreflightRuntime } from "./preflight-runtime.mjs";
import { publishPreflightNotice } from "./preflight-notice.mjs";

const defaultDirectory = fileURLToPath(new URL("../../data/rig-preparation/", import.meta.url));
const localPath = z.string().min(1).max(1024).refine(isAbsolute, "Expected an absolute path");
const profileSchema = z.strictObject({
  steamManifestPath: localPath.optional(),
  controllerProfilePath: localPath.optional(),
  expectedMozaDevices: z.array(z.strictObject({
    label: z.string().trim().min(1).max(100),
    instanceId: z.string().min(1).max(200),
  })).max(16).default([]),
  minimumIdleSeconds: z.number().int().min(1).max(86400).default(300),
});

async function loadProfile(file, optional) {
  try {
    const source = await readFile(file, "utf8");
    return profileSchema.parse(JSON.parse(source.replace(/^\uFEFF/, "")));
  } catch (error) {
    if (optional && error.code === "ENOENT") return profileSchema.parse({});
    throw new Error("Cannot read the private rig profile. Check the file path and profile fields.");
  }
}

function profileKey(profile) {
  // Paths and exact device identities determine what prior observations can be compared.
  const devices = profile.expectedMozaDevices.map(d => d.instanceId.toUpperCase()).sort();
  const selection = [profile.steamManifestPath ?? null, profile.controllerProfilePath ?? null, devices];
  return createHash("sha256").update(JSON.stringify(selection)).digest("hex");
}

const messages = {
  installed_without_pending_work: "Steam records AMS2 installed with no pending work.",
  update_or_install_pending: "Steam records unfinished AMS2 work. Check Downloads before the next session.",
  expected_devices_present: "Windows sees the configured MOZA USB equipment.",
  awaiting_connection: "Waiting for the configured MOZA USB equipment. Check power and USB when you return.",
  device_problem: "Windows reports a problem with the configured MOZA device. Check its status in Device Manager.",
  expected_devices_unconfigured: "MOZA USB monitoring needs a configured device identity.",
  pnp_unavailable: "Windows device status could not be read.",
  controller_profile_fingerprinted: "The saved AMS2 controller profile was readable.",
  controller_profile_missing: "The configured AMS2 controller profile is missing. Check which profile the sim loads.",
  controller_profile_not_configured: "Controller profile monitoring needs a configured file.",
};

export function formatStatus(state, inputs) {
  const inputLines = inputs?.last ? [formatInputCheck(inputs.last)] : [];
  if (inputs?.lastDriverCheck && inputs.lastDriverCheck.evidence.checkedAt !== inputs.last.evidence.checkedAt) {
    inputLines.push("Last driver-confirmed check: " + formatInputCheck(inputs.lastDriverCheck));
  }
  if (!state) return ["No idle preflight has run for this rig profile.", ...inputLines].join("\n");
  const latest = state.lastCycle;
  const lines = [`Latest attempt ${latest.checkedAt} — ${latest.status}${latest.reason ? ` (${latest.reason})` : ""}.`];
  const completed = state.lastCompleted;
  if (!completed) return [...lines, "Waiting for the first completed idle check.", ...inputLines].join("\n");
  lines.push(`Last completed checks at ${completed.checkedAt}`);
  for (const key of ["steam", "moza", "controls"]) {
    const check = completed.result[key];
    if (check) lines.push(messages[check.reason] ?? `${key}: ${check.state} (${check.reason})`);
  }
  if (state.needsDrivingCheck) {
    lines.push("The AMS2 controls file changed. Verify steering and pedal response on your next normal drive.");
    if (completed.result.controls?.fingerprint) {
      lines.push(`After verification, accept this fingerprint: ${completed.result.controls.fingerprint}`);
    }
  }
  for (const change of completed.changes ?? []) {
    if (change.item === "steam.buildId") lines.push(`AMS2 build changed from ${change.previous} to ${change.current}.`);
  }
  if (state.repairFingerprint) lines.push("Missing-profile repair is armed for the accepted backup.");
  if (state.lastRepair) lines.push(`Last verified missing-profile repair: ${state.lastRepair.checkedAt}.`);
  if (completed.result.repair && ["unavailable", "failed", "deferred"].includes(completed.result.repair.status)) {
    lines.push(`Profile repair: ${completed.result.repair.reason}.`);
  }
  return [...lines, ...inputLines].join("\n");
}

const usage = `node preflight.mjs once|watch|status [--profile FILE] [--data-dir DIR] [--json]
node preflight.mjs check-inputs [--parked-driver [--controller-id SLOT] [--pedal-controller-id SLOT] [--text-only] [--enable-repair]]
node preflight.mjs list-controllers
node preflight.mjs accept-controls --fingerprint SHA256 [--enable-repair]
node preflight.mjs disable-repair

once checks if the Windows desktop and simulators are idle. watch repeats every 15 minutes.
status shows the most recent attempt and the time of the last completed checks.
--parked-driver confirms you are safely stationary in neutral, in your own live practice/test cockpit
with AI off, and will follow the wheel, throttle and brake prompts.
--enable-repair saves the verified bindings and permits restoring that file if it goes missing.`;

// The SQLite transaction excludes CLI writers across processes and is released by the OS on exit.
// The JSON observations and the Pi report each have their own commit and recovery boundary.
function claimWriter(directory) {
  const db = new DatabaseSync(join(directory, "writer.sqlite"));
  try {
    db.exec("BEGIN IMMEDIATE");
    return db;
  } catch (error) {
    db.close();
    if (error.errcode === 5) throw new Error("Another Rig Engineer is checking this rig.");
    throw error;
  }
}

export async function runCommand(args, {
  readActivity = readWindowsActivity, collect = collectWindowsPreflight,
  publish = publishPreflightNotice, readInputs = invokeInputHelper, readOwner, print = console.log, signal,
} = {}) {
  const { values, positionals } = parseArgs({ args, allowPositionals: true, options: {
    profile: { type: "string" }, "data-dir": { type: "string" },
    fingerprint: { type: "string" }, json: { type: "boolean" }, help: { type: "boolean" },
    "parked-driver": { type: "boolean" }, "enable-repair": { type: "boolean" },
    "controller-id": { type: "string" }, "pedal-controller-id": { type: "string" }, "text-only": { type: "boolean" },
  } });
  if (values.help) { print(usage); return; }
  const [command] = positionals;
  const parked = !!values["parked-driver"];
  const enroll = !!values["enable-repair"];
  const id = values["controller-id"];
  const pedalId = values["pedal-controller-id"];
  if (positionals.length !== 1 || !["once", "watch", "status", "accept-controls", "disable-repair",
    "check-inputs", "list-controllers"].includes(command)
      || (command === "accept-controls") !== (values.fingerprint !== undefined)
      || (parked && command !== "check-inputs") || (values["text-only"] && !parked)
      || (enroll && !parked && command !== "accept-controls")
      || [id, pedalId].some(slot => slot !== undefined && (!parked || !/^(?:[0-9]|1[0-5])$/.test(slot)))) throw new Error(usage);
  if (command === "list-controllers") { print(JSON.stringify(await readInputs({ list: true, signal }))); return; }
  if (command === "watch") {
    // Release the writer between ticks so on-demand driver checks can use it.
    const once = [...args];
    once[args.indexOf("watch")] = "once";
    while (!signal?.aborted) {
      await runCommand(once, { readActivity, collect, publish, readInputs, readOwner, print, signal });
      try { await wait(900_000, undefined, { signal }); } catch (error) { if (!signal?.aborted) throw error; }
    }
    return;
  }
  const directory = resolve(values["data-dir"] ?? defaultDirectory);
  const profile = await loadProfile(values.profile ?? join(directory, "profile.json"), !values.profile);
  const selected = join(directory, profileKey(profile));
  const statePath = join(selected, "idle-state.json");
  if (command === "status") {
    const state = await readIdleMonitorState(statePath);
    const inputs = await readInputHistory(selected);
    print(values.json ? JSON.stringify(inputs ? { ...state, inputs } : state) : formatStatus(state, inputs));
    return;
  }
  await mkdir(selected, { recursive: true });
  const writer = claimWriter(directory);
  const backupPath = fingerprint => join(selected, `accepted-${fingerprint}.json`);
  const accept = (fingerprint, liveVerifiedFingerprint) => acceptControlProfile({
    statePath, controllerProfilePath: profile.controllerProfilePath, fingerprint, liveVerifiedFingerprint,
    saveBackup: enroll ? () => saveProfileBackup({ backupPath: backupPath(fingerprint),
      controllerProfilePath: profile.controllerProfilePath, fingerprint }) : undefined,
  });
  try {
    if (command === "disable-repair") {
      await disableProfileRepair(statePath);
      print("Automatic missing-profile repair is off.");
      return;
    }
    if (command === "accept-controls") {
      await accept(values.fingerprint);
      print(enroll ? "Saved the verified profile and armed missing-file repair." : "Recorded the verified AMS2 controller profile.");
      return;
    }
    const runtime = await openPreflightRuntime(join(selected, "rig-engineer.sqlite"));
    try {
      const previousInput = await readInputHistory(selected);
      if (previousInput) await runtime.reviewInputs(previousInput.last.evidence);
      const observeInputs = options => checkInputs({ directory: selected, controllerProfilePath: profile.controllerProfilePath,
        run: readInputs, readOwner, runtime, signal, ...options });
      if (command === "check-inputs") {
        const check = await observeInputs({ parked, controllerId: id === undefined ? undefined : Number(id),
          pedalControllerId: pedalId === undefined ? undefined : Number(pedalId),
          controllerName: parked && id === undefined && profile.expectedMozaDevices.length === 1
            ? profile.expectedMozaDevices[0].label : undefined,
          textOnly: values["text-only"] });
        let armed = false;
        if (enroll && check.verified && check.fingerprint) {
          await accept(check.fingerprint, check.fingerprint);
          armed = true;
        }
        print(values.json ? JSON.stringify({ report: check.report, armed })
          : formatInputCheck({ evidence: check.report.inputs }) +
            (enroll ? (armed ? "\nVerified backup saved. Missing-file repair is armed." : "\nNo new repair backup was enrolled.") : ""));
        return;
      }
      const noticePath = join(selected, "preflight-notices.json");
      const previous = await readIdleMonitorState(statePath);
      if (previous) await runtime.review(previous.lastCycle);
      await runIdleMonitor({
        statePath, readActivity, signal,
        minimumIdleSeconds: profile.minimumIdleSeconds,
        collect: async ({ signal: checkSignal }) => {
          const [rig, controls] = await Promise.all([
            collect({ ...profile, signal: checkSignal }), readControlProfile(profile.controllerProfilePath),
          ]);
          const fingerprint = previous?.repairFingerprint;
          if (!/^[a-f0-9]{64}$/.test(fingerprint ?? "")) return { ...rig, controls };
          const repair = await repairMissingProfile({
            backupPath: backupPath(fingerprint), controllerProfilePath: profile.controllerProfilePath,
            fingerprint, signal: checkSignal, reportedRepairId: previous.lastRepair?.id,
            checkIdle: async () => (await readActivity({ minimumIdleSeconds: profile.minimumIdleSeconds,
              signal: checkSignal })).state === "idle",
          });
          return { ...rig, controls: await readControlProfile(profile.controllerProfilePath), repair };
        },
        onCycle: async cycle => {
          const report = await runtime.review(cycle);
          const notice = await publish({ statePath: noticePath, cycle, signal });
          const inputs = cycle.status === "deferred" && cycle.reason === "ams2_running" && !signal?.aborted
            ? await observeInputs({ parked: false }) : null;
          if (values.json) print(JSON.stringify({ report, notice, ...(inputs && { inputs: inputs.report }) }));
          else {
            print(formatStatus(await readIdleMonitorState(statePath), await readInputHistory(selected)));
            if (notice.state === "unavailable") print("Windows notification submission was unavailable.");
          }
        },
      });
    } finally { await runtime.close(); }
  } finally { writer.close(); }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const controller = new AbortController();
  process.once("SIGINT", () => controller.abort());
  process.once("SIGTERM", () => controller.abort());
  runCommand(process.argv.slice(2), { signal: controller.signal }).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
