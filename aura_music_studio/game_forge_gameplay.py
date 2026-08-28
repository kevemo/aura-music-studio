from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_models import GameDNA
from .game_forge_store import load_game, remove_public_snapshot, save_game
from .game_forge_world import (
    BehaviorNodeDNA,
    GameWorldDNA,
    PhysicsBodyDNA,
    TransformDNA,
    Vec3,
    WorldEntityDNA,
    ensure_world,
    load_world_optional,
    save_world,
)
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Gameplay"])

GameplayPreset = Literal[
    "collectible",
    "hazard",
    "checkpoint",
    "moving_platform",
    "trigger",
    "npc_patrol",
]

_EXECUTABLE_BEHAVIORS = {"collectible", "damage", "checkpoint", "patrol", "quest_trigger"}
_PROTECTED_ENTITY_IDS = {"player", "camera"}


class CreateGameplayEntityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    preset: GameplayPreset
    position: Vec3 = Field(default_factory=Vec3)
    scale: Vec3 = Field(default_factory=lambda: Vec3(x=1.0, y=1.0, z=1.0))


class UpdateGameplayEntityRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    position: Vec3 | None = None
    rotation_deg: Vec3 | None = None
    scale: Vec3 | None = None
    visible: bool | None = None
    active: bool | None = None
    physics: PhysicsBodyDNA | None = None


class ApplyGameplayPresetRequest(BaseModel):
    preset: GameplayPreset


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Gameplay authoring unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise ValueError("Reopen this game before changing its gameplay World DNA")


def _entity(world: GameWorldDNA, entity_id: str) -> WorldEntityDNA:
    row = next((item for item in world.entities if item.id == entity_id), None)
    if row is None:
        raise ValueError("World entity not found")
    return row


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def _apply_preset(entity: WorldEntityDNA, preset: GameplayPreset) -> None:
    # Presets deliberately author only closed World DNA. They never contain executable source.
    if preset == "collectible":
        entity.kind = "collectible"
        entity.physics = PhysicsBodyDNA(mode="trigger", shape="sphere", mass_kg=0.0, collision_layer="collectible", collision_mask=["player"])
        entity.behaviors = [BehaviorNodeDNA(op="collectible", params={"points": 1, "respawn": False})]
    elif preset == "hazard":
        entity.kind = "hazard"
        entity.physics = PhysicsBodyDNA(mode="trigger", shape="box", mass_kg=0.0, collision_layer="hazard", collision_mask=["player"])
        entity.behaviors = [BehaviorNodeDNA(op="damage", params={"amount": 1, "reset_to_checkpoint": True, "cooldown_seconds": 0.75})]
    elif preset == "checkpoint":
        entity.kind = "trigger"
        entity.physics = PhysicsBodyDNA(mode="trigger", shape="box", mass_kg=0.0, collision_layer="checkpoint", collision_mask=["player"])
        entity.behaviors = [BehaviorNodeDNA(op="checkpoint", params={"label": entity.name[:80]})]
    elif preset == "moving_platform":
        entity.kind = "mesh"
        entity.physics = PhysicsBodyDNA(mode="kinematic", shape="box", mass_kg=0.0, collision_layer="world", collision_mask=["world", "player"])
        entity.behaviors = [BehaviorNodeDNA(op="patrol", params={"axis": "x", "distance": 4.0, "speed": 1.5, "ping_pong": True})]
    elif preset == "trigger":
        entity.kind = "trigger"
        entity.physics = PhysicsBodyDNA(mode="trigger", shape="box", mass_kg=0.0, collision_layer="trigger", collision_mask=["player"])
        entity.behaviors = [BehaviorNodeDNA(op="quest_trigger", params={"event": entity.name[:80], "once": True})]
    elif preset == "npc_patrol":
        entity.kind = "npc"
        entity.physics = PhysicsBodyDNA(mode="kinematic", shape="capsule", mass_kg=0.0, collision_layer="npc", collision_mask=["world", "player"])
        entity.behaviors = [BehaviorNodeDNA(op="patrol", params={"axis": "x", "distance": 3.0, "speed": 1.2, "ping_pong": True})]


def create_gameplay_entity(game: GameDNA, body: CreateGameplayEntityRequest) -> WorldEntityDNA:
    _require_editable(game)
    world = ensure_world(game)
    entity = WorldEntityDNA(
        name=body.name,
        transform=TransformDNA(position=body.position, scale=body.scale),
    )
    _apply_preset(entity, body.preset)
    world.entities.append(entity)
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def update_gameplay_entity(game: GameDNA, entity_id: str, body: UpdateGameplayEntityRequest) -> WorldEntityDNA:
    _require_editable(game)
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    if body.name is not None:
        entity.name = body.name
    if body.position is not None:
        entity.transform.position = body.position
    if body.rotation_deg is not None:
        entity.transform.rotation_deg = body.rotation_deg
    if body.scale is not None:
        if min(abs(body.scale.x), abs(body.scale.y), abs(body.scale.z)) < 0.000001:
            raise ValueError("Entity scale axes must be non-zero")
        entity.transform.scale = body.scale
    if body.visible is not None:
        entity.visible = body.visible
    if body.active is not None:
        entity.active = body.active
    if body.physics is not None:
        entity.physics = body.physics
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def apply_gameplay_preset(game: GameDNA, entity_id: str, preset: GameplayPreset) -> WorldEntityDNA:
    _require_editable(game)
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    if entity.id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera entities cannot be converted to a gameplay preset")
    _apply_preset(entity, preset)
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def delete_gameplay_entity(game: GameDNA, entity_id: str) -> bool:
    _require_editable(game)
    if entity_id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera entities cannot be deleted")
    world = ensure_world(game)
    before = len(world.entities)
    world.entities = [row for row in world.entities if row.id != entity_id]
    if len(world.entities) == before:
        raise ValueError("World entity not found")
    for row in world.entities:
        if row.parent_id == entity_id:
            row.parent_id = None
    world.touch()
    save_world(world)
    _invalidate(game)
    return True


def _number(value: Any, default: float, low: float, high: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, result))


def _integer(value: Any, default: int, low: int, high: int) -> int:
    return int(round(_number(value, float(default), float(low), float(high))))


def _boolean(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _text(value: Any, default: str, limit: int = 80) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _safe_behavior(node: BehaviorNodeDNA) -> dict[str, Any] | None:
    if not node.enabled or node.op not in _EXECUTABLE_BEHAVIORS:
        return None
    p = node.params if isinstance(node.params, dict) else {}
    if node.op == "collectible":
        params = {
            "points": _integer(p.get("points"), 1, 1, 1_000_000),
            "respawn": _boolean(p.get("respawn"), False),
            "respawn_seconds": _number(p.get("respawn_seconds"), 3.0, 0.25, 3600.0),
        }
    elif node.op == "damage":
        params = {
            "amount": _integer(p.get("amount"), 1, 1, 1000),
            "reset_to_checkpoint": _boolean(p.get("reset_to_checkpoint"), True),
            "cooldown_seconds": _number(p.get("cooldown_seconds"), 0.75, 0.05, 60.0),
        }
    elif node.op == "checkpoint":
        params = {"label": _text(p.get("label"), "Checkpoint")}
    elif node.op == "patrol":
        params = {
            "axis": _text(p.get("axis"), "x", 1).lower() if _text(p.get("axis"), "x", 1).lower() in {"x", "y", "z"} else "x",
            "distance": _number(p.get("distance"), 3.0, 0.0, 100_000.0),
            "speed": _number(p.get("speed"), 1.0, 0.0, 1000.0),
            "ping_pong": _boolean(p.get("ping_pong"), True),
        }
    else:
        params = {
            "event": _text(p.get("event"), "triggered", 120),
            "once": _boolean(p.get("once"), True),
        }
    return {"id": node.id, "op": node.op, "params": params}


def gameplay_runtime_payload(world: GameWorldDNA) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for entity in world.entities:
        behaviors = [safe for node in entity.behaviors if (safe := _safe_behavior(node)) is not None]
        if not behaviors and entity.kind not in {"player", "spawn"}:
            continue
        rows.append(
            {
                "id": entity.id,
                "name": entity.name,
                "kind": entity.kind,
                "position": entity.transform.position.model_dump(mode="json"),
                "rotation": entity.transform.rotation_deg.model_dump(mode="json"),
                "scale": entity.transform.scale.model_dump(mode="json"),
                "active": bool(entity.active),
                "visible": bool(entity.visible),
                "physics": entity.physics.model_dump(mode="json") if entity.physics else None,
                "behaviors": behaviors,
            }
        )
    return {
        "version": 1,
        "world_revision": world.revision,
        "entities": rows,
        "supported_behaviors": sorted(_EXECUTABLE_BEHAVIORS),
        "arbitrary_script_source_allowed": False,
    }


def gameplay_publication_blockers(game_id: str) -> list[str]:
    world = load_world_optional(game_id)
    if world is None:
        return []
    blockers: list[str] = []
    players = [row for row in world.entities if row.active and row.kind == "player"]
    if not players:
        blockers.append("Gameplay world requires one active player entity for playtesting.")
    for entity in world.entities:
        executable = [node for node in entity.behaviors if node.enabled and node.op in _EXECUTABLE_BEHAVIORS]
        if executable and entity.physics is None and any(node.op in {"collectible", "damage", "checkpoint", "quest_trigger"} for node in executable):
            blockers.append(f"Gameplay entity '{entity.name}' requires Physics DNA for collision behavior.")
        for node in executable:
            if node.op == "patrol" and entity.physics is not None and entity.physics.mode not in {"kinematic", "dynamic"}:
                blockers.append(f"Gameplay entity '{entity.name}' patrol requires kinematic or dynamic Physics DNA.")
    return blockers


def gameplay_state(game_id: str, *, world: GameWorldDNA | None = None) -> dict[str, Any]:
    world = world or load_world_optional(game_id)
    if world is None:
        return {"game_id": game_id, "world_revision": None, "entities": [], "presets": []}
    payload = gameplay_runtime_payload(world)
    return {
        "game_id": game_id,
        "world_revision": world.revision,
        "entities": [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "transform": row.transform.model_dump(mode="json"),
                "physics": row.physics.model_dump(mode="json") if row.physics else None,
                "behaviors": [node.model_dump(mode="json") for node in row.behaviors],
                "active": row.active,
                "visible": row.visible,
            }
            for row in world.entities
        ],
        "presets": ["collectible", "hazard", "checkpoint", "moving_platform", "trigger", "npc_patrol"],
        "runtime": payload,
        "integrity_bound_to_world_dna": True,
        "arbitrary_script_source_allowed": False,
    }


@router.get("/api/game-forge/games/{game_id}/gameplay")
def get_gameplay(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    world = ensure_world(game)
    return gameplay_state(game.id, world=world)


@router.post("/api/game-forge/games/{game_id}/gameplay/entities")
def create_gameplay_entity_route(game_id: str, body: CreateGameplayEntityRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = create_gameplay_entity(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}


@router.patch("/api/game-forge/games/{game_id}/gameplay/entities/{entity_id}")
def update_gameplay_entity_route(game_id: str, entity_id: str, body: UpdateGameplayEntityRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = update_gameplay_entity(game, entity_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}


@router.post("/api/game-forge/games/{game_id}/gameplay/entities/{entity_id}/preset")
def apply_gameplay_preset_route(game_id: str, entity_id: str, body: ApplyGameplayPresetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = apply_gameplay_preset(game, entity_id, body.preset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}


@router.delete("/api/game-forge/games/{game_id}/gameplay/entities/{entity_id}")
def delete_gameplay_entity_route(game_id: str, entity_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        delete_gameplay_entity(game, entity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": True, "entity_id": entity_id, "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}


__all__ = [
    "router",
    "GameplayPreset",
    "CreateGameplayEntityRequest",
    "UpdateGameplayEntityRequest",
    "ApplyGameplayPresetRequest",
    "create_gameplay_entity",
    "update_gameplay_entity",
    "apply_gameplay_preset",
    "delete_gameplay_entity",
    "gameplay_runtime_payload",
    "gameplay_publication_blockers",
    "gameplay_state",
]
