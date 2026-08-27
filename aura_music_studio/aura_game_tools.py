from __future__ import annotations

import re
from typing import Any

from . import aura_agent_tools as tools
from .game_forge_api import CreateGameRequest, create_game_for_member
from .game_forge_asset_bindings import (
    BindGameAssetRequest,
    UnbindGameAssetRequest,
    bind_game_asset,
    binding_state,
    unbind_game_asset,
)
from .game_forge_assets import list_game_assets, public_asset
from .game_forge_aura_commands import execute_game_aura_command
from .game_forge_integrity import assess_game_integrity
from .game_forge_model_assets import list_game_models
from .game_forge_model_bindings import bind_game_model, model_binding_state, unbind_game_model
from .game_forge_runtime import build_private_playtest
from .game_forge_store import list_games, load_game, save_game
from .plans import GAME_CREATE

_INSTALLED = False
_GAME_TOOL_NAMES = {
    "list_game_projects",
    "inspect_game_project",
    "create_game_project",
    "scan_game_project",
    "build_game_playtest",
    "list_game_media_assets",
    "inspect_game_asset_bindings",
    "bind_game_media_asset",
    "unbind_game_media_asset",
    "list_game_models",
    "inspect_game_model_bindings",
    "bind_game_model_asset",
    "unbind_game_model_asset",
    "apply_game_media_command",
}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec("list_game_projects", "List the signed-in member's private Aura Game Forge projects and current build/rating state.", {}),
        tools.ToolSpec(
            "inspect_game_project",
            "Read one private Aura-owned engine-independent Game DNA project, including mechanics, content disclosures, build, provisional rating, imported creative media, imported 3D models and current World DNA bindings.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "create_game_project",
            "Create a new private Aura Game DNA workspace. Aura Game Engine 2D/3D are the native defaults; external engines are optional export targets. Basic allows one active editable game at a time; Pro allows unlimited active projects.",
            {
                "title": "Game title.", "prompt": "Detailed game description.", "genre": "Genre/niche.", "dimension": "2d or 3d.",
                "engine_target": "Optional aura2d or aura3d native target, or phaser4/playcanvas/babylon/godot export adapter. Omit to use Aura native automatically.",
                "synopsis": "Optional story/synopsis.", "art_direction": "Optional graphics direction.", "audio_direction": "Optional music/voice/SFX direction.",
                "rights_confirmed": "True only when the member explicitly confirms they own/have permission for requested source material.",
            }, write=True,
        ),
        tools.ToolSpec(
            "scan_game_project",
            "Run Aura/Pulsar's provisional content/rating/privacy/monetisation preflight for current Game DNA, World DNA, imported media/model integrity and all asset bindings. This never issues an official ESRB/PEGI/IARC or authority rating.",
            {"game_id": "Stable Game Forge id."}, write=True,
        ),
        tools.ToolSpec(
            "build_game_playtest",
            "Build the current private Game DNA into Aura's isolated no-network browser playtest runtime, including verified media and server-validated static 3D model geometry. Arbitrary generated server code is never executed in the API host.",
            {"game_id": "Stable Game Forge id."}, write=True,
        ),
        tools.ToolSpec(
            "list_game_media_assets",
            "List verified image, video, audio and music snapshots already imported into one private Game Forge project. Does not expose tenant filesystem paths.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "inspect_game_asset_bindings",
            "Inspect exact World DNA media assignments: world background, soundtrack, cutscene, entity visuals, entity spatial audio and per-entity PBR material texture slots.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "bind_game_media_asset",
            "Assign one already-imported verified media asset to an exact World DNA target. Use image assets for background/entity visuals/PBR textures, music/audio for soundtrack or entity_audio spatial sources, and video for cutscene.",
            {
                "game_id": "Stable Game Forge id.",
                "asset_id": "Imported Game Forge media asset id.",
                "target": "world_background, soundtrack, cutscene, entity_visual, entity_texture or entity_audio.",
                "entity_id": "Required for entity_visual/entity_texture/entity_audio.",
                "material_slot": "For entity_texture: base_color, normal, metallic, roughness, emissive, opacity, height or ao.",
            }, write=True,
        ),
        tools.ToolSpec(
            "unbind_game_media_asset",
            "Remove one exact World DNA media assignment without deleting the underlying imported asset.",
            {
                "game_id": "Stable Game Forge id.",
                "target": "world_background, soundtrack, cutscene, entity_visual, entity_texture or entity_audio.",
                "entity_id": "Required for entity_visual/entity_texture/entity_audio.",
                "material_slot": "For entity_texture; defaults to base_color.",
            }, write=True,
        ),
        tools.ToolSpec(
            "list_game_models",
            "List verified GLB/embedded-glTF static model snapshots imported directly into one private Aura3D game. Raw model filesystem paths and browser URLs are never exposed.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "inspect_game_model_bindings",
            "Inspect exact World DNA entity-to-3D-model assignments and the renderable entities/models available for binding.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "bind_game_model_asset",
            "Assign one verified imported static 3D model to one exact renderable World DNA entity.",
            {"game_id": "Stable Game Forge id.", "model_id": "Imported Game Forge model id.", "entity_id": "Exact renderable World DNA entity id."},
            write=True,
        ),
        tools.ToolSpec(
            "unbind_game_model_asset",
            "Remove the static 3D model assignment from one exact World DNA entity without deleting the model.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact renderable World DNA entity id."},
            write=True,
        ),
        tools.ToolSpec(
            "apply_game_media_command",
            "Apply a plain-language Game Forge asset instruction such as 'use Cosmic Sky as the world background', 'set Sparkles as the soundtrack', 'apply Neon Stone to ground base color', 'use Waterfall Roar as spatial audio on Waterfall', or 'use Dragon Knight as the 3D model on Player'. Fails safely with candidates when an asset/entity is ambiguous.",
            {"game_id": "Stable Game Forge id.", "command": "Explicit member instruction for imported game media/model bindings."}, write=True,
        ),
    ]
    existing = {item.name for item in tools.TOOL_SPECS}
    for spec in specs:
        if spec.name not in existing:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec


def _has_intent_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """Match whole intent words/phrases rather than unsafe substrings."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()
    padded = f" {normalized} "
    for phrase in phrases:
        wanted = re.sub(r"[^a-z0-9]+", " ", phrase.casefold()).strip()
        if wanted and f" {wanted} " in padded:
            return True
    return False


def _explicit_game_write_allowed(name: str, message: str) -> bool:
    if name == "create_game_project":
        return _has_intent_phrase(message, ("create", "make", "build", "start", "generate")) and _has_intent_phrase(message, ("game",))
    if name == "scan_game_project":
        return _has_intent_phrase(message, ("scan", "rate", "rating", "check", "review")) and _has_intent_phrase(message, ("game",))
    if name == "build_game_playtest":
        return _has_intent_phrase(message, ("build", "playtest", "play test", "test", "generate")) and _has_intent_phrase(message, ("game",))
    asset_terms = ("game", "background", "soundtrack", "cutscene", "texture", "visual", "player", "asset", "media", "audio", "sound effect", "sfx", "model", "mesh", "3d model", "3d mesh")
    if name in {"bind_game_media_asset", "bind_game_model_asset"}:
        return _has_intent_phrase(message, ("use", "set", "apply", "bind", "assign", "change")) and _has_intent_phrase(message, asset_terms)
    if name in {"unbind_game_media_asset", "unbind_game_model_asset"}:
        return _has_intent_phrase(message, ("remove", "clear", "unbind", "detach", "stop using")) and _has_intent_phrase(message, asset_terms)
    if name == "apply_game_media_command":
        return _has_intent_phrase(message, ("use", "set", "apply", "bind", "assign", "change", "remove", "clear", "unbind", "show", "list")) and _has_intent_phrase(message, asset_terms)
    return True


def _summary(game) -> dict:
    return {
        "id": game.id, "title": game.title, "genre": game.genre, "dimension": game.dimension,
        "engine_target": game.engine_target, "status": game.status, "version": game.version,
        "rights_confirmed": game.rights_confirmed,
        "latest_build": game.latest_build.model_dump(mode="json") if game.latest_build else None,
        "rating_assessment": game.rating_assessment.model_dump(mode="json") if game.rating_assessment else None,
        "public_id": game.public_id, "private_storage_exposed": False,
    }


def _asset_summary(game_id: str) -> list[dict]:
    rows = []
    for record in list_game_assets(game_id):
        item = public_asset(record)
        rows.append({
            "id": item["id"], "label": item["label"], "kind": item["kind"], "role": item["role"],
            "rights_confirmed": item["rights_confirmed"], "media_url": item["media_url"], "filesystem_path_exposed": False,
        })
    return rows


def _model_summary(game_id: str) -> list[dict]:
    return [
        {
            "id": record.id,
            "label": record.label,
            "kind": "model",
            "role": record.role,
            "rights_confirmed": record.rights_confirmed,
            "mesh_summary": record.mesh_summary,
            "raw_model_browser_url": None,
            "filesystem_path_exposed": False,
        }
        for record in list_game_models(game_id)
    ]


def _game_create_allowed(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("Game editing unlocks on the Basic £4.99 tier")


def install_aura_game_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_specs()
    original_execute = tools.AuraToolRegistry.execute

    def execute(self, call: tools.ToolCall, *, latest_user_message: str) -> Any:
        if call.name not in _GAME_TOOL_NAMES:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        write_names = {
            "create_game_project", "scan_game_project", "build_game_playtest", "bind_game_media_asset",
            "unbind_game_media_asset", "bind_game_model_asset", "unbind_game_model_asset", "apply_game_media_command",
        }
        if call.name in write_names and not _explicit_game_write_allowed(call.name, latest_user_message):
            raise PermissionError("Aura game-changing tools require an explicit matching Game Forge request in the member's latest message")
        args = dict(call.arguments or {})
        if call.name == "list_game_projects":
            return [_summary(game) for game in list_games()]
        if call.name == "inspect_game_project":
            game = load_game(str(args.get("game_id") or ""))
            return {
                "game_dna": game.model_dump(mode="json"),
                "assets": _asset_summary(game.id),
                "models": _model_summary(game.id),
                "asset_bindings": binding_state(game.id),
                "model_bindings": model_binding_state(game.id),
            }
        if call.name == "create_game_project":
            dimension = str(args.get("dimension") or "2d")
            body = CreateGameRequest(
                title=str(args.get("title") or "Untitled Game")[:160], prompt=str(args.get("prompt") or "").strip(),
                genre=str(args.get("genre") or "adventure")[:120], dimension=dimension,
                engine_target=(str(args.get("engine_target")) if args.get("engine_target") else None),
                synopsis=str(args.get("synopsis") or "")[:8000], art_direction=str(args.get("art_direction") or "")[:4000],
                audio_direction=str(args.get("audio_direction") or "")[:4000], rights_confirmed=bool(args.get("rights_confirmed", False)),
                rights_attestation=("Member explicitly confirmed source-material rights in the Aura request." if bool(args.get("rights_confirmed", False)) else ""),
            )
            game = create_game_for_member(self.member, body)
            return {"game": _summary(game), "created": True, "public": False, "aura_native_engine": game.engine_target in {"aura2d", "aura3d"}}
        game = load_game(str(args.get("game_id") or ""))
        if call.name in write_names:
            _game_create_allowed(self.member)
        if call.name == "scan_game_project":
            assessment = assess_game_integrity(game)
            game.rating_assessment = assessment
            game.status = "approved_test" if assessment.public_test_allowed and game.latest_build and game.latest_build.content_hash == assessment.content_hash else "review_ready"
            save_game(game)
            return {"assessment": assessment.model_dump(mode="json"), "official_rating": False, "public_test_allowed": assessment.public_test_allowed, "integrity_bound_to_world_and_assets": True}
        if call.name == "build_game_playtest":
            game, _html = build_private_playtest(game)
            return {"game": _summary(game), "private_playtest_url": f"/game-creation/play/{game.id}", "runtime": game.latest_build.runtime if game.latest_build else "aura_game_runtime_v1", "arbitrary_server_code_executed": False, "network_access_enabled": False}
        if call.name == "list_game_media_assets":
            return {"game_id": game.id, "assets": _asset_summary(game.id), "filesystem_paths_exposed": False}
        if call.name == "inspect_game_asset_bindings":
            return binding_state(game.id)
        if call.name == "bind_game_media_asset":
            state = bind_game_asset(game, BindGameAssetRequest(
                asset_id=str(args.get("asset_id") or ""), target=str(args.get("target") or ""),
                entity_id=(str(args.get("entity_id")) if args.get("entity_id") else None), material_slot=str(args.get("material_slot") or "base_color"),
            ))
            return {"changed": True, "bindings": state, "invalidated_previous_build_and_rating": True}
        if call.name == "unbind_game_media_asset":
            state = unbind_game_asset(game, UnbindGameAssetRequest(
                target=str(args.get("target") or ""), entity_id=(str(args.get("entity_id")) if args.get("entity_id") else None),
                material_slot=str(args.get("material_slot") or "base_color"),
            ))
            return {"changed": True, "bindings": state, "invalidated_previous_build_and_rating": True}
        if call.name == "list_game_models":
            return {"game_id": game.id, "models": _model_summary(game.id), "filesystem_paths_exposed": False, "raw_model_browser_urls_exposed": False}
        if call.name == "inspect_game_model_bindings":
            return model_binding_state(game.id)
        if call.name == "bind_game_model_asset":
            state = bind_game_model(game, model_id=str(args.get("model_id") or ""), entity_id=str(args.get("entity_id") or ""))
            return {"changed": True, "bindings": state, "invalidated_previous_build_and_rating": True}
        if call.name == "unbind_game_model_asset":
            state = unbind_game_model(game, entity_id=str(args.get("entity_id") or ""))
            return {"changed": True, "bindings": state, "invalidated_previous_build_and_rating": True}
        if call.name == "apply_game_media_command":
            return execute_game_aura_command(game, str(args.get("command") or "")).model_dump(mode="json")
        raise ValueError(f"Aura game tool is not implemented: {call.name}")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_game_tools", "_explicit_game_write_allowed"]
