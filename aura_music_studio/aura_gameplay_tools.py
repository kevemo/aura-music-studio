from __future__ import annotations

import re
from typing import Any

from . import aura_agent_tools as tools
from .game_forge_gameplay import (
    ApplyGameplayPresetRequest,
    CreateGameplayEntityRequest,
    UpdateGameplayEntityRequest,
    apply_gameplay_preset,
    create_gameplay_entity,
    delete_gameplay_entity,
    gameplay_state,
    update_gameplay_entity,
)
from .game_forge_gameplay_runtime import build_gameplay_playtest
from .game_forge_store import load_game
from .game_forge_world import Vec3
from .plans import GAME_CREATE

_INSTALLED = False
_TOOL_NAMES = {
    "inspect_game_gameplay",
    "create_gameplay_entity",
    "update_gameplay_entity",
    "apply_gameplay_preset",
    "delete_gameplay_entity",
    "build_gameplay_playtest",
}
_WRITE_NAMES = _TOOL_NAMES - {"inspect_game_gameplay"}
_PRESETS = {"collectible", "hazard", "checkpoint", "moving_platform", "trigger", "npc_patrol"}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec(
            "inspect_game_gameplay",
            "Inspect one Game Forge project's declarative gameplay World DNA: entities, transforms, Physics DNA, safe behavior nodes and supported no-code presets.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "create_gameplay_entity",
            "Create a safe declarative gameplay entity in World DNA. Presets are collectible, hazard, checkpoint, moving_platform, trigger and npc_patrol. No JavaScript/Python/source code is accepted or generated.",
            {
                "game_id": "Stable Game Forge id.",
                "name": "Creator-facing entity name.",
                "preset": "collectible, hazard, checkpoint, moving_platform, trigger or npc_patrol.",
                "x": "World X position.", "y": "World Y position.", "z": "World Z position.",
                "scale_x": "Optional X scale.", "scale_y": "Optional Y scale.", "scale_z": "Optional Z scale.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "update_gameplay_entity",
            "Move/rename/resize or toggle a Game Forge gameplay entity using closed World DNA fields.",
            {
                "game_id": "Stable Game Forge id.", "entity_id": "Exact World DNA entity id.",
                "name": "Optional new name.", "x": "Optional world X.", "y": "Optional world Y.", "z": "Optional world Z.",
                "scale_x": "Optional X scale.", "scale_y": "Optional Y scale.", "scale_z": "Optional Z scale.",
                "active": "Optional active state.", "visible": "Optional visible state.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "apply_gameplay_preset",
            "Convert a non-core World DNA entity to a safe gameplay preset without executing creator code.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact World DNA entity id.", "preset": "collectible, hazard, checkpoint, moving_platform, trigger or npc_patrol."},
            write=True,
        ),
        tools.ToolSpec(
            "delete_gameplay_entity",
            "Delete a non-core gameplay entity from World DNA. Player and camera are protected.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact World DNA entity id."},
            write=True,
        ),
        tools.ToolSpec(
            "build_gameplay_playtest",
            "Build the current private Game Forge project with the gameplay-aware Aura2D/Aura3D runtime. Executes only server-sanitized declarative behavior DNA, with no runtime network access and no generated game source execution.",
            {"game_id": "Stable Game Forge id."},
            write=True,
        ),
    ]
    existing = {item.name for item in tools.TOOL_SPECS}
    for spec in specs:
        if spec.name not in existing:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec


def _tokens(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip() + " "


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    haystack = _tokens(text)
    return any(f" {re.sub(r'[^a-z0-9]+', ' ', phrase.casefold()).strip()} " in haystack for phrase in phrases)


def _explicit_write_allowed(name: str, latest: str) -> bool:
    if name == "build_gameplay_playtest":
        return _contains(latest, ("build", "playtest", "play test", "test game", "run game"))
    if name == "delete_gameplay_entity":
        return _contains(latest, ("delete", "remove")) and _contains(latest, ("game", "entity", "hazard", "collectible", "checkpoint", "trigger", "platform", "npc"))
    if name == "apply_gameplay_preset":
        return _contains(latest, ("make", "turn", "convert", "apply", "set", "change")) and _contains(latest, tuple(_PRESETS))
    if name == "create_gameplay_entity":
        return _contains(latest, ("add", "create", "make", "place", "spawn")) and _contains(latest, tuple(_PRESETS))
    if name == "update_gameplay_entity":
        return _contains(latest, ("move", "rename", "resize", "scale", "hide", "show", "activate", "deactivate", "update", "change"))
    return True


def _float(args: dict, key: str, default: float) -> float:
    value = args.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(args: dict, key: str) -> float | None:
    if key not in args or args.get(key) is None:
        return None
    return _float(args, key, 0.0)


def _require_editing(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("Gameplay authoring unlocks on the Basic £4.99 tier")


def install_aura_gameplay_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_specs()
    original_execute = tools.AuraToolRegistry.execute

    def execute(self, call: tools.ToolCall, *, latest_user_message: str) -> Any:
        if call.name not in _TOOL_NAMES:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        if call.name in _WRITE_NAMES and not _explicit_write_allowed(call.name, latest_user_message):
            raise PermissionError("Aura gameplay-changing tools require an explicit matching Game Forge request in the member's latest message")
        args = dict(call.arguments or {})
        game_id = str(args.get("game_id") or "")
        game = load_game(game_id)
        if call.name == "inspect_game_gameplay":
            return gameplay_state(game.id)
        _require_editing(self.member)
        if call.name == "create_gameplay_entity":
            preset = str(args.get("preset") or "")
            if preset not in _PRESETS:
                raise ValueError("Unknown gameplay preset")
            entity = create_gameplay_entity(
                game,
                CreateGameplayEntityRequest(
                    name=str(args.get("name") or preset.replace("_", " ").title())[:160],
                    preset=preset,
                    position=Vec3(x=_float(args, "x", 0), y=_float(args, "y", 0), z=_float(args, "z", 0)),
                    scale=Vec3(x=_float(args, "scale_x", 1), y=_float(args, "scale_y", 1), z=_float(args, "scale_z", 1)),
                ),
            )
            return {"changed": True, "entity": entity.model_dump(mode="json"), "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}
        if call.name == "update_gameplay_entity":
            current = next((row for row in gameplay_state(game.id)["entities"] if row["id"] == str(args.get("entity_id") or "")), None)
            if current is None:
                raise ValueError("World entity not found")
            current_pos = current["transform"]["position"]
            current_scale = current["transform"]["scale"]
            pos_changed = any(key in args for key in ("x", "y", "z"))
            scale_changed = any(key in args for key in ("scale_x", "scale_y", "scale_z"))
            body = UpdateGameplayEntityRequest(
                name=(str(args["name"])[:160] if args.get("name") is not None else None),
                position=(Vec3(x=_float(args, "x", current_pos["x"]), y=_float(args, "y", current_pos["y"]), z=_float(args, "z", current_pos["z"])) if pos_changed else None),
                scale=(Vec3(x=_float(args, "scale_x", current_scale["x"]), y=_float(args, "scale_y", current_scale["y"]), z=_float(args, "scale_z", current_scale["z"])) if scale_changed else None),
                active=(bool(args["active"]) if isinstance(args.get("active"), bool) else None),
                visible=(bool(args["visible"]) if isinstance(args.get("visible"), bool) else None),
            )
            entity = update_gameplay_entity(game, str(args.get("entity_id") or ""), body)
            return {"changed": True, "entity": entity.model_dump(mode="json"), "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}
        if call.name == "apply_gameplay_preset":
            preset = str(args.get("preset") or "")
            if preset not in _PRESETS:
                raise ValueError("Unknown gameplay preset")
            entity = apply_gameplay_preset(game, str(args.get("entity_id") or ""), preset)
            return {"changed": True, "entity": entity.model_dump(mode="json"), "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}
        if call.name == "delete_gameplay_entity":
            delete_gameplay_entity(game, str(args.get("entity_id") or ""))
            return {"changed": True, "gameplay": gameplay_state(game.id), "invalidated_previous_build_and_rating": True}
        if call.name == "build_gameplay_playtest":
            game, _html = build_gameplay_playtest(game)
            return {
                "changed": True,
                "private_playtest_url": f"/game-creation/play/{game.id}",
                "runtime": game.latest_build.runtime if game.latest_build else None,
                "gameplay_runtime": True,
                "generated_game_code_executed": False,
                "network_access_enabled": False,
            }
        raise ValueError("Unsupported Aura gameplay tool")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_gameplay_tools"]
