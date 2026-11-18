import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { readControlProfile } from "./control-profile.mjs";

async function temporaryDirectory(t) {
  const directory = await mkdtemp(join(tmpdir(), "orc-control-profile-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

test("fingerprints bytes in a configured ordinary file", async (t) => {
  const path = join(await temporaryDirectory(t), "bindings.sav");
  const bytes = Buffer.from([0, 1, 255]);
  await writeFile(path, bytes);
  assert.deepEqual(await readControlProfile(path), {
    state: "ready", reason: "controller_profile_fingerprinted",
    fingerprint: createHash("sha256").update(bytes).digest("hex"),
  });
  await writeFile(path, Buffer.from([0, 2, 255]));
  assert.notEqual((await readControlProfile(path)).fingerprint, createHash("sha256").update(bytes).digest("hex"));
});

test("distinguishes unconfigured, missing and unsupported files", async (t) => {
  const directory = await temporaryDirectory(t);
  const path = join(directory, "bindings.sav");
  assert.deepEqual(await readControlProfile(), { state: "unknown", reason: "controller_profile_not_configured" });
  assert.deepEqual(await readControlProfile(path), { state: "blocked", reason: "controller_profile_missing" });
  const unsupported = { state: "unknown", reason: "controller_profile_unsupported_file" };
  assert.deepEqual(await readControlProfile(directory), unsupported);
  await writeFile(path, "");
  assert.deepEqual(await readControlProfile(path), unsupported);
  await writeFile(path, Buffer.alloc(1024 * 1024 + 1));
  assert.deepEqual(await readControlProfile(path), unsupported);
});
