"""Explicit visit source snapshots and per-process admission, never deployment."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path
from uuid import uuid4

from simcoach.live.collector_release import _digest, _inventory, _path, _read
from simcoach.live.supervisor import strict_json

MANIFEST = "visit-release.json"
ASSETS = ("docs/live-dashboard.html", "docs/live-dashboard.mjs", "scripts/start_visit_release.ps1")
DEPENDENCIES = ("numpy", "openai", "pandas", "pyarrow", "PyYAML", "ulid-py")
ROLES = frozenset(("simcoach.cli.visit", "simcoach.live.visit_worker",
                  "simcoach.live.operations_service", "simcoach.cli.dashboard",
                  "simcoach.cli.dashboard_feedback"))
BOOTSTRAP = ("import sys;sys.path.insert(0,sys.argv.pop(1));"
             "from simcoach.cli.visit_release import main;raise SystemExit(main())")
_ACTIVE = None


def _environment():
    return dict(python=platform.python_version(), dependencies={
        name: importlib.metadata.version(name) for name in DEPENDENCIES})


def _files(root):
    files = _inventory(root)
    for relative in ASSETS:
        raw = _read(_path(root, relative))
        files[relative] = dict(sha256=_digest(raw), size=len(raw))
    if sum(item["size"] for item in files.values()) > 16 * 1024 * 1024:
        raise ValueError("visit_release_byte_limit")
    return files


def _identity(files, environment):
    return _digest(json.dumps(dict(kind="visit_source_snapshot", files=files,
                                   environment=environment), sort_keys=True).encode())


def prepare_release(repo, out):
    repo, out = Path(repo).resolve(strict=True), Path(out).resolve()
    if out == repo or out in repo.parents or repo / "simcoach" in out.parents:
        raise ValueError("release_destination_overlaps_source")
    before, environment = _files(repo), _environment()
    out.mkdir(parents=True, exist_ok=False)
    try:
        for relative, expected in before.items():
            raw = _read(_path(repo, relative))
            if _digest(raw) != expected["sha256"]:
                raise ValueError("source_changed_during_prepare")
            target = _path(out, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(raw)
        if _files(repo) != before or _files(out) != before or _environment() != environment:
            raise ValueError("source_or_environment_changed_during_prepare")
        from simcoach.cli.telemetry_watch import write_status
        write_status(out / MANIFEST, dict(schema_version=1, kind="visit_source_snapshot",
            files=before, environment=environment, release_id=_identity(before, environment),
            prepared_at=time.time(), deployment="NOT RUN", live_acceptance="NOT RUN"))
        return verify_release(out)
    except BaseException as exc:
        with (out / "prepare-failure.json").open("x", encoding="utf8") as handle:
            json.dump(dict(error_type=type(exc).__name__, deployment="NOT RUN"), handle)
        raise


def verify_release(root, expected_id=None):
    root = Path(root).resolve(strict=True)
    manifest = strict_json(_read(_path(root, MANIFEST)).decode("utf8"))
    if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("kind") != "visit_source_snapshot"):
        raise ValueError("unsupported_visit_release")
    actual = _files(root)
    if actual != manifest.get("files"):
        raise ValueError("visit_release_integrity_mismatch")
    allowed = set(actual) | {MANIFEST}
    if any(p.relative_to(root).as_posix() not in allowed for p in root.rglob("*") if p.is_file()):
        raise ValueError("unexpected_visit_release_file")
    environment = _environment()
    if environment != manifest.get("environment"):
        raise ValueError("visit_release_environment_changed")
    identity = _identity(actual, environment)
    if (identity != manifest.get("release_id")
            or expected_id is not None and identity != expected_id):
        raise ValueError("selected_visit_release_id_mismatch")
    return dict(release_path=str(root), release_id=identity, integrity="PASS", files=len(actual),
                source_bytes=sum(v["size"] for v in actual.values()), environment=environment,
                deployment="NOT RUN", live_acceptance="NOT RUN")


def active_binding():
    return dict(_ACTIVE) if _ACTIVE is not None else None


def require_guarded_entry():
    root = Path(__file__).resolve().parents[2]
    if (root / MANIFEST).exists() and _ACTIVE is None:
        raise RuntimeError("visit_snapshot_requires_guarded_entry")


def launch_command(root, identity, module, attempt, arguments):
    if module not in ROLES:
        raise ValueError("unsupported_visit_role")
    return [sys.executable, "-I", "-B", "-u", "-c", BOOTSTRAP, str(Path(root).resolve()),
            "run", "--release-id", identity, "--attempt", str(Path(attempt).absolute()),
            "--module", module, "--", *arguments]


def child_command(module, arguments, attempt):
    require_guarded_entry()
    if _ACTIVE is None:
        return [sys.executable, "-m", module, *arguments]
    # Admission is repeated by the actual child, not only by its parent.
    return launch_command(_ACTIVE["release_path"], _ACTIVE["release_id"], module,
                          attempt, arguments)


def _loaded_sources(root):
    sources = {}
    for name, module in tuple(sys.modules.items()):
        if name == "simcoach" or name.startswith("simcoach."):
            value = getattr(module, "__file__", None)
            if value is None or not Path(value).resolve().is_relative_to(root):
                raise ValueError("mixed_visit_package_sources")
            sources[name] = str(Path(value).resolve().relative_to(root))
    return sources


def _check_paths(root, module, arguments):
    # Changing cwd for private logs must not reinterpret an input/output locator.
    flags = {"--archive", "--fixture", "--plan", "--out", "--phrase", "--loss-phrase",
             "--recovery-phrase", "--chief-phrase", "--audio-selection", "--selection",
             "--visit", "--operations", "--session", "--review", "--output", "--run",
             "--lock-root"}
    paths = []
    for index, argument in enumerate(arguments):
        flag, separator, value = argument.partition("=")
        if flag in flags:
            if not separator:
                if index + 1 == len(arguments):
                    raise ValueError("missing_role_path")
                value = arguments[index + 1]
            paths.append(value)
    if (module == "simcoach.cli.visit" and len(arguments) > 1
            and arguments[0] in {"stop", "checkpoint", "inspect", "outcome", "review",
                                 "reason", "fuel", "fuel-check", "plan"}
            and arguments[1] != "--help"):
        paths.append(arguments[1])
    for value in paths:
        path = Path(value)
        if (not path.is_absolute() or path.resolve() != path.absolute()
                or path == root or root in path.parents or path in root.parents):
            raise ValueError("role_paths_require_absolute_separate_unredirected_locations")


def run_release(identity, module, attempt, arguments):
    """One opt-in role call with fresh evidence; no task, retry or owner adoption."""
    global _ACTIVE
    from simcoach.cli.telemetry_watch import write_status
    from simcoach.live.visit_recovery import probe_process

    if _ACTIVE is not None or module not in ROLES:
        raise ValueError("unsupported_or_nested_visit_role")
    root = Path(__file__).resolve().parents[2]
    attempt = Path(attempt).absolute()
    if (attempt.resolve() != attempt or attempt == root or root in attempt.parents
            or attempt in root.parents):
        raise ValueError("attempt_must_be_separate_from_release_without_redirects")
    attempt.mkdir(parents=True, exist_ok=False)
    receipt = attempt / "launch.json"
    state = dict(schema_version=1, kind="visit_role_launch", state="admitting",
        requested_release_id=identity, release_path=str(root), module=module,
        arguments=list(arguments), process=probe_process(os.getpid()), started_at=time.time(),
        automatic_retry=False, exit_code=None, live_acceptance="NOT RUN")

    def publish():
        write_status(receipt, state, temporary=attempt / f".{uuid4().hex}.tmp")

    publish()  # No application effects after an unrecorded admission.
    try:
        if not sys.flags.isolated or not sys.dont_write_bytecode:
            raise ValueError("isolated_no_bytecode_interpreter_required")
        verified = verify_release(root, identity)
        sources = _loaded_sources(root)
        _check_paths(root, module, arguments)
        if state["process"]["state"] != "running" or not state["process"]["created_100ns"]:
            raise RuntimeError("native_process_identity_unconfirmed")
        working = attempt / "working"
        working.mkdir()
        os.chdir(working)
        # Isolated children ignore these too; do not propagate accidental import roots.
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSAFEPATH"):
            os.environ.pop(key, None)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        from simcoach.utils.llm_logger import configure_logging
        configure_logging(attempt / "logs")
        _ACTIVE = dict(release_path=str(root), release_id=identity, launch_receipt=str(receipt))
        target = importlib.import_module(module)
        state.update(state="role_entering", verified_release=verified,
                     admitted_at=time.time(), imported_sources=_loaded_sources(root),
                     admission_sources=sources, working_directory=str(working))
        publish()
        try:
            code = target.main(arguments)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
        state.update(state="role_returned", exit_code=code, returned_at=time.time(),
                     imported_sources=_loaded_sources(root))
        verify_release(root, identity)  # Changed source after an effect is retained as failure.
        publish()
        return code
    except BaseException as exc:
        state.update(state="failed", error_type=type(exc).__name__, error=str(exc),
                     ended_at=time.time())
        publish()
        raise
    finally:
        _ACTIVE = None
