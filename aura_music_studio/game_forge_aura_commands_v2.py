from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_asset_bindings import (
    BindGameAssetRequest,
    UnbindGameAssetRequest,
    bind_game_asset,
    binding_state,
    unbind_game_asset,
)
from .game_forge_assets import GameAssetRecord, list_game_assets, public_asset
from .game_forge_model_assets import GameModelAssetRecord, list_game_models
from .game_forge_model_assets import router as game_model_assets_router
from .game_forge_model_bindings import (
    bind_game_model,
    model_binding_state,
    unbind_game_model,
)
from .game_forge_model_bindings import router as game_model_bindings_router
from .game_forge_models import GameDNA
from .game_forge_store import load_game
from .game_forge_world import ensure_world
from .plans import GAME_CREATE


router = APIRouter(tags=["Aura Game Commands"])
# Model upload/binding endpoints compose through this router so the stable Game World router does not
# need cross-chat composition edits. They remain ordinary authenticated Game Forge routes.
router.include_router(game_model_assets_router)
router.include_router(game_model_bindings_router)

CommandAction = Literal["inspect", "bind", "unbind", "clarify"]


class GameAuraCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=1600)


class GameAuraCommandResult(BaseModel):
    action: CommandAction
    message: str
    game_id: str
    changed: bool = False
    needs_clarification: bool = False
    bindings: dict = Field(default_factory=dict)
    assets: list[dict] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    parsed: dict = Field(default_factory=dict)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Aura Game editing unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _asset_view(record: GameAssetRecord) -> dict:
    row = public_asset(record)
    return {
        "id": row["id"],
        "label": row["label"],
        "kind": row["kind"],
        "role": row["role"],
        "media_url": row["media_url"],
        "rights_confirmed": row["rights_confirmed"],
    }


def _model_view(record: GameModelAssetRecord) -> dict:
    return {
        "id": record.id,
        "label": record.label,
        "kind": "model",
        "role": record.role,
        "media_url": None,
        "rights_confirmed": record.rights_confirmed,
        "mesh_summary": record.mesh_summary,
    }


def _all_asset_views(game_id: str) -> list[dict]:
    rows = [_asset_view(row) for row in list_game_assets(game_id)]
    rows.extend(_model_view(row) for row in list_game_models(game_id))
    return sorted(rows, key=lambda row: (str(row.get("kind")), str(row.get("label", "")).casefold()))


def _combined_bindings(game_id: str) -> dict:
    state = binding_state(game_id)
    state["model_bindings"] = model_binding_state(game_id)
    return state


def _target_from_text(text: str) -> tuple[str | None, str | None]:
    value = _norm(text)
    if any(x in value for x in ("soundtrack", "background music", "theme music", "music track", "game music")):
        return "soundtrack", None
    if any(x in value for x in ("cutscene", "cut scene", "cinematic", "intro video", "opening video")):
        return "cutscene", None
    if any(x in value for x in ("world background", "game background", "background image", "backdrop", "scene background")):
        return "world_background", None
    if any(x in value for x in ("spatial audio", "positional audio", "3d audio", "sound effect", "sfx", "audio source")):
        return "entity_audio", None
    slots = {
        "normal map": "normal",
        "normal texture": "normal",
        "metallic map": "metallic",
        "metallic texture": "metallic",
        "roughness map": "roughness",
        "roughness texture": "roughness",
        "emissive map": "emissive",
        "emissive texture": "emissive",
        "opacity map": "opacity",
        "opacity texture": "opacity",
        "height map": "height",
        "height texture": "height",
        "ao map": "ao",
        "ambient occlusion": "ao",
        "base color": "base_color",
        "base colour": "base_color",
        "albedo": "base_color",
    }
    for phrase, slot in slots.items():
        if phrase in value:
            return "entity_texture", slot
    if any(x in value for x in ("3d model", "3d mesh", "model asset", "mesh asset", "character model", "object model", "entity model")):
        return "entity_model", None
    if any(x in value for x in ("texture", "material", "surface")):
        return "entity_texture", "base_color"
    if any(x in value for x in ("player image", "player visual", "player avatar", "character image", "character visual")):
        return "entity_visual", None
    if any(x in value for x in ("visual", "image on", "image for", "sprite for", "avatar for")):
        return "entity_visual", None
    return None, None


def _required_kinds(target: str | None) -> set[str]:
    if target in {"world_background", "entity_visual", "entity_texture"}:
        return {"image"}
    if target in {"soundtrack", "entity_audio"}:
        return {"music", "audio"}
    if target == "cutscene":
        return {"video"}
    if target == "entity_model":
        return {"model"}
    return {"image", "video", "audio", "music", "model"}


def _label_matches(records: list[Any], command: str) -> list[Any]:
    command_norm = _norm(command)
    direct = [row for row in records if str(row.id).casefold() in command.casefold()]
    if direct:
        return direct
    labelled = []
    for row in records:
        label = _norm(row.label)
        if label and label in command_norm:
            labelled.append(row)
    if labelled:
        longest = max(len(_norm(row.label)) for row in labelled)
        return [row for row in labelled if len(_norm(row.label)) == longest]
    quoted = [x.strip() for x in re.findall(r"[\"']([^\"']{2,160})[\"']", command)]
    if quoted:
        hits = []
        for row in records:
            label = _norm(row.label)
            if any(_norm(q) == label or _norm(q) in label or label in _norm(q) for q in quoted):
                hits.append(row)
        if hits:
            return hits
    return []


def _asset_candidates(game_id: str, command: str, target: str | None) -> list[Any]:
    if target == "entity_model":
        return _label_matches(list(list_game_models(game_id)), command)
    records = [row for row in list_game_assets(game_id) if row.kind in _required_kinds(target)]
    return _label_matches(records, command)


def _available_candidates(game_id: str, target: str | None) -> list[dict]:
    if target == "entity_model":
        return [_model_view(row) for row in list_game_models(game_id)]
    return [_asset_view(row) for row in list_game_assets(game_id) if row.kind in _required_kinds(target)]


def _candidate_view(record: Any) -> dict:
    return _model_view(record) if isinstance(record, GameModelAssetRecord) else _asset_view(record)


def _entity_candidates(game: GameDNA, command: str) -> list[dict]:
    world = ensure_world(game)
    value = _norm(command)
    direct = [row for row in world.entities if row.id.casefold() in command.casefold()]
    if direct:
        return [{"id": row.id, "name": row.name, "kind": row.kind} for row in direct]
    if "player" in value:
        players = [row for row in world.entities if row.id == "player" or row.kind == "player"]
        if players:
            return [{"id": row.id, "name": row.name, "kind": row.kind} for row in players]
    hits = []
    for row in world.entities:
        label = _norm(row.name)
        if label and label in value:
            hits.append(row)
    return [{"id": row.id, "name": row.name, "kind": row.kind} for row in hits]


def _inspect(game: GameDNA, *, message: str) -> GameAuraCommandResult:
    return GameAuraCommandResult(
        action="inspect",
        message=message,
        game_id=game.id,
        bindings=_combined_bindings(game.id),
        assets=_all_asset_views(game.id),
    )


def execute_game_aura_command(game: GameDNA, command: str) -> GameAuraCommandResult:
    text = str(command or "").strip()
    value = _norm(text)
    if not text:
        raise ValueError("Aura needs a Game Forge instruction")

    if any(x in value for x in ("show bindings", "list bindings", "what is bound", "current bindings", "show game media", "show game models")):
        return _inspect(game, message="Here are the current Game Forge media/model bindings and imported assets.")
    if any(x in value for x in ("show assets", "list assets", "show imported media", "list imported media", "list models", "show models")):
        return _inspect(game, message="Here are the media and 3D model assets currently imported into this game.")

    target, material_slot = _target_from_text(text)
    remove = any(x in value for x in ("remove ", "clear ", "unbind ", "stop using ", "detach "))
    if target is None:
        return GameAuraCommandResult(
            action="clarify",
            message="Tell Aura what the asset should control: world background, soundtrack, cutscene, entity visual/material, spatial audio, or a 3D entity model.",
            game_id=game.id,
            needs_clarification=True,
            bindings=_combined_bindings(game.id),
            assets=_all_asset_views(game.id),
        )

    entity_id = None
    entity_candidates: list[dict] = []
    if target in {"entity_visual", "entity_texture", "entity_audio", "entity_model"}:
        entity_candidates = _entity_candidates(game, text)
        if len(entity_candidates) != 1:
            return GameAuraCommandResult(
                action="clarify",
                message=(
                    "I found more than one matching world entity. Choose the exact entity."
                    if len(entity_candidates) > 1
                    else "Tell Aura which world entity should receive this asset."
                ),
                game_id=game.id,
                needs_clarification=True,
                bindings=_combined_bindings(game.id),
                candidates=entity_candidates or model_binding_state(game.id).get("available_entities", []) or binding_state(game.id).get("available_entities", []),
                parsed={"target": target, "material_slot": material_slot},
            )
        entity_id = entity_candidates[0]["id"]

    if remove:
        if target == "entity_model":
            state = unbind_game_model(game, entity_id=str(entity_id))
        else:
            state = unbind_game_asset(
                game,
                UnbindGameAssetRequest(
                    target=target,
                    entity_id=entity_id,
                    material_slot=material_slot or "base_color",
                ),
            )
        return GameAuraCommandResult(
            action="unbind",
            message=f"Aura removed the {target.replace('_', ' ')} binding.",
            game_id=game.id,
            changed=True,
            bindings={**_combined_bindings(game.id), "last_change": state},
            parsed={"target": target, "entity_id": entity_id, "material_slot": material_slot},
        )

    matches = _asset_candidates(game.id, text, target)
    if len(matches) != 1:
        return GameAuraCommandResult(
            action="clarify",
            message=(
                "More than one imported asset matches that instruction. Choose the exact asset."
                if len(matches) > 1
                else (
                    "I need the exact imported 3D model. Upload a GLB or embedded glTF to this game first."
                    if target == "entity_model"
                    else "I need the exact imported asset to use. Import it first if it is still only in the Creative Library."
                )
            ),
            game_id=game.id,
            needs_clarification=True,
            bindings=_combined_bindings(game.id),
            candidates=[_candidate_view(row) for row in matches] or _available_candidates(game.id, target),
            parsed={"target": target, "entity_id": entity_id, "material_slot": material_slot},
        )

    asset = matches[0]
    if target == "entity_model":
        state = bind_game_model(game, model_id=asset.id, entity_id=str(entity_id))
    else:
        state = bind_game_asset(
            game,
            BindGameAssetRequest(
                asset_id=asset.id,
                target=target,
                entity_id=entity_id,
                material_slot=material_slot or "base_color",
            ),
        )
    return GameAuraCommandResult(
        action="bind",
        message=f"Aura set '{asset.label}' as the {target.replace('_', ' ')}.",
        game_id=game.id,
        changed=True,
        bindings={**_combined_bindings(game.id), "last_change": state},
        parsed={
            "asset_id": asset.id,
            "asset_label": asset.label,
            "target": target,
            "entity_id": entity_id,
            "material_slot": material_slot,
        },
    )


@router.post("/api/game-forge/games/{game_id}/aura-command")
def aura_game_command(game_id: str, body: GameAuraCommandRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    if not game.actively_editable:
        raise HTTPException(409, "Reopen this game before asking Aura to change its asset bindings")
    try:
        return execute_game_aura_command(game, body.command).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(404, "Game asset, model or entity not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = [
    "router",
    "GameAuraCommandRequest",
    "GameAuraCommandResult",
    "execute_game_aura_command",
]
