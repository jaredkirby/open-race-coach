import { createHash, randomUUID } from "node:crypto";
import { linkSync, lstatSync, realpathSync } from "node:fs";
import { open, rename, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { maximumProfileBytes, readProfileBytes } from "./control-profile.mjs";

const sha256 = /^[a-f0-9]{64}$/;
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const backupLimit = ((maximumProfileBytes + 2n) / 3n) * 4n + 16384n;
const recordLimit = 16384n;
const statOptions = { bigint: true };
const missing = "controller_profile_missing";
const digest = bytes => createHash("sha256").update(bytes).digest("hex");

function validPath(path) {
  return typeof path === "string" && path.length <= 1024 && isAbsolute(path);
}

function identity(stats) {
  return stats.ino > 0n ? `${stats.dev}:${stats.ino}` : null;
}

function parentIdentity(path) {
  const parent = realpathSync(dirname(path));
  const stats = lstatSync(parent, statOptions);
  const fileId = identity(stats);
  if (!stats.isDirectory() || !fileId) throw new Error("Controller profile directory cannot be identified");
  return JSON.stringify([parent, fileId]);
}

function stagePath(path, id) {
  return join(dirname(path), `.orc-profile-repair-${id}.tmp`);
}

function matches(record, path, fingerprint, parent) {
  return record?.version === 1 && record.path === path && record.fingerprint === fingerprint
    && record.parent === parent;
}

async function readRecord(path, limit) {
  const read = await readProfileBytes(path, limit);
  if (read.reason === missing) return null;
  if (read.state !== "ready") throw new Error("Cannot read controller profile repair record");
  return JSON.parse(read.contents.toString("utf8"));
}

async function writeSynced(path, bytes) {
  const file = await open(path, "wx", 0o600);
  let fileId;
  try {
    try {
      fileId = identity(await file.stat(statOptions));
      await file.writeFile(bytes);
      await file.sync();
    } finally {
      await file.close();
    }
    return fileId;
  } catch (error) {
    if (fileId && fileMatches(path, fileId)) await rm(path, { force: true }).catch(() => {});
    throw error;
  }
}

async function writeRecord(path, record) {
  const temporary = `${path}.${randomUUID()}.tmp`;
  let fileId;
  try {
    fileId = await writeSynced(temporary, `${JSON.stringify(record)}\n`);
    if (!fileId) throw new Error("Controller profile repair record could not be identified");
    await rename(temporary, path);
  } finally {
    if (fileId && fileMatches(temporary, fileId)) await rm(temporary, { force: true });
  }
}

export async function saveProfileBackup({ backupPath, controllerProfilePath: path, fingerprint }) {
  if (!validPath(path) || !validPath(backupPath) || typeof fingerprint !== "string"
    || !sha256.test(fingerprint)
    || resolve(path).toLowerCase() === resolve(backupPath).toLowerCase()) {
    throw new Error("A configured controller profile and a full fingerprint are required");
  }
  const parent = parentIdentity(path);
  const profile = await readProfileBytes(path);
  if (profile.state !== "ready" || profile.fingerprint !== fingerprint || parentIdentity(path) !== parent) {
    throw new Error("Fingerprint does not match the current controller profile");
  }
  // The backup contains independent bytes. A restored profile must never share its inode.
  await writeRecord(backupPath, {
    version: 1, path, parent, fingerprint, bytes: profile.contents.toString("base64"),
  });
  await loadBackup(backupPath, path, fingerprint, parent);
}

async function loadBackup(backupPath, path, fingerprint, parent) {
  const source = await readRecord(backupPath, backupLimit);
  if (!matches(source, path, fingerprint, parent) || typeof source.bytes !== "string") {
    throw new Error("The accepted controller profile backup is unavailable");
  }
  const bytes = Buffer.from(source.bytes, "base64");
  if (bytes.length === 0 || bytes.length > Number(maximumProfileBytes)
    || bytes.toString("base64") !== source.bytes || digest(bytes) !== fingerprint) {
    throw new Error("The accepted controller profile backup failed verification");
  }
  return bytes;
}

function outcome(status, reason, details) {
  return { status, reason: `controller_profile_${reason}`, ...details };
}

function verifiedReadback(profile, receipt) {
  if (profile.state === "ready" && profile.fingerprint === receipt.fingerprint
    && identity(profile.stats) === receipt.fileId) {
    try {
      if (parentIdentity(receipt.path) === receipt.parent) {
        return outcome("repaired", "repair_verified", { fingerprint: receipt.fingerprint, id: receipt.id });
      }
    } catch {
      // The selected directory could not be verified after the write.
    }
  }
  return outcome("failed", "repair_readback_failed");
}

function fileMatches(path, fileId) {
  try {
    const file = lstatSync(path, statOptions);
    return file.isFile() && identity(file) === fileId;
  } catch {
    return false;
  }
}

function stageMatches(receipt) {
  try {
    return parentIdentity(receipt.path) === receipt.parent
      && fileMatches(stagePath(receipt.path, receipt.id), receipt.fileId);
  } catch {
    return false;
  }
}

async function removeStage(receipt) {
  try {
    if (stageMatches(receipt)) await rm(stagePath(receipt.path, receipt.id));
  } catch {
    // A retained staging name is harmless. Never remove the configured profile during cleanup.
  }
}

export async function repairMissingProfile({
  backupPath, controllerProfilePath: path, fingerprint, checkIdle, signal, reportedRepairId,
}) {
  if (!validPath(path) || !validPath(backupPath) || typeof fingerprint !== "string"
    || !sha256.test(fingerprint)) return outcome("unavailable", "repair_no_accepted_backup");

  const observed = await readProfileBytes(path);
  if (observed.state !== "ready" && observed.reason !== missing) {
    return outcome("unavailable", "repair_target_unverified");
  }

  const journal = `${backupPath}.repair.json`;
  let parent;
  let receipt;
  try {
    parent = parentIdentity(path);
    receipt = await readRecord(journal, recordLimit);
  } catch {
    return outcome("unavailable", "repair_identity_unverified");
  }

  if (matches(receipt, path, fingerprint, parent) && typeof receipt.id === "string" && uuid.test(receipt.id)
    && typeof receipt.fileId === "string" && /^\d+:\d+$/.test(receipt.fileId)) {
    if (observed.state === "ready" && identity(observed.stats) === receipt.fileId
      && receipt.id !== reportedRepairId && stageMatches(receipt)) {
      // The other hard link pins the inode across a crash and prevents misattribution after inode reuse.
      return verifiedReadback(await readProfileBytes(path), receipt);
    }
    await removeStage(receipt);
  }
  if (observed.state === "ready") return outcome("already_present", "repair_target_present");

  let bytes;
  try {
    bytes = await loadBackup(backupPath, path, fingerprint, parent);
  } catch {
    return outcome("unavailable", "repair_backup_unverified");
  }

  const id = randomUUID();
  const staging = stagePath(path, id);
  let pending;
  let published = false;
  try {
    const fileId = await writeSynced(staging, bytes);
    if (!fileId) return outcome("failed", "repair_stage_unverified");
    pending = { version: 1, id, path, fingerprint, parent, fileId };
    const staged = await readProfileBytes(staging);
    if (staged.state !== "ready" || identity(staged.stats) !== fileId || staged.fingerprint !== fingerprint) {
      return outcome("failed", "repair_stage_unverified");
    }
    await writeRecord(journal, pending);

    let idle;
    try {
      idle = !signal?.aborted && await checkIdle();
    } catch {
      idle = false;
    }
    if (idle !== true || signal?.aborted) return outcome("deferred", "repair_activity_unconfirmed");

    const currentStage = lstatSync(staging, statOptions);
    if (parentIdentity(path) !== parent || !currentStage.isFile() || identity(currentStage) !== fileId
      || currentStage.size !== staged.stats.size || currentStage.mtimeNs !== staged.stats.mtimeNs
      || currentStage.ctimeNs !== staged.stats.ctimeNs) return outcome("failed", "repair_stage_unverified");
    try {
      // link is an atomic create-if-absent on the same volume. An existing destination always wins.
      linkSync(staging, path);
      published = true;
    } catch (error) {
      if (error.code === "EEXIST") return outcome("already_present", "repair_target_present");
      throw error;
    }
    return verifiedReadback(await readProfileBytes(path), pending);
  } catch {
    return outcome("failed", "repair_write_failed");
  } finally {
    if (pending && !published) await removeStage(pending);
  }
}
