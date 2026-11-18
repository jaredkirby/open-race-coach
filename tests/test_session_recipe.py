"""Reviewed loading recipes exercised end-to-end with synthetic observations."""

import json
import os
from uuid import uuid4

import pytest

from simcoach.live.input_coordinator import InputCoordinator, SimulatedInput
from simcoach.live.input_gateway import InputGateway
from simcoach.live.session_recipe import GATES, ReviewedRecipe, digest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows coordinator owner")


def save(path, value):
    data = json.dumps(value, indent=2).encode()
    with path.open("xb") as stream:
        stream.write(data)
    return digest(data)


def recipe_value():
    return dict(
        schema_version=1,
        id="synthetic-practice",
        scope="synthetic_only",
        app="synthetic_menu",
        app_version="fixture-v1",
        context=dict(
            car="fixture car",
            track="fixture circuit",
            mode="practice",
            opponents=0,
            laps=None,
            conditions="dry",
        ),
        fuel_plan_sha256="b" * 64,
        steps=[
            dict(
                id=str(i),
                from_state=before,
                to_state=after,
                target=target,
                requires=[]
                if i == 0
                else ["car", "track", "mode", "opponents", "laps", "conditions"],
                provenance=dict(
                    kind="proposed",
                    evidence_sha256="c" * 64,
                    note="Generated menu fixture; not observed AMS2",
                ),
            )
            for i, (before, after, target) in enumerate(
                [
                    ("menu", "setup", "Practice"),
                    ("setup", "garage", "Start"),
                    ("garage", "cockpit_stopped", "Drive"),
                ]
            )
        ],
    )


def reviewed(root, value=None):
    recipe_path, review_path = root / "recipe.json", root / "review.json"
    sha = save(recipe_path, value or recipe_value())
    save(
        review_path,
        dict(
            schema_version=1,
            recipe_sha256=sha,
            scope="synthetic_only",
            decision="approved",
            reviewer="test-chief",
            note="Offline fixture review",
        ),
    )
    return ReviewedRecipe(recipe_path, review_path)


@pytest.fixture
def rig(tmp_path):
    recipe = reviewed(tmp_path)
    clock = [10.0]
    owner = InputCoordinator(
        tmp_path / "journal",
        clock=lambda: clock[0],
        claim_name="Local\\ORC.RecipeTest." + uuid4().hex,
    )
    backend = SimulatedInput(clock=lambda: clock[0])
    owner.grant(reason="Chief reviewed synthetic visit plan")
    gateway = InputGateway(owner, backend)
    yield recipe, owner, backend, gateway, clock, tmp_path, uuid4().hex
    gateway.close()
    owner.close(backend)


def packet(rig, **changes):
    recipe, _, backend, _, _, root, _ = rig
    value = (
        dict(
            schema_version=1,
            scope="synthetic_only",
            app_version="fixture-v1",
            observation=backend.observe(),
            context=dict(recipe.value["context"]),
        )
        | changes
    )
    path = root / ("observation-" + uuid4().hex + ".json")
    return path, save(path, value)


def compile_step(rig, index, **changes):
    recipe, owner, _, _, _, _, run = rig
    path, sha = packet(rig, **changes)
    return recipe.compile(
        owner,
        run_id=run,
        index=index,
        packet_path=path,
        packet_sha256=sha,
        active_fuel_plan_sha256="b" * 64,
    )


def finish(rig, gates):
    recipe, owner, _, _, _, _, run = rig
    path, sha = packet(rig)
    return recipe.assess(
        owner,
        run_id=run,
        packet_path=path,
        packet_sha256=sha,
        active_fuel_plan_sha256="b" * 64,
        gates=gates,
    )


def drive_recipe(rig, warm=False):
    recipe, owner, backend, gateway, clock, _, _ = rig
    binding = None
    for i, step in enumerate(recipe.value["steps"]):
        clock[0] += 0.25
        backend.value["targets"] = [step["target"]]
        if i == 0 and warm:
            backend.value.update(state="setup", targets=[])
        command = compile_step(rig, i)
        if binding is None:
            binding = gateway.bind("rig-engineer", [command])
        else:
            gateway.extend([command])
        request = {k: binding[k] for k in ("schema_version", "owner", "binding", "principal")}
        result = gateway.handle(
            binding["token"], request | dict(op="submit", command_id=command["id"])
        )
        assert result["receipt"]["state"] == "queued"
        result = owner.dispatch(command["id"], backend)
        assert result["state"] == ("skipped" if warm and i == 0 else "verified")
    return binding


@pytest.mark.parametrize("warm", [False, True])
def test_reviewed_three_step_path_uses_one_generation_and_cannot_claim_live_ready(rig, warm):
    _, owner, backend, _, clock, root, run = rig
    generation = owner.generation
    binding = drive_recipe(rig, warm)
    assert binding["generation"] == owner.generation == generation
    assert len(backend.effects) == (2 if warm else 3)
    gates = {
        name: dict(
            result="PASS",
            at=clock[0],
            expires_at=clock[0] + 1,
            run_id=run,
            generation=generation,
            fuel_plan_sha256="b" * 64,
            scope="synthetic_only",
            evidence_sha256="d" * 64,
        )
        for name in GATES
    }
    report = finish(rig, gates)
    assert report["rehearsal"] == "PASS" and report["navigation"] == "PASS"
    assert not report["live_ready"] and report["live_validation"] == "NOT RUN"
    save(root / "rehearsal.json", report)
    assert finish(rig, {})["rehearsal"] == "NOT RUN"
    gates["loaded_fuel"]["result"] = "FAIL"
    assert finish(rig, gates)["rehearsal"] == "FAIL"
    clock[0] += 3
    assert finish(rig, gates)["gates"]["loaded_fuel"] == "NOT RUN"


def test_changed_recipe_bytes_invalidate_exact_review(tmp_path):
    reviewed(tmp_path)
    with (tmp_path / "recipe.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="review_not_bound"):
        ReviewedRecipe(tmp_path / "recipe.json", tmp_path / "review.json")


@pytest.mark.parametrize(
    "change",
    [
        {"scope": "live"},
        {"app_version": "fixture-v2"},
        {"context": {"car": "wrong car"}},
        {"context": {"opponents": False}},
    ],
)
def test_observation_scope_version_and_context_are_not_inferred(rig, change):
    rig[2].value["targets"] = ["Practice"]
    with pytest.raises(ValueError):
        compile_step(rig, 0, **change)
    assert not rig[2].effects


@pytest.mark.parametrize(
    "change", [dict(at=7), dict(focused=False), dict(modal=True), dict(driving=True)]
)
def test_stale_or_unusable_observation_stops_compilation(rig, change):
    rig[2].value["targets"] = ["Practice"]
    with pytest.raises(ValueError):
        compile_step(rig, 0, observation=rig[2].observe() | change)


def test_fuel_plan_and_observation_bytes_must_match_review(rig):
    recipe, owner, backend, _, _, _, run = rig
    backend.value["targets"] = ["Practice"]
    path, sha = packet(rig)
    with pytest.raises(ValueError, match="fuel_plan_changed"):
        recipe.compile(
            owner,
            run_id=run,
            index=0,
            packet_path=path,
            packet_sha256=sha,
            active_fuel_plan_sha256="e" * 64,
        )
    with path.open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="hash_changed"):
        recipe.compile(
            owner,
            run_id=run,
            index=0,
            packet_path=path,
            packet_sha256=sha,
            active_fuel_plan_sha256="b" * 64,
        )


def test_no_retry_compile_no_skipping_unfinished_step_or_early_extension(rig):
    _, _, backend, gateway, _, _, _ = rig
    backend.value["targets"] = ["Practice"]
    first = compile_step(rig, 0)
    gateway.bind("rig-engineer", [first])
    with pytest.raises(ValueError, match="already_compiled"):
        compile_step(rig, 0)
    with pytest.raises(ValueError, match="previous_step_unconfirmed"):
        compile_step(rig, 1)
    with pytest.raises(ValueError, match="previous_unconfirmed"):
        gateway.extend([first | {"id": uuid4().hex}])


def test_unknown_action_requires_evidence_before_any_command(tmp_path):
    value = recipe_value()
    value["steps"][0]["target"] = None
    value["steps"][0]["provenance"]["kind"] = "unknown"
    recipe = reviewed(tmp_path, value)
    owner = InputCoordinator(
        tmp_path / "journal", clock=lambda: 10, claim_name="Local\\ORC.UnknownRecipe." + uuid4().hex
    )
    backend = SimulatedInput(clock=lambda: 10)
    owner.grant(reason="Synthetic review")
    try:
        test_rig = recipe, owner, backend, None, [10], tmp_path, uuid4().hex
        with pytest.raises(ValueError, match="action_unknown"):
            compile_step(test_rig, 0)
    finally:
        owner.close(backend)


def test_takeover_cannot_be_rearmed_by_recipe_extension(rig):
    recipe, owner, backend, gateway, _, _, _ = rig
    backend.value["targets"] = ["Practice"]
    first = compile_step(rig, 0)
    gateway.bind("rig-engineer", [first])
    owner.submit(first)
    owner.dispatch(first["id"], backend)
    owner.takeover(reason="Driver took controls", backend=backend)
    with pytest.raises(ValueError, match="permission_unavailable"):
        compile_step(rig, 1)
    with pytest.raises(ValueError, match="permission_unavailable"):
        gateway.extend([first | {"id": uuid4().hex}])
    assert owner.generation is None


def test_changed_native_target_between_steps_stops_the_recipe(rig):
    _, owner, backend, _, _, _, _ = rig
    backend.value["targets"] = ["Practice"]
    first = compile_step(rig, 0)
    owner.submit(first)
    owner.dispatch(first["id"], backend)
    backend.value.update(pid=2, targets=["Start"])
    with pytest.raises(ValueError, match="observer_identity_changed"):
        compile_step(rig, 1)


def test_fresh_but_wrong_run_gate_and_replaced_generation_do_not_complete(rig):
    _, owner, backend, _, clock, _, run = rig
    drive_recipe(rig)
    gates = {
        name: dict(
            result="PASS",
            at=clock[0],
            expires_at=clock[0] + 1,
            run_id=run,
            generation=owner.generation,
            fuel_plan_sha256="b" * 64,
            scope="synthetic_only",
            evidence_sha256="d" * 64,
        )
        for name in GATES
    }
    gates["loaded_fuel"]["run_id"] = uuid4().hex
    assert finish(rig, gates)["gates"]["loaded_fuel"] == "NOT RUN"
    owner.takeover(reason="New visit context", backend=backend)
    owner.grant(reason="Chief explicit new permission")
    assert finish(rig, {})["navigation"] == "NOT RUN"


def test_missing_required_context_and_mutated_review_stop_before_admission(rig):
    recipe, owner, backend, _, _, _, _ = rig
    backend.value["targets"] = ["Practice"]
    first = compile_step(rig, 0)
    owner.submit(first)
    owner.dispatch(first["id"], backend)
    backend.value["targets"] = ["Start"]
    with pytest.raises(ValueError, match="required_context_missing"):
        compile_step(rig, 1, context={})
    recipe.value["steps"][1]["target"] = "Unreviewed"
    with pytest.raises(ValueError, match="reviewed_recipe_mutated"):
        compile_step(rig, 1)


def test_configuration_lifetime_does_not_extend_fast_telemetry_lifetime(rig):
    _, owner, _, _, clock, _, run = rig
    drive_recipe(rig)
    gate = dict(
        result="PASS",
        at=clock[0],
        expires_at=clock[0] + 60,
        run_id=run,
        generation=owner.generation,
        fuel_plan_sha256="b" * 64,
        scope="synthetic_only",
        evidence_sha256="d" * 64,
    )
    result = finish(rig, {"radio_route": gate, "independent_capture": gate})
    assert result["gates"]["radio_route"] == "PASS"
    assert result["gates"]["independent_capture"] == "NOT RUN"
    assert not result["live_ready"]
