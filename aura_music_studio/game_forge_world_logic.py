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

router = APIRouter(tags=["Aura Game World Logic"])

WorldLogicPreset = Literal["npc_follow", "timed_trigger", "auto_door"]
_LOGIC_BEHAVIORS = {"follow_target", "timer", "door"}
_PROTECTED_ENTITY_IDS = {"player", "camera"}


class CreateWorldLogicEntityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    preset: WorldLogicPreset
    position: Vec3 = Field(default_factory=Vec3)
    scale: Vec3 = Field(default_factory=lambda: Vec3(x=1.0, y=1.0, z=1.0))


class ApplyWorldLogicPresetRequest(BaseModel):
    preset: WorldLogicPreset


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Advanced World Logic unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise ValueError("Reopen this game before changing its advanced World Logic DNA")


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


def _apply_preset(entity: WorldEntityDNA, preset: WorldLogicPreset) -> None:
    if preset == "npc_follow":
        entity.kind = "npc"
        entity.physics = PhysicsBodyDNA(
            mode="kinematic",
            shape="capsule",
            mass_kg=0.0,
            collision_layer="npc",
            collision_mask=["world", "player"],
        )
        entity.behaviors = [
            BehaviorNodeDNA(
                op="follow_target",
                params={"target": "player", "speed": 2.0, "stop_distance": 1.5},
            )
        ]
    elif preset == "timed_trigger":
        entity.kind = "trigger"
        entity.physics = None
        entity.visible = False
        entity.behaviors = [
            BehaviorNodeDNA(
                op="timer",
                params={"seconds": 5.0, "repeat": False, "event": entity.name[:120]},
            )
        ]
    elif preset == "auto_door":
        entity.kind = "mesh"
        entity.physics = PhysicsBodyDNA(
            mode="kinematic",
            shape="box",
            mass_kg=0.0,
            collision_layer="world",
            collision_mask=["world", "player"],
        )
        entity.behaviors = [
            BehaviorNodeDNA(
                op="door",
                params={
                    "axis": "y",
                    "distance": 3.0,
                    "speed": 2.0,
                    "trigger_distance": 2.5,
                    "auto_close": True,
                    "close_delay": 1.5,
                },
            )
        ]


def create_world_logic_entity(game: GameDNA, body: CreateWorldLogicEntityRequest) -> WorldEntityDNA:
    _require_editable(game)
    if min(abs(body.scale.x), abs(body.scale.y), abs(body.scale.z)) < 0.000001:
        raise ValueError("Entity scale axes must be non-zero")
    world = ensure_world(game)
    entity = WorldEntityDNA(name=body.name, transform=TransformDNA(position=body.position, scale=body.scale))
    _apply_preset(entity, body.preset)
    world.entities.append(entity)
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def apply_world_logic_preset(game: GameDNA, entity_id: str, preset: WorldLogicPreset) -> WorldEntityDNA:
    _require_editable(game)
    if entity_id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera entities cannot be converted to an advanced logic preset")
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    _apply_preset(entity, preset)
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def _number(value: Any, default: float, low: float, high: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, result))


def _boolean(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _text(value: Any, default: str, limit: int = 120) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _axis(value: Any) -> str:
    axis = _text(value, "y", 1).lower()
    return axis if axis in {"x", "y", "z"} else "y"


def _safe_logic_behavior(node: BehaviorNodeDNA) -> dict[str, Any] | None:
    if not node.enabled or node.op not in _LOGIC_BEHAVIORS:
        return None
    p = node.params if isinstance(node.params, dict) else {}
    if node.op == "follow_target":
        params = {
            "target": _text(p.get("target"), "player", 160),
            "speed": _number(p.get("speed"), 2.0, 0.0, 1000.0),
            "stop_distance": _number(p.get("stop_distance"), 1.5, 0.0, 100_000.0),
        }
    elif node.op == "timer":
        params = {
            "seconds": _number(p.get("seconds"), 5.0, 0.05, 86_400.0),
            "repeat": _boolean(p.get("repeat"), False),
            "event": _text(p.get("event"), "Timer event", 160),
        }
    else:
        params = {
            "axis": _axis(p.get("axis")),
            "distance": _number(p.get("distance"), 3.0, 0.0, 100_000.0),
            "speed": _number(p.get("speed"), 2.0, 0.01, 1000.0),
            "trigger_distance": _number(p.get("trigger_distance"), 2.5, 0.1, 100_000.0),
            "auto_close": _boolean(p.get("auto_close"), True),
            "close_delay": _number(p.get("close_delay"), 1.5, 0.0, 3600.0),
        }
    return {"id": node.id, "op": node.op, "params": params}


def world_logic_runtime_payload(world: GameWorldDNA) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for entity in world.entities:
        behaviors = [safe for node in entity.behaviors if (safe := _safe_logic_behavior(node)) is not None]
        if not behaviors:
            continue
        rows.append(
            {
                "id": entity.id,
                "name": entity.name,
                "kind": entity.kind,
                "position": entity.transform.position.model_dump(mode="json"),
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
        "supported_behaviors": sorted(_LOGIC_BEHAVIORS),
        "arbitrary_script_source_allowed": False,
    }


def world_logic_publication_blockers(game_id: str) -> list[str]:
    world = load_world_optional(game_id)
    if world is None:
        return []
    ids = {row.id for row in world.entities}
    blockers: list[str] = []
    for entity in world.entities:
        for node in entity.behaviors:
            safe = _safe_logic_behavior(node)
            if safe is None:
                continue
            if node.op == "follow_target":
                target = str(safe["params"]["target"])
                if target not in ids:
                    blockers.append(f"World Logic entity '{entity.name}' follows missing target '{target}'.")
                if entity.physics is None or entity.physics.mode not in {"kinematic", "dynamic"}:
                    blockers.append(f"World Logic entity '{entity.name}' follow behavior requires kinematic or dynamic Physics DNA.")
            elif node.op == "door":
                if entity.physics is None or entity.physics.mode != "kinematic":
                    blockers.append(f"World Logic door '{entity.name}' requires kinematic Physics DNA.")
    return blockers


def world_logic_state(game_id: str, *, world: GameWorldDNA | None = None) -> dict[str, Any]:
    world = world or load_world_optional(game_id)
    if world is None:
        return {"game_id": game_id, "world_revision": None, "entities": [], "presets": []}
    payload = world_logic_runtime_payload(world)
    logic_ids = {row["id"] for row in payload["entities"]}
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
            if row.id in logic_ids
        ],
        "presets": ["npc_follow", "timed_trigger", "auto_door"],
        "runtime": payload,
        "integrity_bound_to_world_dna": True,
        "arbitrary_script_source_allowed": False,
    }


@router.get("/api/game-forge/games/{game_id}/world-logic")
def get_world_logic(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    world = ensure_world(game)
    return world_logic_state(game.id, world=world)


@router.post("/api/game-forge/games/{game_id}/world-logic/entities")
def create_world_logic_entity_route(game_id: str, body: CreateWorldLogicEntityRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = create_world_logic_entity(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "entity": entity.model_dump(mode="json"),
        "world_logic": world_logic_state(game.id),
        "invalidated_previous_build_and_rating": True,
    }


@router.post("/api/game-forge/games/{game_id}/world-logic/entities/{entity_id}/preset")
def apply_world_logic_preset_route(game_id: str, entity_id: str, body: ApplyWorldLogicPresetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = apply_world_logic_preset(game, entity_id, body.preset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "entity": entity.model_dump(mode="json"),
        "world_logic": world_logic_state(game.id),
        "invalidated_previous_build_and_rating": True,
    }


__all__ = [
    "WorldLogicPreset",
    "CreateWorldLogicEntityRequest",
    "ApplyWorldLogicPresetRequest",
    "create_world_logic_entity",
    "apply_world_logic_preset",
    "world_logic_runtime_payload",
    "world_logic_publication_blockers",
    "world_logic_state",
    "router",
]
