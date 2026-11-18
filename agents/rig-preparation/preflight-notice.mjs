import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile, rename, rm, writeFile } from "node:fs/promises";
import { win32 } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const noticeScript = fileURLToPath(new URL("./windows-preflight-notice.ps1", import.meta.url));
const issueText = {
  steam: "Steam reported AMS2 install or update work.",
  moza: "Windows reported a problem with an expected MOZA device.",
  controlsMissing: "The configured controller profile file is missing.",
  controlsDrift: "The controller profile changed. A driving check is needed.",
};

function issueObservations({ steam, moza, controls }) {
  return {
    steam: steam?.state === "blocked" ? true : steam?.state === "ready" ? false : null,
    moza: moza?.state === "blocked" && moza.reason === "device_problem" ? true
      : moza?.state === "ready" ? false : null,
    controlsMissing: controls?.state === "blocked" && controls.reason === "controller_profile_missing" ? true
      : controls?.state === "ready" ? false : null,
    controlsDrift: controls?.state === "ready" && typeof controls.needsDrivingCheck === "boolean"
      ? controls.needsDrivingCheck : null,
  };
}

async function readNoticeState(statePath) {
  let source;
  try {
    source = await readFile(statePath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
  const { version, latched } = JSON.parse(source);
  if (version !== 1 || !Array.isArray(latched) || latched.length > Object.keys(issueText).length
    || latched.some((key) => !Object.hasOwn(issueText, key)) || new Set(latched).size !== latched.length) {
    throw new Error("Invalid preflight notice state");
  }
  return latched;
}

async function writeNoticeState(statePath, latched) {
  const temporary = `${statePath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, `${JSON.stringify({ version: 1, latched })}\n`, { flag: "wx", mode: 0o600 });
    await rename(temporary, statePath);
  } finally {
    await rm(temporary, { force: true });
  }
}

async function showWindowsPreflightNotice({ body, signal }) {
  if (signal?.aborted || process.platform !== "win32" || !process.env.SystemRoot) return "unavailable";
  const powershell = win32.join(process.env.SystemRoot,
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  try {
    const { stdout } = await execute(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", noticeScript], {
      windowsHide: true,
      timeout: 5_000,
      maxBuffer: 4096,
      signal,
      env: { ...process.env, ORC_PREFLIGHT_NOTICE_BODY: body },
    });
    return stdout.trim();
  } catch (error) {
    // A helper that started but stopped responding may have reached Show().
    return error.code === "ENOENT" || error.code === "EACCES" ? "unavailable" : "ambiguous";
  }
}

// This state belongs to the monitor's single writer and is updated after the
// checked cycle is durable. A crash or ambiguous submit keeps the latch since
// Windows may already have received the toast. Confirmed non-submission retries.
export async function publishPreflightNotice({ statePath, cycle, signal, notify = showWindowsPreflightNotice }) {
  if (cycle?.status !== "completed" || cycle.activity?.state !== "idle" || !cycle.result || signal?.aborted) {
    return { state: "skipped" };
  }
  const observations = issueObservations(cycle.result);
  const previous = await readNoticeState(statePath);
  const unresolved = previous.filter((key) => observations[key] !== false);
  const newlyObserved = Object.keys(observations).filter((key) => observations[key] === true && !previous.includes(key));
  if (unresolved.length !== previous.length || newlyObserved.length > 0) {
    await writeNoticeState(statePath, [...unresolved, ...newlyObserved]);
  }
  if (newlyObserved.length === 0) return { state: "unchanged" };

  let submitted;
  try {
    submitted = signal?.aborted ? "unavailable" : await notify({
      body: newlyObserved.map((key) => issueText[key]).join(" "), signal,
    });
  } catch {
    submitted = "ambiguous";
  }
  if (submitted === "unavailable") {
    await writeNoticeState(statePath, unresolved);
    return { state: "unavailable" };
  }
  return { state: submitted === "submitted" ? "submitted" : "ambiguous" };
}
