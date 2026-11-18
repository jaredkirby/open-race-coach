"""Release admission and native child propagation; synthetic visits, no live rig."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from simcoach.live import visit_release as release
from tests.test_visit import fixture_file

REPO = Path(__file__).resolve().parents[1]


def snapshot(tmp_path):
    root = tmp_path / "snapshot"
    return root, release.prepare_release(REPO, root)


def execute(command, directory):
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True,
                            timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    name = f"child-{uuid4().hex}"
    (directory / f"{name}.stdout.log").write_text(result.stdout)
    (directory / f"{name}.stderr.log").write_text(result.stderr)
    return result


def test_manifest_binds_browser_assets_and_environment_without_extra_files(tmp_path):
    root, prepared = snapshot(tmp_path)
    assert release.verify_release(root, prepared["release_id"])["integrity"] == "PASS"
    manifest = json.loads((root / release.MANIFEST).read_text())
    assert set(release.ASSETS).issubset(manifest["files"])
    assert manifest["environment"] == prepared["environment"]
    with pytest.raises(FileExistsError):
        release.prepare_release(REPO, root)
    with pytest.raises(ValueError, match="selected_visit_release_id_mismatch"):
        release.verify_release(root, "0" * 64)


@pytest.mark.parametrize("fault", ["asset", "code", "extra", "environment", "removed"])
def test_verification_refuses_changed_snapshot(tmp_path, fault):
    root, prepared = snapshot(tmp_path)
    if fault in {"asset", "code"}:
        relative = release.ASSETS[0] if fault == "asset" else "simcoach/cli/visit.py"
        with (root / relative).open("ab") as stream:
            stream.write(b"\n# changed\n")
    elif fault == "extra":
        (root / "unexpected.py").write_text("raise RuntimeError('not a release file')")
    elif fault == "removed":
        # Preserve deliberately omitted bytes outside this synthetic snapshot.
        (root / release.ASSETS[0]).rename(tmp_path / "removed-asset.html")
    else:
        path = root / release.MANIFEST
        value = json.loads(path.read_text())
        value["environment"]["python"] = "0.0.0"
        path.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        release.verify_release(root, prepared["release_id"])


@pytest.mark.skipif(os.name != "nt", reason="native Windows process identities")
@pytest.mark.parametrize("module", sorted(release.ROLES))
def test_every_role_admits_exact_source_before_help_and_preserves_attempt(tmp_path, module):
    root, prepared = snapshot(tmp_path)
    attempt = tmp_path / "attempt"
    command = release.launch_command(root, prepared["release_id"], module, attempt, ["--help"])
    result = execute(command, tmp_path)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((attempt / "launch.json").read_text())
    assert receipt["state"] == "role_returned" and receipt["exit_code"] == 0
    assert receipt["process"]["created_100ns"] and receipt["module"] == module
    assert receipt["verified_release"]["release_id"] == prepared["release_id"]
    assert module in receipt["imported_sources"]
    assert receipt["working_directory"] == str(attempt / "working")
    assert release.verify_release(root, prepared["release_id"])["integrity"] == "PASS"
    before = (attempt / "launch.json").read_bytes()
    again = execute(command, tmp_path)
    assert again.returncode != 0 and "FileExistsError" in again.stderr
    assert (attempt / "launch.json").read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="native Windows process identities")
@pytest.mark.parametrize("fault", ["asset", "wrong_id", "not_isolated", "bytecode"])
def test_failed_admission_never_enters_role(tmp_path, fault):
    root, prepared = snapshot(tmp_path)
    attempt = tmp_path / "attempt"
    if fault == "asset":
        (root / release.ASSETS[0]).write_text("changed")
    identity = "0" * 64 if fault == "wrong_id" else prepared["release_id"]
    command = release.launch_command(root, identity,
                                     "simcoach.cli.visit", attempt, ["--help"])
    if fault == "not_isolated":
        command.remove("-I")
    if fault == "bytecode":
        command.remove("-B")
    result = execute(command, tmp_path)
    assert result.returncode != 0
    receipt = json.loads((attempt / "launch.json").read_text())
    assert receipt["state"] == "failed" and "admitted_at" not in receipt
    assert not (attempt / "working").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows process identities")
def test_direct_snapshot_entry_refused(tmp_path):
    root, _ = snapshot(tmp_path)
    code = ("import sys;sys.path.insert(0,sys.argv[1]);"
            "from simcoach.cli.visit import main;main(['--help'])")
    result = execute([sys.executable, "-I", "-B", "-c", code, str(root)], tmp_path)
    assert result.returncode != 0 and "snapshot_requires_guarded_entry" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows process identities")
def test_fixture_host_and_actual_recorder_child_share_pinned_release(tmp_path):
    root, prepared = snapshot(tmp_path)
    fixture = fixture_file(tmp_path)
    attempt, visits = tmp_path / "attempt", tmp_path / "visits"
    command = release.launch_command(root, prepared["release_id"], "simcoach.cli.visit", attempt,
        ["start", "--present", "--intent", "Synthetic pinned source test", "--fixture",
         str(fixture), "--out", str(visits), "--max-seconds", "15"])
    result = execute(command, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    visit = next(p for p in visits.iterdir() if p.is_dir())
    host = json.loads((visit / "host-process.json").read_text())
    worker = json.loads((visit / "worker/worker-process.json").read_text())
    assert host["process"]["pid"] != worker["process"]["pid"]
    for metadata in (host, worker):
        binding = metadata["source_release"]
        assert binding["release_id"] == prepared["release_id"]
        receipt = json.loads(Path(binding["launch_receipt"]).read_text())
        assert receipt["state"] == "role_returned" and receipt["exit_code"] == 0
        assert receipt["process"]["pid"] == metadata["process"]["pid"]
        assert receipt["process"]["created_100ns"] == metadata["process"]["created_100ns"]
    final = json.loads((visit / "status.json").read_text())
    assert final["status"] == "complete" and final["mode"] == "replay"
    assert final["closure"]["recordings"][0]["analysis"] == "PASS"
    assert release.verify_release(root, prepared["release_id"])["integrity"] == "PASS"


def test_child_command_cannot_forget_active_selection(tmp_path, monkeypatch):
    root, prepared = snapshot(tmp_path)
    monkeypatch.setattr(release, "_ACTIVE", dict(release_path=str(root),
                                                release_id=prepared["release_id"]))
    for module in ("simcoach.live.operations_service", "simcoach.live.visit_worker"):
        command = release.child_command(module, ["--help"], tmp_path / module)
        assert "-I" in command and "-B" in command
        assert command[command.index("--release-id") + 1] == prepared["release_id"]
        assert command[command.index("--module") + 1] == module
        assert str(root) in command


@pytest.mark.parametrize("arguments", [
    ["--out", "relative"], ["--out=relative"], ["stop", "relative"],
    ["--archive", "relative"], ["--fixture"],
])
def test_guard_refuses_paths_that_change_meaning_with_working_directory(tmp_path, arguments):
    with pytest.raises(ValueError):
        release._check_paths(tmp_path, "simcoach.cli.visit", arguments)


def test_guard_refuses_runtime_outputs_inside_source(tmp_path):
    with pytest.raises(ValueError):
        release._check_paths(tmp_path, "simcoach.cli.dashboard", ["--out", str(tmp_path / "data")])


OPERATIONS_CHILD = r'''
import sys,json,os,time
from pathlib import Path
root, identity, attempt, data, fault = sys.argv[1:]
sys.path.insert(0, root)
from simcoach.live import visit_release as release
from simcoach.cli import visit as target
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.operations_client import OperationsClient
from simcoach.live.supervisor import Context,FuelPlan
from simcoach.live.visit_recovery import probe_process
def synthetic_host(arguments):
    data_path=Path(data)
    run, archive=data_path/'visit',data_path/'archive'
    run.mkdir(); archive.mkdir()
    write_status(data_path/'test-provenance.json', dict(mode='synthetic_host_metadata',
        actual_driver_presence=False, actual_simulator=False, audio_enabled=False))
    write_status(archive/'run.json',dict(schema_version=1,expected_version=14,struct_size_bytes=20700))
    write_status(run/'host-process.json',dict(schema_version=1,process=probe_process(os.getpid())))
    write_status(run/'status.json',dict(schema_version=1,status='running',declared_presence=True))
    write_status(run/'heartbeat.json',dict(schema_version=1,at=time.time()))
    if fault=='changed_before_child':
        with (Path(root)/'docs/live-dashboard.html').open('a') as stream: stream.write('changed')
    client=OperationsClient(run,archive=archive,
        plan=FuelPlan(Context('synthetic','synthetic','short',1),.5,6,2,2,'synthetic only'),
        duration=.2)
    try:
        code=client.process.wait(timeout=15)
        (data_path/'child-return.json').write_text(json.dumps(dict(code=code)))
    finally:
        client.close_streams()
    return code
target.main=synthetic_host
raise SystemExit(release.run_release(identity,'simcoach.cli.visit',Path(attempt),[]))
'''


@pytest.mark.skipif(os.name != "nt", reason="native Windows operations child")
@pytest.mark.parametrize("fault", ["none", "changed_before_child"])
def test_actual_operations_child_rechecks_parent_release(tmp_path, fault):
    root, prepared = snapshot(tmp_path)
    result = execute([sys.executable, "-I", "-B", "-c", OPERATIONS_CHILD, str(root),
                      prepared["release_id"], str(tmp_path / "attempt"), str(tmp_path), fault],
                     tmp_path)
    receipt = json.loads((tmp_path / "visit/operations-source-launch/launch.json").read_text())
    if fault == "changed_before_child":
        assert result.returncode != 0
        assert receipt["state"] == "failed" and "admitted_at" not in receipt
        assert not (tmp_path / "visit/operations/run.json").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert receipt["state"] == "role_returned" and receipt["exit_code"] == 0
        actual = json.loads((tmp_path / "visit/operations/run.json").read_text())
        assert actual["source_release"]["release_id"] == prepared["release_id"]
        assert actual["process"]["pid"] == receipt["process"]["pid"]
        assert actual["process"]["created_100ns"] == receipt["process"]["created_100ns"]
        assert not actual["audio_enabled"]
        assert release.verify_release(root)["integrity"] == "PASS"


STORAGE_CHILD = r'''
import sys,json
from pathlib import Path
root, identity, attempt, mode = sys.argv[1:]
sys.path.insert(0,root)
from simcoach.live import visit_release as release
from simcoach.cli import telemetry_watch, dashboard
original=telemetry_watch.write_status
def publish(path,value,**kwargs):
    if mode=='initial' or value.get('state') in {'role_returned','failed'}:
        raise OSError('synthetic receipt publication failure')
    return original(path,value,**kwargs)
def effect(arguments):
    (Path(attempt)/'synthetic-effect').write_text('once')
    return 0
telemetry_watch.write_status=publish
dashboard.main=effect
raise SystemExit(release.run_release(identity,'simcoach.cli.dashboard',Path(attempt),[]))
'''


@pytest.mark.skipif(os.name != "nt", reason="native Windows admission storage failures")
@pytest.mark.parametrize("mode", ["initial", "terminal"])
def test_receipt_failure_preserves_uncertainty_without_retry(tmp_path, mode):
    root, prepared = snapshot(tmp_path)
    attempt = tmp_path / "attempt"
    result = execute([sys.executable, "-I", "-B", "-c", STORAGE_CHILD, str(root),
                      prepared["release_id"], str(attempt), mode], tmp_path)
    assert result.returncode != 0
    assert (attempt / "synthetic-effect").exists() is (mode == "terminal")
    if mode == "terminal":
        assert json.loads((attempt / "launch.json").read_text())["state"] == "role_entering"
    else:
        assert not (attempt / "launch.json").exists()
    repeat = execute(release.launch_command(root, prepared["release_id"],
        "simcoach.cli.dashboard", attempt, ["--help"]), tmp_path)
    assert repeat.returncode != 0 and "FileExistsError" in repeat.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows hidden wrapper")
def test_hidden_powershell_wrapper_uses_selected_release_and_keeps_logs_outside(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 unavailable")
    root, prepared = snapshot(tmp_path)
    attempt = tmp_path / "attempt"

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = tmp_path / "invoke.ps1"
    script.write_text(
        "& " + quote(root / "scripts/start_visit_release.ps1")
        + " -Python " + quote(sys.executable) + " -Release " + quote(root)
        + " -ReleaseId " + quote(prepared["release_id"]) + " -Attempt " + quote(attempt)
        + " -Module 'simcoach.cli.dashboard' -RoleArguments @('--help')\nexit $LASTEXITCODE\n")
    result = execute([pwsh, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File",
                      str(script)], tmp_path)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((attempt / "launch.json").read_text())
    wrapper = Path(str(attempt) + ".launcher")
    assert json.loads((wrapper / "exit.json").read_text())["wrapper_exit_code"] == 0
    assert receipt["state"] == "role_returned" and receipt["exit_code"] == 0
    assert "usage:" in (wrapper / "stdout.log").read_text()
    assert release.verify_release(root, prepared["release_id"])["integrity"] == "PASS"


@pytest.mark.skipif(os.name != "nt", reason="native Windows interrupted role")
def test_process_death_after_entry_is_not_replayed(tmp_path):
    root, prepared = snapshot(tmp_path)
    attempt = tmp_path / "attempt"
    code = r'''
import sys,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from simcoach.live import visit_release as release
from simcoach.cli import dashboard
def interrupted(arguments):
    (Path(sys.argv[3])/'synthetic-effect').write_text('once')
    os._exit(71)
dashboard.main=interrupted
release.run_release(sys.argv[2],'simcoach.cli.dashboard',Path(sys.argv[3]),[])
'''
    result = execute([sys.executable, "-I", "-B", "-c", code, str(root), prepared["release_id"],
                      str(attempt)], tmp_path)
    assert result.returncode == 71
    original = (attempt / "launch.json").read_bytes()
    assert json.loads(original)["state"] == "role_entering"
    assert (attempt / "synthetic-effect").read_text() == "once"
    again = execute(release.launch_command(root, prepared["release_id"],
        "simcoach.cli.dashboard", attempt, ["--help"]), tmp_path)
    assert again.returncode != 0 and (attempt / "launch.json").read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="native Windows source admission")
def test_mixed_loaded_module_refused_before_entry(tmp_path):
    root, prepared = snapshot(tmp_path)
    attempt = tmp_path / "attempt"
    code = r'''
import sys,types
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from simcoach.live import visit_release as release
mixed=types.ModuleType('simcoach.foreign')
mixed.__file__=str(Path(sys.argv[3]).parent/'foreign.py')
sys.modules['simcoach.foreign']=mixed
release.run_release(sys.argv[2],'simcoach.cli.dashboard',Path(sys.argv[3]),['--help'])
'''
    result = execute([sys.executable, "-I", "-B", "-c", code, str(root), prepared["release_id"],
                      str(attempt)], tmp_path)
    assert result.returncode != 0
    receipt = json.loads((attempt / "launch.json").read_text())
    assert receipt["state"] == "failed" and receipt["error"] == "mixed_visit_package_sources"
    assert "admitted_at" not in receipt
