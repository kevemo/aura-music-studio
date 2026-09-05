from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_assets import find_game_asset, runtime_asset_manifest
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

router = APIRouter(tags=["Aura Game World Events"])

WorldEventPreset = Literal["spawn_portal", "audio_zone", "particle_emitter"]
_EVENT_BEHAVIORS = {"spawn", "audio_zone", "particle_emitter"}
_PROTECTED_ENTITY_IDS = {"player", "camera", "spawn"}
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class CreateWorldEventEntityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    preset: WorldEventPreset
    position: Vec3 = Field(default_factory=Vec3)
    scale: Vec3 = Field(default_factory=lambda: Vec3(x=1.0, y=1.0, z=1.0))
    target_spawn_id: str | None = Field(default=None, max_length=160)
    audio_asset_id: str | None = Field(default=None, max_length=160)
    radius: float = Field(default=5.0, ge=0.25, le=100_000.0)
    volume: float = Field(default=0.65, ge=0.0, le=1.0)
    particle_color: str = Field(default="#5be1ff", max_length=16)


class ApplyWorldEventPresetRequest(BaseModel):
    preset: WorldEventPreset
    target_spawn_id: str | None = Field(default=None, max_length=160)
    audio_asset_id: str | None = Field(default=None, max_length=160)
    radius: float = Field(default=5.0, ge=0.25, le=100_000.0)
    volume: float = Field(default=0.65, ge=0.0, le=1.0)
    particle_color: str = Field(default="#5be1ff", max_length=16)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "World Events authoring unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise ValueError("Reopen this game before changing its World Events DNA")


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def _entity(world: GameWorldDNA, entity_id: str) -> WorldEntityDNA:
    row = next((item for item in world.entities if item.id == entity_id), None)
    if row is None:
        raise ValueError("World entity not found")
    return row


def _spawn_target(world: GameWorldDNA, requested: str | None) -> str:
    target_id = str(requested or "spawn").strip() or "spawn"
    target = next((row for row in world.entities if row.id == target_id), None)
    if target is None or target.kind != "spawn":
        raise ValueError("Spawn Portal target must be an existing World spawn entity")
    return target.id


def _audio_asset(game_id: str, asset_id: str | None):
    value = str(asset_id or "").strip()
    if not value:
        raise ValueError("Audio Zone requires an imported audio/music asset")
    try:
        record = find_game_asset(game_id, value)
    except FileNotFoundError as exc:
        raise ValueError("Audio Zone asset is not imported into this game") from exc
    if record.kind not in {"audio", "music"}:
        raise ValueError("Audio Zone requires an imported audio or music asset")
    return record


def _color(value: Any) -> str:
    text = str(value or "").strip()
    return text.lower() if _HEX_COLOR.fullmatch(text) else "#5be1ff"


def _apply_event_preset(
    game: GameDNA,
    world: GameWorldDNA,
    entity: WorldEntityDNA,
    *,
    preset: WorldEventPreset,
    target_spawn_id: str | None,
    audio_asset_id: str | None,
    radius: float,
    volume: float,
    particle_color: str,
) -> None:
    if preset == "spawn_portal":
        target = _spawn_target(world, target_spawn_id)
        entity.kind = "trigger"
        entity.visible = True
        entity.physics = PhysicsBodyDNA(
            mode="trigger",
            shape="box",
            mass_kg=0.0,
            collision_layer="trigger",
            collision_mask=["player"],
        )
        entity.behaviors = [BehaviorNodeDNA(op="spawn", params={"target_spawn_id": target, "cooldown_seconds": 1.0})]
    elif preset == "audio_zone":
        record = _audio_asset(game.id, audio_asset_id)
        entity.kind = "audio"
        entity.visible = False
        entity.physics = None
        entity.behaviors = [
            BehaviorNodeDNA(
                op="audio_zone",
                params={
                    "asset_id": record.id,
                    "radius": float(radius),
                    "volume": float(volume),
                    "loop": True,
                    "fade_seconds": 0.6,
                },
            )
        ]
    else:
        entity.kind = "vfx"
        entity.visible = False
        entity.physics = None
        entity.behaviors = [
            BehaviorNodeDNA(
                op="particle_emitter",
                params={
                    "rate": 18.0,
                    "lifetime_seconds": 1.8,
                    "speed": 1.5,
                    "spread": 1.0,
                    "size": 5.0,
                    "max_particles": 160,
                    "color": _color(particle_color),
                },
            )
        ]


def create_world_event_entity(game: GameDNA, body: CreateWorldEventEntityRequest) -> WorldEntityDNA:
    _require_editable(game)
    if min(abs(body.scale.x), abs(body.scale.y), abs(body.scale.z)) < 0.000001:
        raise ValueError("World Event scale axes must be non-zero")
    world = ensure_world(game)
    entity = WorldEntityDNA(name=body.name, transform=TransformDNA(position=body.position, scale=body.scale))
    _apply_event_preset(
        game,
        world,
        entity,
        preset=body.preset,
        target_spawn_id=body.target_spawn_id,
        audio_asset_id=body.audio_asset_id,
        radius=body.radius,
        volume=body.volume,
        particle_color=body.particle_color,
    )
    world.entities.append(entity)
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def apply_world_event_preset(game: GameDNA, entity_id: str, body: ApplyWorldEventPresetRequest) -> WorldEntityDNA:
    _require_editable(game)
    if entity_id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera/spawn entities cannot be converted to a World Event preset")
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    _apply_event_preset(
        game,
        world,
        entity,
        preset=body.preset,
        target_spawn_id=body.target_spawn_id,
        audio_asset_id=body.audio_asset_id,
        radius=body.radius,
        volume=body.volume,
        particle_color=body.particle_color,
    )
    world.touch()
    save_world(world)
    _invalidate(game)
    return entity


def delete_world_event_entity(game: GameDNA, entity_id: str) -> bool:
    _require_editable(game)
    if entity_id in _PROTECTED_ENTITY_IDS:
        raise ValueError("Core player/camera/spawn entities cannot be deleted")
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    if not any(node.enabled and node.op in _EVENT_BEHAVIORS for node in entity.behaviors):
        raise ValueError("World entity is not an authored World Event")
    world.entities = [row for row in world.entities if row.id != entity_id]
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


def _text(value: Any, default: str, limit: int = 160) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _safe_event_behavior(node: BehaviorNodeDNA, *, game_id: str, runtime_assets: dict[str, dict]) -> dict[str, Any] | None:
    if not node.enabled or node.op not in _EVENT_BEHAVIORS:
        return None
    p = node.params if isinstance(node.params, dict) else {}
    if node.op == "spawn":
        params = {
            "target_spawn_id": _text(p.get("target_spawn_id"), "spawn"),
            "cooldown_seconds": _number(p.get("cooldown_seconds"), 1.0, 0.1, 60.0),
        }
    elif node.op == "audio_zone":
        asset_id = _text(p.get("asset_id"), "", 160)
        runtime = runtime_assets.get(asset_id) or {}
        params = {
            "asset_id": asset_id,
            "media_url": str(runtime.get("media_url") or "")[:300],
            "radius": _number(p.get("radius"), 5.0, 0.25, 100_000.0),
            "volume": _number(p.get("volume"), 0.65, 0.0, 1.0),
            "loop": _boolean(p.get("loop"), True),
            "fade_seconds": _number(p.get("fade_seconds"), 0.6, 0.05, 30.0),
        }
    else:
        params = {
            "rate": _number(p.get("rate"), 18.0, 0.0, 240.0),
            "lifetime_seconds": _number(p.get("lifetime_seconds"), 1.8, 0.05, 30.0),
            "speed": _number(p.get("speed"), 1.5, 0.0, 100.0),
            "spread": _number(p.get("spread"), 1.0, 0.0, 20.0),
            "size": _number(p.get("size"), 5.0, 0.5, 64.0),
            "max_particles": _integer(p.get("max_particles"), 160, 1, 512),
            "color": _color(p.get("color")),
        }
    return {"id": node.id, "op": node.op, "params": params}


def world_event_runtime_payload(game_id: str, *, world: GameWorldDNA | None = None) -> dict[str, Any]:
    world = world or load_world_optional(game_id)
    if world is None:
        return {"version": 1, "world_revision": None, "entities": [], "arbitrary_script_source_allowed": False}
    runtime_assets = {row["id"]: row for row in runtime_asset_manifest(game_id)}
    spawn_positions = {
        row.id: row.transform.position.model_dump(mode="json")
        for row in world.entities
        if row.kind == "spawn" and row.active
    }
    rows: list[dict[str, Any]] = []
    for entity in world.entities:
        behaviors = [
            safe
            for node in entity.behaviors
            if (safe := _safe_event_behavior(node, game_id=game_id, runtime_assets=runtime_assets)) is not None
        ]
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
        "spawn_positions": spawn_positions,
        "supported_behaviors": sorted(_EVENT_BEHAVIORS),
        "verified_same_origin_media_only": True,
        "external_media_urls_allowed": False,
        "arbitrary_script_source_allowed": False,
    }


def world_event_publication_blockers(game_id: str) -> list[str]:
    world = load_world_optional(game_id)
    if world is None:
        return []
    by_id = {row.id: row for row in world.entities}
    blockers: list[str] = []
    for entity in world.entities:
        for node in entity.behaviors:
            if not node.enabled or node.op not in _EVENT_BEHAVIORS:
                continue
            p = node.params if isinstance(node.params, dict) else {}
            if node.op == "spawn":
                target_id = _text(p.get("target_spawn_id"), "spawn")
                target = by_id.get(target_id)
                if target is None or target.kind != "spawn":
                    blockers.append(f"World Event '{entity.name}' references missing/non-spawn target '{target_id}'.")
                if entity.physics is None or entity.physics.mode != "trigger":
                    blockers.append(f"Spawn Portal '{entity.name}' requires trigger Physics DNA.")
            elif node.op == "audio_zone":
                asset_id = _text(p.get("asset_id"), "", 160)
                try:
                    record = find_game_asset(game_id, asset_id)
                    if record.kind not in {"audio", "music"}:
                        blockers.append(f"Audio Zone '{entity.name}' must reference an audio/music game asset.")
                except FileNotFoundError:
                    blockers.append(f"Audio Zone '{entity.name}' references missing game asset '{asset_id}'.")
    return blockers


def world_event_state(game_id: str) -> dict[str, Any]:
    world = load_world_optional(game_id)
    if world is None:
        return {"game_id": game_id, "world_revision": None, "entities": [], "runtime": world_event_runtime_payload(game_id)}
    runtime = world_event_runtime_payload(game_id, world=world)
    event_ids = {row["id"] for row in runtime["entities"]}
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
                "behaviors": [node.model_dump(mode="json") for node in row.behaviors if node.op in _EVENT_BEHAVIORS],
                "active": row.active,
                "visible": row.visible,
            }
            for row in world.entities
            if row.id in event_ids
        ],
        "presets": ["spawn_portal", "audio_zone", "particle_emitter"],
        "runtime": runtime,
        "integrity_bound_to_world_dna": True,
        "verified_same_origin_media_only": True,
        "arbitrary_script_source_allowed": False,
    }


@router.get("/api/game-forge/games/{game_id}/world-events")
def get_world_events(game_id: str, request: Request):
    _creator(request)
    _game(game_id)
    return world_event_state(game_id)


@router.post("/api/game-forge/games/{game_id}/world-events/entities")
def create_world_event_entity_route(game_id: str, body: CreateWorldEventEntityRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = create_world_event_entity(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "world_events": world_event_state(game.id), "invalidated_previous_build_and_rating": True}


@router.post("/api/game-forge/games/{game_id}/world-events/entities/{entity_id}/preset")
def apply_world_event_preset_route(game_id: str, entity_id: str, body: ApplyWorldEventPresetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        entity = apply_world_event_preset(game, entity_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity": entity.model_dump(mode="json"), "world_events": world_event_state(game.id), "invalidated_previous_build_and_rating": True}


@router.delete("/api/game-forge/games/{game_id}/world-events/entities/{entity_id}")
def delete_world_event_entity_route(game_id: str, entity_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        delete_world_event_entity(game, entity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": True, "entity_id": entity_id, "world_events": world_event_state(game.id), "invalidated_previous_build_and_rating": True}


__all__ = [
    "router",
    "WorldEventPreset",
    "CreateWorldEventEntityRequest",
    "ApplyWorldEventPresetRequest",
    "create_world_event_entity",
    "apply_world_event_preset",
    "delete_world_event_entity",
    "world_event_runtime_payload",
    "world_event_publication_blockers",
    "world_event_state",
]
