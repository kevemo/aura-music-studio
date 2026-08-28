from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .game_forge_adventure import load_adventure_optional
from .game_forge_models import GameDNA
from .game_forge_store import load_game, remove_public_snapshot, save_game
from .game_forge_world import BehaviorNodeDNA, GameWorldDNA, TransformDNA, Vec3, WorldEntityDNA, ensure_world, load_world_optional, save_world
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game State Machines"])

StateTrigger = Literal["timer", "player_near", "adventure_flag"]
_CONFLICTING_MOTION_OPS = {"patrol", "follow_target", "door", "spawn"}
_PROTECTED_ENTITY_IDS = {"player", "camera", "spawn"}


class StateOffsetDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(default=0.0, ge=-100_000.0, le=100_000.0)
    y: float = Field(default=0.0, ge=-100_000.0, le=100_000.0)
    z: float = Field(default=0.0, ge=-100_000.0, le=100_000.0)


class MachineStateDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    label: str = Field(default="State", min_length=1, max_length=120)
    visible: bool = True
    offset: StateOffsetDNA = Field(default_factory=StateOffsetDNA)
    message: str = Field(default="", max_length=160)


class MachineTransitionDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_state: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    to_state: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    trigger: StateTrigger
    seconds: float | None = Field(default=None, ge=0.05, le=86_400.0)
    radius: float | None = Field(default=None, ge=0.1, le=100_000.0)
    flag: str | None = Field(default=None, min_length=1, max_length=120)
    flag_value: bool = True
    min_state_seconds: float = Field(default=0.1, ge=0.05, le=3600.0)

    @model_validator(mode="after")
    def validate_trigger_fields(self):
        if self.trigger == "timer" and self.seconds is None:
            raise ValueError("Timer transition requires seconds")
        if self.trigger == "player_near" and self.radius is None:
            raise ValueError("player_near transition requires radius")
        if self.trigger == "adventure_flag" and not self.flag:
            raise ValueError("adventure_flag transition requires flag")
        return self


class StateMachineDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_state: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    states: list[MachineStateDNA] = Field(min_length=1, max_length=16)
    transitions: list[MachineTransitionDNA] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = [row.state for row in self.states]
        if len(ids) != len(set(ids)):
            raise ValueError("State Machine state names must be unique")
        known = set(ids)
        if self.initial_state not in known:
            raise ValueError("State Machine initial_state must reference a defined state")
        for transition in self.transitions:
            if transition.from_state not in known or transition.to_state not in known:
                raise ValueError("State Machine transition references an undefined state")
            if transition.from_state == transition.to_state:
                raise ValueError("State Machine transition must change state")
        return self


class CreateStateMachineEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    position: StateOffsetDNA = Field(default_factory=StateOffsetDNA)
    machine: StateMachineDNA


class ReplaceStateMachineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine: StateMachineDNA


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "State Machine authoring unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    if not game.actively_editable:
        raise HTTPException(409, "Reopen this game before editing State Machine DNA")
    return game


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def declared_adventure_flags(game_id: str) -> set[str]:
    adventure = load_adventure_optional(game_id)
    if adventure is None:
        return set()
    flags = set(adventure.initial_flags)
    for objective in adventure.objectives:
        if objective.flag:
            flags.add(objective.flag)
        if objective.completion_flag:
            flags.add(objective.completion_flag)
    for dialogue in adventure.dialogues:
        if dialogue.completion_flag:
            flags.add(dialogue.completion_flag)
        for choice in dialogue.choices:
            if choice.set_flag:
                flags.add(choice.set_flag)
    for gate in adventure.gates:
        if gate.requires_flag:
            flags.add(gate.requires_flag)
        if gate.open_flag:
            flags.add(gate.open_flag)
    return flags


def validate_machine_for_game(game_id: str, machine: StateMachineDNA) -> None:
    allowed_flags = declared_adventure_flags(game_id)
    for transition in machine.transitions:
        if transition.trigger == "adventure_flag" and transition.flag not in allowed_flags:
            raise ValueError(f"State Machine transition references undeclared Adventure flag '{transition.flag}'.")


def _machine_node(machine: StateMachineDNA) -> BehaviorNodeDNA:
    return BehaviorNodeDNA(op="state_machine", params=machine.model_dump(mode="json"))


def _machine_from_entity(entity: WorldEntityDNA) -> StateMachineDNA | None:
    node = next((row for row in entity.behaviors if row.op == "state_machine" and row.enabled), None)
    if node is None:
        return None
    try:
        return StateMachineDNA.model_validate(node.params)
    except ValueError:
        return None


def _conflicting_ops(entity: WorldEntityDNA) -> set[str]:
    return {row.op for row in entity.behaviors if row.enabled and row.op in _CONFLICTING_MOTION_OPS}


def create_state_machine_entity(game: GameDNA, body: CreateStateMachineEntityRequest) -> WorldEntityDNA:
    validate_machine_for_game(game.id, body.machine)
    world = ensure_world(game)
    entity = WorldEntityDNA(
        name=body.name,
        kind="mesh" if game.dimension == "3d" else "sprite",
        transform=TransformDNA(position=Vec3(**body.position.model_dump())),
        behaviors=[_machine_node(body.machine)],
        metadata={"aura_state_machine_actor": True},
    )
    world.entities.append(entity)
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def replace_state_machine(game: GameDNA, entity_id: str, machine: StateMachineDNA) -> WorldEntityDNA:
    if entity_id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera/spawn entities cannot own a State Machine")
    validate_machine_for_game(game.id, machine)
    world = ensure_world(game)
    entity = next((row for row in world.entities if row.id == entity_id), None)
    if entity is None:
        raise ValueError("World entity not found")
    conflicts = _conflicting_ops(entity)
    if conflicts:
        raise ValueError(f"State Machine cannot share transform ownership with: {', '.join(sorted(conflicts))}")
    entity.behaviors = [row for row in entity.behaviors if row.op != "state_machine"] + [_machine_node(machine)]
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def delete_state_machine_entity(game: GameDNA, entity_id: str) -> None:
    if entity_id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera/spawn entities cannot be deleted through State Machines")
    world = ensure_world(game)
    entity = next((row for row in world.entities if row.id == entity_id), None)
    if entity is None or _machine_from_entity(entity) is None:
        raise ValueError("State Machine entity not found")
    if entity.metadata.get("aura_state_machine_actor") is not True:
        raise ValueError("Only dedicated Aura State Machine actors can be deleted here; remove/replace the behavior through World DNA for other entities")
    world.entities = [row for row in world.entities if row.id != entity_id]
    world.touch()
    save_world(world)
    _invalidate(game)


def state_machine_runtime_payload(game_id: str, *, world: GameWorldDNA | None = None) -> dict:
    world = world or load_world_optional(game_id)
    if world is None:
        return {"version": 1, "entities": [], "max_states_per_machine": 16, "max_transitions_per_machine": 64, "arbitrary_script_source_allowed": False}
    rows = []
    for entity in world.entities:
        machine = _machine_from_entity(entity)
        if machine is None or not entity.active:
            continue
        try:
            validate_machine_for_game(game_id, machine)
        except ValueError:
            continue
        rows.append(
            {
                "id": entity.id,
                "name": entity.name,
                "kind": entity.kind,
                "position": entity.transform.position.model_dump(mode="json"),
                "scale": entity.transform.scale.model_dump(mode="json"),
                "machine": machine.model_dump(mode="json"),
            }
        )
    return {
        "version": 1,
        "world_revision": world.revision,
        "entities": sorted(rows, key=lambda row: row["id"]),
        "max_states_per_machine": 16,
        "max_transitions_per_machine": 64,
        "max_transitions_per_frame": 1,
        "adventure_flags_only": True,
        "arbitrary_script_source_allowed": False,
    }


def state_machine_publication_blockers(game_id: str) -> list[str]:
    world = load_world_optional(game_id)
    if world is None:
        return []
    blockers: list[str] = []
    for entity in world.entities:
        raw_nodes = [row for row in entity.behaviors if row.op == "state_machine" and row.enabled]
        if not raw_nodes:
            continue
        if len(raw_nodes) > 1:
            blockers.append(f"State Machine entity '{entity.name}' contains multiple active state_machine behaviors.")
        conflicts = _conflicting_ops(entity)
        if conflicts:
            blockers.append(f"State Machine entity '{entity.name}' conflicts with transform-owning behavior(s): {', '.join(sorted(conflicts))}.")
        try:
            machine = StateMachineDNA.model_validate(raw_nodes[0].params)
            validate_machine_for_game(game_id, machine)
        except ValueError as exc:
            blockers.append(f"State Machine entity '{entity.name}' is invalid: {exc}")
    return blockers


def state_machine_state(game_id: str) -> dict:
    world = load_world_optional(game_id)
    payload = state_machine_runtime_payload(game_id, world=world)
    return {
        "game_id": game_id,
        "world_revision": world.revision if world else None,
        "entities": payload["entities"],
        "declared_adventure_flags": sorted(declared_adventure_flags(game_id)),
        "limits": {"states_per_machine": 16, "transitions_per_machine": 64, "transitions_per_frame": 1},
        "integrity_bound_to_world_dna": True,
        "arbitrary_script_source_allowed": False,
    }


@router.get("/api/game-forge/games/{game_id}/state-machines")
def get_state_machines(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    ensure_world(game)
    return state_machine_state(game.id)


@router.post("/api/game-forge/games/{game_id}/state-machines/entities")
def create_state_machine_route(game_id: str, body: CreateStateMachineEntityRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = create_state_machine_entity(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "state_machines": state_machine_state(game.id), "invalidated_previous_build_and_rating": True}


@router.put("/api/game-forge/games/{game_id}/state-machines/entities/{entity_id}")
def replace_state_machine_route(game_id: str, entity_id: str, body: ReplaceStateMachineRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = replace_state_machine(game, entity_id, body.machine)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "state_machines": state_machine_state(game.id), "invalidated_previous_build_and_rating": True}


@router.delete("/api/game-forge/games/{game_id}/state-machines/entities/{entity_id}")
def delete_state_machine_route(game_id: str, entity_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        delete_state_machine_entity(game, entity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": True, "entity_id": entity_id, "state_machines": state_machine_state(game.id), "invalidated_previous_build_and_rating": True}


__all__ = [
    "StateOffsetDNA",
    "MachineStateDNA",
    "MachineTransitionDNA",
    "StateMachineDNA",
    "CreateStateMachineEntityRequest",
    "ReplaceStateMachineRequest",
    "declared_adventure_flags",
    "validate_machine_for_game",
    "create_state_machine_entity",
    "replace_state_machine",
    "delete_state_machine_entity",
    "state_machine_runtime_payload",
    "state_machine_publication_blockers",
    "state_machine_state",
    "router",
]
