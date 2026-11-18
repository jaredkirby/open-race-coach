import { createHash } from "node:crypto";
import { lstat, open } from "node:fs/promises";

export const maximumProfileBytes = 1024n * 1024n;
const statOptions = { bigint: true };

function sameVersion(left, right) {
  return right.isFile() && left.dev === right.dev && left.ino === right.ino
    && left.size === right.size && left.mtimeNs === right.mtimeNs && left.ctimeNs === right.ctimeNs;
}

export async function readControlProfile(path) {
  const { contents, stats, ...observation } = await readProfileBytes(path);
  return observation;
}

export async function readProfileBytes(path, maximumBytes = maximumProfileBytes) {
  if (path === undefined || path === null || path === "") {
    return { state: "unknown", reason: "controller_profile_not_configured" };
  }

  let expected;
  try {
    expected = await lstat(path, statOptions);
  } catch (error) {
    return error.code === "ENOENT"
      ? { state: "blocked", reason: "controller_profile_missing" }
      : { state: "unknown", reason: "controller_profile_unreadable" };
  }

  if (!expected.isFile() || expected.size < 1n || expected.size > maximumBytes) {
    return { state: "unknown", reason: "controller_profile_unsupported_file" };
  }

  const unstable = { state: "unknown", reason: "controller_profile_changed_during_read" };
  try {
    const file = await open(path, "r");
    try {
      if (!sameVersion(expected, await file.stat(statOptions))) return unstable;

      const contents = Buffer.alloc(Number(expected.size));
      let offset = 0;
      while (offset < contents.length) {
        const { bytesRead } = await file.read(contents, offset, contents.length - offset, offset);
        if (bytesRead === 0) return unstable;
        offset += bytesRead;
      }

      if (!sameVersion(expected, await file.stat(statOptions))
        || !sameVersion(expected, await lstat(path, statOptions))) return unstable;
      return {
        state: "ready", reason: "controller_profile_fingerprinted",
        fingerprint: createHash("sha256").update(contents).digest("hex"),
        contents, stats: expected,
      };
    } finally {
      await file.close();
    }
  } catch (error) {
    if (error.code === "ENOENT") return unstable;
    return { state: "unknown", reason: "controller_profile_unreadable" };
  }
}
