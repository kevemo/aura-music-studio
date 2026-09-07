from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_assets import (
    GameAssetRecord,
    detach_game_asset,
    find_game_asset,
    runtime_asset_manifest,
)
from .game_forge_models import GameDNA
from .game_forge_store import load_game, remove_public_snapshot, save_game
from .game_forge_world import GameWorldDNA, MaterialDNA, ensure_world, load_world_optional, save_world
from .plans import GAME_CREATE


router = APIRouter(tags=["Aura Game Asset Bindings"])

GlobalBindingTarget = Literal["world_background", "soundtrack", "cutscene"]
EntityBindingTarget = Literal["entity_visual", "entity_texture", "entity_audio"]
BindingTarget = Literal[
    "world_background",
    "soundtrack",
    "cutscene",
    "entity_visual",
    "entity_texture",
    "entity_audio",
]

_BINDING_METADATA_KEY = "game_asset_bindings"
_ENTITY_AUDIO_METADATA_KEY = "game_audio_asset_ref"
_GLOBAL_TARGETS = {"world_background", "soundtrack", "cutscene"}
_ALLOWED_MATERIAL_SLOTS = {
    "base_color",
    "normal",
    "metallic",
    "roughness",
    "emissive",
    "opacity",
    "height",
    "ao",
}


class BindGameAssetRequest(BaseModel):
    asset_id: str = Field(min_length=8, max_length=160)
    target: BindingTarget
    entity_id: str | None = Field(default=None, max_length=160)
    material_slot: str = Field(default="base_color", max_length=64)


class UnbindGameAssetRequest(BaseModel):
    target: BindingTarget
    entity_id: str | None = Field(default=None, max_length=160)
    material_slot: str = Field(default="base_color", max_length=64)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game asset binding unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise ValueError("Reopen this game before changing its asset bindings")


def _global_bindings(world: GameWorldDNA) -> dict[str, str]:
    raw = world.metadata.get(_BINDING_METADATA_KEY)
    if not isinstance(raw, dict):
        raw = {}
        world.metadata[_BINDING_METADATA_KEY] = raw
    return raw


def _entity(world: GameWorldDNA, entity_id: str | None):
    if not entity_id:
        raise ValueError("entity_id is required for entity asset bindings")
    entity = next((row for row in world.entities if row.id == entity_id), None)
    if entity is None:
        raise ValueError("World entity not found")
    return entity


def _validate_slot(slot: str) -> str:
    value = str(slot or "base_color").strip().lower()
    if value not in _ALLOWED_MATERIAL_SLOTS:
        raise ValueError(f"Unsupported material slot '{value}'")
    return value


def _validate_asset_target(asset: GameAssetRecord, target: BindingTarget) -> None:
    if target in {"world_background", "entity_visual", "entity_texture"} and asset.kind != "image":
        raise ValueError(f"{target} requires an image asset")
    if target in {"soundtrack", "entity_audio"} and asset.kind not in {"music", "audio"}:
        if target == "entity_audio":
            raise ValueError("entity_audio requires a music or audio asset")
        raise ValueError("soundtrack requires a music or audio asset")
    if target == "cutscene" and asset.kind != "video":
        raise ValueError("cutscene requires a video asset")


def _invalidate_after_binding_change(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def bind_game_asset(game: GameDNA, body: BindGameAssetRequest) -> dict:
    _require_editable(game)
    asset = find_game_asset(game.id, body.asset_id)
    _validate_asset_target(asset, body.target)
    world = ensure_world(game)

    if body.target in _GLOBAL_TARGETS:
        _global_bindings(world)[body.target] = asset.id
    elif body.target == "entity_visual":
        _entity(world, body.entity_id).asset_ref = asset.id
    elif body.target == "entity_texture":
        entity = _entity(world, body.entity_id)
        if entity.material is None:
            entity.material = MaterialDNA()
        entity.material.texture_refs[_validate_slot(body.material_slot)] = asset.id
    elif body.target == "entity_audio":
        _entity(world, body.entity_id).metadata[_ENTITY_AUDIO_METADATA_KEY] = asset.id
    else:  # pragma: no cover
        raise ValueError("Unsupported asset binding target")

    world.touch()
    save_world(world)
    _invalidate_after_binding_change(game)
    return binding_state(game.id, world=world)


def unbind_game_asset(game: GameDNA, body: UnbindGameAssetRequest) -> dict:
    _require_editable(game)
    world = ensure_world(game)
    changed = False

    if body.target in _GLOBAL_TARGETS:
        bindings = _global_bindings(world)
        if body.target in bindings:
            bindings.pop(body.target, None)
            changed = True
    elif body.target == "entity_visual":
        entity = _entity(world, body.entity_id)
        if entity.asset_ref is not None:
            entity.asset_ref = None
            changed = True
    elif body.target == "entity_texture":
        entity = _entity(world, body.entity_id)
        if entity.material is not None:
            slot = _validate_slot(body.material_slot)
            if slot in entity.material.texture_refs:
                entity.material.texture_refs.pop(slot, None)
                changed = True
    elif body.target == "entity_audio":
        entity = _entity(world, body.entity_id)
        if entity.metadata.pop(_ENTITY_AUDIO_METADATA_KEY, None) is not None:
            changed = True
    else:  # pragma: no cover
        raise ValueError("Unsupported asset binding target")

    if changed:
        world.touch()
        save_world(world)
        _invalidate_after_binding_change(game)
    return binding_state(game.id, world=world)


def clear_asset_bindings(game_id: str, asset_id: str) -> bool:
    """Remove every World DNA reference to one imported asset."""
    world = load_world_optional(game_id)
    if world is None:
        return False
    changed = False
    globals_ = _global_bindings(world)
    for key, value in list(globals_.items()):
        if value == asset_id:
            globals_.pop(key, None)
            changed = True
    for entity in world.entities:
        if entity.asset_ref == asset_id:
            entity.asset_ref = None
            changed = True
        if entity.metadata.get(_ENTITY_AUDIO_METADATA_KEY) == asset_id:
            entity.metadata.pop(_ENTITY_AUDIO_METADATA_KEY, None)
            changed = True
        if entity.material is not None:
            for slot, value in list(entity.material.texture_refs.items()):
                if value == asset_id:
                    entity.material.texture_refs.pop(slot, None)
                    changed = True
    if changed:
        world.touch()
        save_world(world)
    return changed


def _runtime_assets_by_id(game_id: str) -> dict[str, dict]:
    return {row["id"]: row for row in runtime_asset_manifest(game_id)}


def binding_runtime_payload(game_id: str, *, world: GameWorldDNA | None = None) -> dict:
    world = world or load_world_optional(game_id)
    if world is None:
        return {"world": {}, "entities": {}}
    assets = _runtime_assets_by_id(game_id)
    global_rows: dict[str, dict] = {}
    for target, asset_id in _global_bindings(world).items():
        row = assets.get(str(asset_id))
        if row is not None:
            global_rows[target] = row

    entity_rows: dict[str, dict] = {}
    for entity in world.entities:
        visual = assets.get(str(entity.asset_ref)) if entity.asset_ref else None
        audio_ref = entity.metadata.get(_ENTITY_AUDIO_METADATA_KEY)
        audio = assets.get(str(audio_ref)) if audio_ref else None
        textures: dict[str, dict] = {}
        if entity.material is not None:
            for slot, asset_id in entity.material.texture_refs.items():
                row = assets.get(str(asset_id))
                if row is not None:
                    textures[slot] = row
        if visual is not None or audio is not None or textures:
            entity_rows[entity.id] = {
                "visual": visual,
                "audio": audio,
                "textures": textures,
            }
    return {"world": global_rows, "entities": entity_rows}


def binding_publication_blockers(game_id: str) -> list[str]:
    world = load_world_optional(game_id)
    if world is None:
        return []
    assets = _runtime_assets_by_id(game_id)
    blockers: list[str] = []

    for target, asset_id in _global_bindings(world).items():
        row = assets.get(str(asset_id))
        if row is None:
            blockers.append(f"World asset binding '{target}' references a missing asset.")
            continue
        kind = row.get("kind")
        if target == "world_background" and kind != "image":
            blockers.append("World background binding must reference an image asset.")
        elif target == "soundtrack" and kind not in {"music", "audio"}:
            blockers.append("Soundtrack binding must reference music or audio.")
        elif target == "cutscene" and kind != "video":
            blockers.append("Cutscene binding must reference video.")

    for entity in world.entities:
        if entity.asset_ref:
            row = assets.get(str(entity.asset_ref))
            if row is None:
                blockers.append(f"Entity '{entity.name}' visual binding references a missing asset.")
            elif row.get("kind") != "image":
                blockers.append(f"Entity '{entity.name}' visual binding must reference an image asset.")
        audio_ref = entity.metadata.get(_ENTITY_AUDIO_METADATA_KEY)
        if audio_ref:
            row = assets.get(str(audio_ref))
            if row is None:
                blockers.append(f"Entity '{entity.name}' audio binding references a missing asset.")
            elif row.get("kind") not in {"music", "audio"}:
                blockers.append(f"Entity '{entity.name}' audio binding must reference music or audio.")
        if entity.material is not None:
            for slot, asset_id in entity.material.texture_refs.items():
                row = assets.get(str(asset_id))
                if row is None:
                    blockers.append(f"Entity '{entity.name}' material slot '{slot}' references a missing asset.")
                elif row.get("kind") != "image":
                    blockers.append(f"Entity '{entity.name}' material slot '{slot}' must reference an image asset.")
    return blockers


def binding_state(game_id: str, *, world: GameWorldDNA | None = None) -> dict:
    world = world or load_world_optional(game_id)
    if world is None:
        return {
            "game_id": game_id,
            "world_revision": None,
            "bindings": {"world": {}, "entities": {}},
            "available_entities": [],
        }
    return {
        "game_id": game_id,
        "world_revision": world.revision,
        "bindings": binding_runtime_payload(game_id, world=world),
        "raw_refs": {
            "world": dict(_global_bindings(world)),
            "entities": {
                entity.id: {
                    "visual": entity.asset_ref,
                    "audio": entity.metadata.get(_ENTITY_AUDIO_METADATA_KEY),
                    "textures": dict(entity.material.texture_refs) if entity.material else {},
                }
                for entity in world.entities
                if entity.asset_ref
                or entity.metadata.get(_ENTITY_AUDIO_METADATA_KEY)
                or (entity.material and entity.material.texture_refs)
            },
        },
        "available_entities": [
            {"id": entity.id, "name": entity.name, "kind": entity.kind}
            for entity in world.entities
        ],
        "allowed_material_slots": sorted(_ALLOWED_MATERIAL_SLOTS),
        "entity_audio_binding_supported": True,
        "integrity_bound_to_world_dna": True,
    }


@router.get("/api/game-forge/games/{game_id}/asset-bindings")
def get_asset_bindings(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    ensure_world(game)
    return binding_state(game.id)


@router.post("/api/game-forge/games/{game_id}/asset-bindings")
def bind_asset_route(game_id: str, body: BindGameAssetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        state = bind_game_asset(game, body)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Game asset not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **state,
        "invalidated_previous_build_and_rating": True,
    }


@router.post("/api/game-forge/games/{game_id}/asset-bindings/unbind")
def unbind_asset_route(game_id: str, body: UnbindGameAssetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        state = unbind_game_asset(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **state,
        "invalidated_previous_build_and_rating": True,
    }


@router.delete("/api/game-forge/games/{game_id}/assets/{asset_id}")
def delete_asset_with_binding_cleanup(game_id: str, asset_id: str, request: Request):
    """Binding-aware asset deletion.

    This route is composed before the lower-level asset router, so normal API deletion removes
    every World DNA reference before deleting the immutable private snapshot.
    """
    _creator(request)
    game = _game(game_id)
    try:
        _require_editable(game)
        record = find_game_asset(game.id, asset_id)
        bindings_removed = clear_asset_bindings(game.id, asset_id)
        detach_game_asset(game.id, asset_id)
        _invalidate_after_binding_change(game)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Game asset not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "deleted": True,
        "asset_id": record.id,
        "bindings_removed": bindings_removed,
        "invalidated_previous_build_and_rating": True,
    }


__all__ = [
    "router",
    "BindGameAssetRequest",
    "UnbindGameAssetRequest",
    "bind_game_asset",
    "unbind_game_asset",
    "clear_asset_bindings",
    "binding_runtime_payload",
    "binding_publication_blockers",
    "binding_state",
]
