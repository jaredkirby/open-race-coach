"""Reviewed semantic recipes compiled from fresh synthetic observations only."""

from __future__ import annotations

import hashlib
from pathlib import Path

from simcoach.live.input_coordinator import encoded, finite, label, observation
from simcoach.live.supervisor import strict_json

CONTEXT = {"car", "track", "mode", "opponents", "laps", "conditions"}
GATES = {
    "devices",
    "profiles",
    "haptic_output",
    "independent_capture",
    "canonical_intake",
    "fuel_supervision",
    "loaded_fuel",
    "radio_route",
    "storage",
    "maintenance_idle",
    "driver_handoff",
}
# Offline policy caps, not device availability promises. A future live adapter
# must invalidate configuration evidence on change rather than ask for repeated listening.
GATE_TTL = {name: 2 for name in GATES} | {"profiles": 300, "radio_route": 300, "storage": 30}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def hex_id(value, size=64):
    if (
        not isinstance(value, str)
        or len(value) != size
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("invalid_recipe_identity")
    return value


def fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError("invalid_recipe_fields")


def read_artifact(path, limit=65536):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError("recipe_artifact_redirected")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("recipe_artifact_too_large")
    return data, strict_json(data.decode("utf8"))


def context(value):
    fields(value, CONTEXT)
    for name in ("car", "track", "mode", "conditions"):
        label(value[name])
    if value["mode"] not in {"practice", "race", "time_trial"}:
        raise ValueError("unsupported_recipe_mode")
    if type(value["opponents"]) is not int or not 0 <= value["opponents"] <= 99:
        raise ValueError("invalid_recipe_opponents")
    if value["laps"] is not None and (
        type(value["laps"]) is not int or not 1 <= value["laps"] <= 999
    ):
        raise ValueError("invalid_recipe_laps")
    if value["mode"] == "race" and value["laps"] is None:
        raise ValueError("recipe_race_length_required")


class ReviewedRecipe:
    """A Chief-reviewed offline recipe; hashes bind artifacts, not real UI truth."""

    def __init__(self, recipe_path, review_path):
        raw, recipe = read_artifact(recipe_path)
        review_raw, review = read_artifact(review_path)
        fields(
            recipe,
            {
                "schema_version",
                "id",
                "scope",
                "app",
                "app_version",
                "context",
                "fuel_plan_sha256",
                "steps",
            },
        )
        fields(review, {"schema_version", "recipe_sha256", "scope", "decision", "reviewer", "note"})
        if (
            type(recipe["schema_version"]) is not int
            or recipe["schema_version"] != 1
            or type(review["schema_version"]) is not int
            or review["schema_version"] != 1
            or recipe["scope"] != "synthetic_only"
            or review["scope"] != "synthetic_only"
            or review["decision"] != "approved"
            or review["recipe_sha256"] != digest(raw)
        ):
            raise ValueError("recipe_review_not_bound")
        label(review["reviewer"])
        label(review["note"], 512)
        for name in ("id", "app", "app_version"):
            label(recipe[name])
        context(recipe["context"])
        hex_id(recipe["fuel_plan_sha256"])
        steps = recipe["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            raise ValueError("invalid_recipe_steps")
        seen = set()
        for i, step in enumerate(steps):
            fields(step, {"id", "from_state", "to_state", "target", "requires", "provenance"})
            for name in ("id", "from_state", "to_state"):
                label(step[name])
            if step["id"] in seen or step["from_state"] == step["to_state"]:
                raise ValueError("invalid_recipe_step_identity")
            seen.add(step["id"])
            if i and step["from_state"] != steps[i - 1]["to_state"]:
                raise ValueError("recipe_path_disconnected")
            if step["target"] is not None:
                label(step["target"])
            requires = step["requires"]
            if (
                not isinstance(requires, list)
                or not all(isinstance(k, str) for k in requires)
                or len(set(requires)) != len(requires)
                or not set(requires) <= CONTEXT
            ):
                raise ValueError("invalid_recipe_requirements")
            provenance = step["provenance"]
            fields(provenance, {"kind", "evidence_sha256", "note"})
            if provenance["kind"] not in {"observed_summary", "proposed", "unknown"}:
                raise ValueError("invalid_recipe_provenance")
            hex_id(provenance["evidence_sha256"])
            label(provenance["note"], 512)
        self.raw, self.value = raw, recipe
        self.sha256, self.review_sha256 = digest(raw), digest(review_raw)
        self.canonical = encoded(recipe)

    def command_id(self, run_id, index):
        hex_id(run_id, 32)
        if type(index) is not int or not 0 <= index < len(self.value["steps"]):
            raise ValueError("invalid_recipe_step_index")
        return digest(f"{run_id}:{self.sha256}:{index}".encode())[:32]

    def packet(self, path, expected_sha256, *, now, active_fuel_plan_sha256):
        if encoded(self.value) != self.canonical:
            raise ValueError("reviewed_recipe_mutated")
        raw, packet = read_artifact(path)
        if digest(raw) != hex_id(expected_sha256):
            raise ValueError("recipe_observation_hash_changed")
        fields(packet, {"schema_version", "scope", "app_version", "observation", "context"})
        if (
            type(packet["schema_version"]) is not int
            or packet["schema_version"] != 1
            or packet["scope"] != "synthetic_only"
            or packet["app_version"] != self.value["app_version"]
        ):
            raise ValueError("recipe_observation_scope_or_version")
        observed = observation(packet["observation"])
        finite(now)
        if not 0 <= now - observed["at"] < 2:
            raise ValueError("recipe_observation_stale")
        if (
            observed["app"] != self.value["app"]
            or not observed["focused"]
            or observed["modal"]
            or observed["driving"]
        ):
            raise ValueError("recipe_observation_unusable")
        if active_fuel_plan_sha256 != self.value["fuel_plan_sha256"]:
            raise ValueError("recipe_fuel_plan_changed")
        # Partial context is allowed in earlier menus; required fields fail closed.
        if not isinstance(packet["context"], dict) or not set(packet["context"]) <= CONTEXT:
            raise ValueError("invalid_recipe_observed_context")
        for key, value in packet["context"].items():
            expected = self.value["context"][key]
            if type(value) is not type(expected) or value != expected:
                raise ValueError("recipe_context_mismatch")
        return packet

    def compile(self, owner, *, run_id, index, packet_path, packet_sha256, active_fuel_plan_sha256):
        identity = self.command_id(run_id, index)
        with owner.lock:
            owner.usable()
            now = owner.now()
            packet = self.packet(
                packet_path, packet_sha256, now=now, active_fuel_plan_sha256=active_fuel_plan_sha256
            )
            if not owner.generation or owner.cancel.is_set() or owner.unresolved():
                raise ValueError("recipe_permission_unavailable")
            if owner.receipt(identity):
                raise ValueError("recipe_step_already_submitted")
            if any(
                strict_json(row[0])["command_id"] == identity
                for row in owner.db.execute(
                    "SELECT value FROM events WHERE kind='recipe_step_compiled'"
                )
            ):
                raise ValueError("recipe_step_already_compiled")
            if index:
                previous = owner.receipt(self.command_id(run_id, index - 1))
                if not previous or previous["state"] not in {"verified", "skipped"}:
                    raise ValueError("recipe_previous_step_unconfirmed")
                prior = strict_json(previous["payload"])
                if prior["generation"] != owner.generation or prior["recipe_sha256"] != self.sha256:
                    raise ValueError("recipe_run_generation_changed")
                for key in ("app", "pid", "created", "window"):
                    if packet["observation"][key] != prior["before"][key]:
                        raise ValueError("recipe_observer_identity_changed")
            step = self.value["steps"][index]
            if step["target"] is None or step["provenance"]["kind"] == "unknown":
                raise ValueError("recipe_action_unknown")
            if not set(step["requires"]) <= set(packet["context"]):
                raise ValueError("recipe_required_context_missing")
            before = packet["observation"]
            if before["state"] not in {step["from_state"], step["to_state"]}:
                raise ValueError("recipe_unexpected_state")
            if step["target"] not in before["targets"] and before["state"] != step["to_state"]:
                raise ValueError("recipe_target_unobserved")
            command = dict(
                id=identity,
                generation=owner.generation,
                recipe_sha256=self.sha256,
                before=before,
                action="select",
                target=step["target"],
                expected_state=step["to_state"],
                deadline=before["at"] + 1.5,
            )
            if now >= command["deadline"]:
                raise ValueError("recipe_dispatch_window_expired")
            with owner.transaction():
                owner.event(
                    "recipe_step_compiled",
                    dict(
                        run_id=run_id,
                        recipe_sha256=self.sha256,
                        review_sha256=self.review_sha256,
                        observation_sha256=packet_sha256,
                        fuel_plan_sha256=active_fuel_plan_sha256,
                        index=index,
                        step_id=step["id"],
                        command_id=identity,
                    ),
                )
            return command

    def assess(self, owner, **kwargs):
        with owner.lock:
            owner.usable()
            return self._assess(owner, **kwargs)

    def _assess(self, owner, *, run_id, packet_path, packet_sha256, active_fuel_plan_sha256, gates):
        """Offline completion evidence only; never a live readiness certificate."""
        now = owner.now()
        packet = self.packet(
            packet_path, packet_sha256, now=now, active_fuel_plan_sha256=active_fuel_plan_sha256
        )
        if not isinstance(gates, dict) or not set(gates) <= GATES:
            raise ValueError("invalid_recipe_gates")
        complete = (
            set(packet["context"]) == CONTEXT
            and owner.generation is not None
            and not owner.cancel.is_set()
            and not owner.unresolved()
        )
        for index in range(len(self.value["steps"])):
            receipt = owner.receipt(self.command_id(run_id, index))
            complete &= bool(receipt and receipt["state"] in {"verified", "skipped"})
            if receipt:
                command = strict_json(receipt["payload"])
                complete &= (
                    command["generation"] == owner.generation
                    and command["recipe_sha256"] == self.sha256
                )
                complete &= all(
                    packet["observation"][key] == command["before"][key]
                    for key in ("app", "pid", "created", "window")
                )
        complete &= packet["observation"]["state"] == self.value["steps"][-1]["to_state"]
        results = {}
        for name in sorted(GATES):
            gate = gates.get(name)
            result = "NOT RUN"
            if isinstance(gate, dict):
                try:
                    fields(
                        gate,
                        {
                            "result",
                            "at",
                            "expires_at",
                            "evidence_sha256",
                            "scope",
                            "run_id",
                            "generation",
                            "fuel_plan_sha256",
                        },
                    )
                    hex_id(gate["evidence_sha256"])
                    if (
                        gate["scope"] != "synthetic_only"
                        or gate["result"] not in {"PASS", "FAIL", "NOT RUN"}
                        or gate["run_id"] != run_id
                        or gate["generation"] != owner.generation
                        or gate["fuel_plan_sha256"] != active_fuel_plan_sha256
                        or not finite(gate["at"])
                        <= now
                        < finite(gate["expires_at"])
                        <= gate["at"] + GATE_TTL[name]
                    ):
                        raise ValueError("invalid_gate")
                    result = gate["result"]
                except (ValueError, TypeError):
                    result = "NOT RUN"
            results[name] = result
        rehearsal = (
            "FAIL"
            if "FAIL" in results.values()
            else ("PASS" if complete and all(v == "PASS" for v in results.values()) else "NOT RUN")
        )
        return dict(
            schema_version=1,
            scope="synthetic_only",
            recipe_sha256=self.sha256,
            run_id=run_id,
            navigation="PASS" if complete else "NOT RUN",
            gates=results,
            rehearsal=rehearsal,
            live_ready=False,
            live_validation="NOT RUN",
        )
