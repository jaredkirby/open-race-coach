"""Prepare and verify a private collector source snapshot; never deploy or launch."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path, PurePosixPath

from simcoach.live.supervisor import strict_json

MAX_FILES, MAX_BYTES = 2000, 16 * 1024 * 1024
MANIFEST = "collector-release.json"
INPUTS = {"pyproject.toml", "uv.lock"}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(root, relative):
    parsed = PurePosixPath(relative)
    if (parsed.is_absolute() or ".." in parsed.parts or "\\" in relative or ":" in relative
            or relative != parsed.as_posix()):
        raise ValueError("invalid_release_path")
    path = root.joinpath(*parsed.parts)
    if path.resolve() != path.absolute() or root not in path.resolve().parents:
        raise ValueError("release_path_redirected")
    return path


def _read(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES+1)
    if len(data) > MAX_BYTES:
        raise ValueError("release_byte_limit")
    return data


def _files(root):
    files = sorted(p.relative_to(root).as_posix() for p in (root / "simcoach").rglob("*.py")
                   if "__pycache__" not in p.parts)
    files += sorted(INPUTS)
    if not 0 < len(files) <= MAX_FILES or "simcoach/cli/telemetry_watch.py" not in files:
        raise ValueError("invalid_release_inventory")
    return files


def _inventory(root):
    total, result = 0, {}
    for relative in _files(root):
        raw = _read(_path(root, relative))
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError("release_byte_limit")
        result[relative] = dict(sha256=_digest(raw), size=len(raw))
    return result


def prepare_release(repo, out):
    repo, out = Path(repo).resolve(strict=True), Path(out).resolve()
    if out == repo or out in repo.parents or repo / "simcoach" in out.parents:
        raise ValueError("release_destination_overlaps_source")
    before = _inventory(repo)
    out.mkdir(parents=True, exist_ok=False)
    try:
        for relative, expected in before.items():
            raw = _read(_path(repo, relative))
            if _digest(raw) != expected["sha256"]:
                raise ValueError("source_changed_during_prepare")
            destination = _path(out, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(raw)
        if _inventory(repo) != before or _inventory(out) != before:
            raise ValueError("source_changed_during_prepare")
        manifest = dict(schema_version=1, kind="collector_source_snapshot", files=before,
            release_id=_digest(json.dumps(before, sort_keys=True).encode()),
            prepared_at=time.time(),
            python=platform.python_version(), dependencies={
                name: importlib.metadata.version(name)
                for name in ("numpy", "openai", "pandas", "pyarrow", "PyYAML", "ulid-py")},
            deployed=False, acceptance="NOT RUN")
        # Import lazily: telemetry_watch imports this module for its own provenance.
        from simcoach.cli.telemetry_watch import write_status
        write_status(out / MANIFEST, manifest)
        return verify_release(out)
    except BaseException as exc:
        with (out / "prepare-failure.json").open("x", encoding="utf8") as stream:
            json.dump(dict(error_type=type(exc).__name__, deployment="NOT RUN"), stream)
        raise


def verify_release(root):
    root = Path(root).resolve(strict=True)
    manifest = strict_json(_read(_path(root, MANIFEST)).decode("utf8"))
    if (manifest.get("schema_version") != 1 or manifest.get("kind") != "collector_source_snapshot"
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("unsupported_release")
    actual = _inventory(root)
    if actual != manifest["files"]:
        raise ValueError("release_integrity_mismatch")
    allowed = set(actual) | {MANIFEST}
    if any(p.relative_to(root).as_posix() not in allowed for p in root.rglob("*")
           if p.is_file()):
        raise ValueError("unexpected_release_file")
    digest = _digest(json.dumps(actual, sort_keys=True).encode())
    if manifest.get("release_id") != digest:
        raise ValueError("release_identity_mismatch")
    if (manifest.get("python") != platform.python_version()
            or any(importlib.metadata.version(name) != version
                   for name, version in manifest.get("dependencies", {}).items())
            or set(manifest.get("dependencies", {})) != {
                "numpy", "openai", "pandas", "pyarrow", "PyYAML", "ulid-py"}):
        raise ValueError("release_environment_changed")
    return dict(schema_version=1, release_path=str(root), release_id=digest, integrity="PASS",
                files=len(actual), source_bytes=sum(r["size"] for r in actual.values()),
                deployed=False, live_acceptance="NOT RUN")


def runtime_provenance():
    """Record startup evidence; workspace hashes cannot prove an old process version."""
    from simcoach.live.visit_recovery import probe_process
    root = Path(__file__).resolve().parents[2]
    info = time.get_clock_info("perf_counter")
    snapshot = verify_release(root) if (root / MANIFEST).exists() else None
    return dict(schema_version=1, source_mode="snapshot" if snapshot else "workspace",
                release=snapshot,
                process=probe_process(os.getpid()), python=platform.python_version(),
                executable=sys.executable, clock=dict(name="perf_counter",
                    implementation=info.implementation, resolution_s=info.resolution,
                    monotonic=info.monotonic, adjustable=info.adjustable))
