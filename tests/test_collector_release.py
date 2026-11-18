"""Isolated source preparation and real hidden child verification; no simulator."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simcoach.live import collector_release as release


def source(tmp_path):
    root = tmp_path / "source"
    (root / "simcoach/cli").mkdir(parents=True)
    (root / "simcoach/__init__.py").write_text("")
    (root / "simcoach/cli/telemetry_watch.py").write_text("version = 'original'\n")
    (root / "pyproject.toml").write_text("# synthetic source\n")
    (root / "uv.lock").write_text("# synthetic lock\n")
    (root / ".env").write_text("PRIVATE_TEST_MARKER\n")
    (root / "simcoach/private.json").write_text("PRIVATE_TEST_MARKER\n")
    return root


def test_prepared_snapshot_excludes_private_data_and_survives_workspace_edit(tmp_path):
    repo, out = source(tmp_path), tmp_path / "release"
    prepared = release.prepare_release(repo, out)
    assert prepared["integrity"] == "PASS" and not prepared["deployed"]
    assert not (out / ".env").exists() and not (out / "simcoach/private.json").exists()
    (repo / "simcoach/cli/telemetry_watch.py").write_text("version = 'changed'\n")
    assert release.verify_release(out)["release_id"] == prepared["release_id"]
    with pytest.raises(FileExistsError):
        release.prepare_release(repo, out)


@pytest.mark.parametrize("fault", ["source", "extra_python", "extra_private", "identity", "env"])
def test_tampered_source_inventory_or_environment_is_refused(tmp_path, fault):
    out = tmp_path / "release"
    release.prepare_release(source(tmp_path), out)
    if fault == "source":
        (out / "simcoach/cli/telemetry_watch.py").write_text("changed")
    elif fault == "extra_python":
        (out / "simcoach/unreviewed.py").write_text("changed")
    elif fault == "extra_private":
        (out / ".env").write_text("changed")
    else:
        path = out / release.MANIFEST
        manifest = json.loads(path.read_text())
        if fault == "identity":
            manifest["release_id"] = "wrong"
        else:
            manifest["dependencies"]["numpy"] = "unreviewed-version"
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        release.verify_release(out)


def test_source_changed_during_prepare_preserves_failed_attempt(tmp_path, monkeypatch):
    repo, out = source(tmp_path), tmp_path / "release"
    target = repo / "simcoach/cli/telemetry_watch.py"
    original, calls = release._read, []
    def changing(path):
        if path == target:
            calls.append(1)
            if len(calls) == 2:
                target.write_text("changed during preparation")
        return original(path)
    monkeypatch.setattr(release, "_read", changing)
    with pytest.raises(ValueError, match="source_changed"):
        release.prepare_release(repo, out)
    assert (out / "prepare-failure.json").exists()
    assert not (out / release.MANIFEST).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows collector entry point and native process")
def test_real_child_uses_snapshot_and_records_native_clock_provenance(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    out, artifacts = tmp_path / "release", tmp_path / "artifacts"
    prepared = release.prepare_release(repo, out)
    artifacts.mkdir()
    script = r'''
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
from simcoach.utils.llm_logger import configure_logging
configure_logging(out / "logs")
from simcoach.cli import telemetry_watch as module
from simcoach.adapters.ams2 import SharedMemory
class Synthetic:
    def __init__(self, *args):
        self.count = 0
    def read(self):
        self.count += 1
        page = SharedMemory()
        page.mVersion = 14
        page.mSequenceNumber = self.count*2
        if self.count == 5:
            next(out.glob("ams2-*/run.json")).with_name("stop").touch()
        return bytes(page)
    def close(self):
        pass
module.ExistingMapping = Synthetic
sys.argv = ["synthetic", "--out", str(out)]
code = module.main()
identity = json.loads(next(out.glob("ams2-*/run.json")).read_text())
(out / "result.json").write_text(json.dumps(dict(code=code, module=module.__file__,
    identity=identity, input="synthetic", simulator_mapping_opened=False)))
raise SystemExit(code)
'''
    result = subprocess.run([sys.executable, "-B", "-c", script, str(artifacts)], cwd=out,
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    (artifacts / "stdout.log").write_text(result.stdout)
    (artifacts / "stderr.log").write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    value = json.loads((artifacts / "result.json").read_text())
    assert Path(value["module"]).resolve().is_relative_to(out.resolve())
    runtime = value["identity"]["runtime"]
    assert runtime["release"]["release_id"] == prepared["release_id"]
    assert runtime["clock"]["name"] == "perf_counter" and runtime["clock"]["monotonic"]
    assert runtime["process"]["state"] == "running"
    status = json.loads(next(artifacts.glob("ams2-*/status.json")).read_text())
    assert status["status"] == "complete" and status["samples"] == 5
