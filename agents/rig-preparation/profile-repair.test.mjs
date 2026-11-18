import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { repairMissingProfile, saveProfileBackup } from "./profile-repair.mjs";

async function setup(t, { accept = true } = {}) {
  const directory = await mkdtemp(join(tmpdir(), "orc-profile-repair-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const controllerProfilePath = join(directory, "sim", "controls.sav");
  await mkdir(dirname(controllerProfilePath));
  const backupPath = join(directory, "accepted.json");
  const bytes = Buffer.from([9, 0, 254, 12, 192, 3]);
  const fingerprint = createHash("sha256").update(bytes).digest("hex");
  const options = { backupPath, controllerProfilePath, fingerprint };
  await writeFile(controllerProfilePath, bytes);
  if (accept) await saveProfileBackup(options);
  return {
    ...options, bytes,
    repair: overrides => repairMissingProfile({ ...options, checkIdle: () => true, ...overrides }),
  };
}

test("restores the accepted bytes and reconciles once before receipt is durably reported", async t => {
  const { controllerProfilePath: path, backupPath, bytes, fingerprint, repair } = await setup(t);
  const backupBefore = await readFile(backupPath);
  await rm(path);
  const restored = await repair();
  assert.equal(restored.status, "repaired");
  assert.equal(restored.fingerprint, fingerprint);
  assert.deepEqual(await readFile(path), bytes);
  assert.deepEqual(await repair(), restored);
  assert.equal((await repair({ reportedRepairId: restored.id })).status, "already_present");
  assert.deepEqual(await readdir(dirname(path)), ["controls.sav"]);
  await writeFile(path, "different");
  assert.deepEqual(await readFile(backupPath), backupBefore);
});

test("refuses unaccepted, corrupt or oversized backups and a different configured target", async t => {
  const rig = await setup(t, { accept: false });
  const { controllerProfilePath: path, backupPath, repair } = rig;
  await rm(path);
  assert.equal((await repair()).reason, "controller_profile_repair_backup_unverified");
  assert.equal((await repair({ fingerprint: undefined })).reason, "controller_profile_repair_no_accepted_backup");
  await writeFile(path, rig.bytes);
  await assert.rejects(saveProfileBackup({ ...rig, fingerprint: "0".repeat(64) }), /Fingerprint does not match/);
  await saveProfileBackup(rig);
  const other = join(dirname(path), "other.sav");
  assert.equal((await repair({ controllerProfilePath: other })).reason, "controller_profile_repair_backup_unverified");
  await rm(path);
  const backup = JSON.parse(await readFile(backupPath, "utf8"));
  await writeFile(backupPath, JSON.stringify({ ...backup, bytes: "AAAA" }));
  assert.equal((await repair()).reason, "controller_profile_repair_backup_unverified");
  await writeFile(backupPath, Buffer.alloc(2 * 1024 * 1024));
  assert.equal((await repair()).reason, "controller_profile_repair_backup_unverified");
  await assert.rejects(readFile(path), { code: "ENOENT" });
});

test("uses the final idle check and handles aborts without publishing", async t => {
  const { controllerProfilePath: path, repair } = await setup(t);
  await rm(path);
  assert.equal((await repair({ checkIdle: () => false })).status, "deferred");
  const controller = new AbortController();
  assert.equal((await repair({
    signal: controller.signal,
    checkIdle: () => { controller.abort(); return true; },
  })).status, "deferred");
  assert.equal((await repair({ checkIdle: () => { throw new Error("unknown activity"); } })).status, "deferred");
  assert.deepEqual(await readdir(dirname(path)), []);
});

test("preserves a profile created by the simulator after staging", async t => {
  const { controllerProfilePath: path, repair } = await setup(t);
  await rm(path);
  const result = await repair({
    checkIdle: async () => { await writeFile(path, "simulator won the race"); return true; },
  });
  assert.equal(result.status, "already_present");
  assert.equal(await readFile(path, "utf8"), "simulator won the race");
  assert.deepEqual(await readdir(dirname(path)), ["controls.sav"]);
});

test("refuses a replacement directory and does not publish altered staging bytes", async t => {
  const { controllerProfilePath: path, repair } = await setup(t);
  const directory = dirname(path);
  await rm(path);
  const modified = await repair({
    checkIdle: async () => {
      const [staging] = await readdir(directory);
      await writeFile(join(directory, staging), "altered");
      return true;
    },
  });
  assert.equal(modified.reason, "controller_profile_repair_stage_unverified");
  await rename(directory, `${directory}-original`);
  await mkdir(directory);
  assert.equal((await repair()).reason, "controller_profile_repair_backup_unverified");
  assert.deepEqual(await readdir(directory), []);
});

test("reconciliation independently checks current bytes and rejects a new file with the accepted bytes", async t => {
  const { controllerProfilePath: path, bytes, repair } = await setup(t);
  await rm(path);
  assert.equal((await repair()).status, "repaired");
  await writeFile(path, "drift on the exact restored inode");
  assert.equal((await repair()).reason, "controller_profile_repair_readback_failed");

  await rm(path);
  await writeFile(path, bytes);
  assert.equal((await repair()).status, "already_present");
  assert.deepEqual(await readdir(dirname(path)), ["controls.sav"]);
});
