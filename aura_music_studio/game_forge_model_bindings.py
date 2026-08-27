from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_model_assets import find_game_model, list_game_models
from .game_forge_models import GameDNA
from .game_forge_store import load_game, remove_public_snapshot, save_game
from .game_forge_world import GameWorldDNA, ensure_world, load_world_optional, save_world
from .plans import GAME_CREATE


router = APIRouter(tags=["Aura Game Model Bindings"])
_MODEL_REF_KEY = "game_model_asset_ref"


class BindGameModelRequest(BaseModel):
    model_id: str = Field(min_length=8, max_length=160)
    entity_id: str = Field(min_length=1, max_length=160)


class UnbindGameModelRequest(BaseModel):
    entity_id: str = Field(min_length=1, max_length=160)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "3D model binding unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise ValueError("Reopen this game before changing its 3D model bindings")


def _entity(world: GameWorldDNA, entity_id: str):
    entity = next((row for row in world.entities if row.id == entity_id), None)
    if entity is None:
        raise ValueError("World entity not found")
    if entity.kind in {"light", "camera", "spawn", "audio", "ui_anchor"}:
        raise ValueError(f"World entity kind '{entity.kind}' cannot render a 3D model")
    return entity


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def bind_game_model(game: GameDNA, *, model_id: str, entity_id: str) -> dict:
    _require_editable(game)
    find_game_model(game.id, model_id)
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    entity.metadata[_MODEL_REF_KEY] = model_id
    world.touch()
    save_world(world)
    _invalidate(game)
    return model_binding_state(game.id, world=world)


def unbind_game_model(game: GameDNA, *, entity_id: str) -> dict:
    _require_editable(game)
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    if entity.metadata.pop(_MODEL_REF_KEY, None) is not None:
        world.touch()
        save_world(world)
        _invalidate(game)
    return model_binding_state(game.id, world=world)


def clear_model_bindings(game_id: str, model_id: str) -> bool:
    world = load_world_optional(game_id)
    if world is None:
        return False
    changed = False
    for entity in world.entities:
        if entity.metadata.get(_MODEL_REF_KEY) == model_id:
            entity.metadata.pop(_MODEL_REF_KEY, None)
            changed = True
    if changed:
        world.touch()
        save_world(world)
    return changed


def model_binding_runtime_payload(game_id: str, *, world: GameWorldDNA | None = None) -> dict[str, str]:
    world = world or load_world_optional(game_id)
    if world is None:
        return {}
    known = {row.id for row in list_game_models(game_id)}
    return {
        entity.id: str(entity.metadata[_MODEL_REF_KEY])
        for entity in world.entities
        if entity.metadata.get(_MODEL_REF_KEY) in known
    }


def model_binding_publication_blockers(game_id: str) -> list[str]:
    world = load_world_optional(game_id)
    if world is None:
        return []
    known = {row.id for row in list_game_models(game_id)}
    blockers: list[str] = []
    for entity in world.entities:
        model_id = entity.metadata.get(_MODEL_REF_KEY)
        if not model_id:
            continue
        if entity.kind in {"light", "camera", "spawn", "audio", "ui_anchor"}:
            blockers.append(f"Entity '{entity.name}' cannot render a bound 3D model.")
        elif str(model_id) not in known:
            blockers.append(f"Entity '{entity.name}' model binding references a missing model asset.")
    return blockers


def model_binding_state(game_id: str, *, world: GameWorldDNA | None = None) -> dict:
    world = world or load_world_optional(game_id)
    models = {row.id: row for row in list_game_models(game_id)}
    if world is None:
        return {"game_id": game_id, "world_revision": None, "bindings": {}, "available_entities": [], "available_models": []}
    bindings: dict[str, dict] = {}
    for entity in world.entities:
        model_id = entity.metadata.get(_MODEL_REF_KEY)
        record = models.get(str(model_id)) if model_id else None
        if record is not None:
            bindings[entity.id] = {
                "model_id": record.id,
                "model_label": record.label,
                "model_role": record.role,
            }
    return {
        "game_id": game_id,
        "world_revision": world.revision,
        "bindings": bindings,
        "available_entities": [
            {"id": entity.id, "name": entity.name, "kind": entity.kind}
            for entity in world.entities
            if entity.kind not in {"light", "camera", "spawn", "audio", "ui_anchor"}
        ],
        "available_models": [
            {"id": record.id, "label": record.label, "role": record.role, "kind": "model"}
            for record in models.values()
        ],
        "integrity_bound_to_world_dna": True,
    }


@router.get("/api/game-forge/games/{game_id}/model-bindings")
def get_model_bindings(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    ensure_world(game)
    return model_binding_state(game.id)


@router.post("/api/game-forge/games/{game_id}/model-bindings")
def bind_model_route(game_id: str, body: BindGameModelRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        state = bind_game_model(game, model_id=body.model_id, entity_id=body.entity_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Game model not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**state, "invalidated_previous_build_and_rating": True}


@router.post("/api/game-forge/games/{game_id}/model-bindings/unbind")
def unbind_model_route(game_id: str, body: UnbindGameModelRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        state = unbind_game_model(game, entity_id=body.entity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**state, "invalidated_previous_build_and_rating": True}


__all__ = [
    "router",
    "BindGameModelRequest",
    "UnbindGameModelRequest",
    "bind_game_model",
    "unbind_game_model",
    "clear_model_bindings",
    "model_binding_runtime_payload",
    "model_binding_publication_blockers",
    "model_binding_state",
]
