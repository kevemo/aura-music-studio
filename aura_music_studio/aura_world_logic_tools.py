from __future__ import annotations

import re
from typing import Any

from . import aura_agent_tools as tools
from .game_forge_store import load_game
from .game_forge_world_logic import (
    ApplyWorldLogicPresetRequest,
    CreateWorldLogicEntityRequest,
    apply_world_logic_preset,
    create_world_logic_entity,
    world_logic_state,
)
from .game_forge_world_logic_runtime import build_world_logic_playtest
from .game_forge_world import Vec3
from .plans import GAME_CREATE

_INSTALLED = False
_TOOL_NAMES = {
    "inspect_game_world_logic",
    "create_game_world_logic_entity",
    "apply_game_world_logic_preset",
    "build_world_logic_playtest",
}
_WRITE_NAMES = _TOOL_NAMES - {"inspect_game_world_logic"}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec(
            "inspect_game_world_logic",
            "Inspect one Game Forge project's safe Advanced World Logic DNA: NPC follow/escort, timed triggers and automatic doors.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "create_game_world_logic_entity",
            "Create a typed Advanced World Logic entity using a safe preset. Presets: npc_follow, timed_trigger or auto_door.",
            {
                "game_id": "Stable Game Forge id.",
                "name": "Entity name.",
                "preset": "npc_follow, timed_trigger or auto_door.",
                "x": "World X position.",
                "y": "World Y position.",
                "z": "World Z position.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "apply_game_world_logic_preset",
            "Replace one non-core World DNA entity's behavior/physics with a safe Advanced World Logic preset.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact World DNA entity id.", "preset": "npc_follow, timed_trigger or auto_door."},
            write=True,
        ),
        tools.ToolSpec(
            "build_world_logic_playtest",
            "Build the cumulative Aura private playtest with gameplay, Adventure State and Advanced World Logic enabled.",
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
    return any(f" {re.sub(r'[^a-z0-9]+', ' ', p.casefold()).strip()} " in haystack for p in phrases)


def _explicit_write_allowed(name: str, latest: str) -> bool:
    if name == "build_world_logic_playtest":
        return _contains(latest, ("build", "playtest", "play test", "run game", "test game"))
    if name == "create_game_world_logic_entity":
        return _contains(latest, ("add", "create", "make")) and _contains(latest, ("follower", "escort", "timer", "trigger", "door", "world logic"))
    if name == "apply_game_world_logic_preset":
        return _contains(latest, ("apply", "make", "turn", "convert", "set")) and _contains(latest, ("follower", "escort", "timer", "trigger", "door", "world logic"))
    return True


def _require_editing(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("Advanced World Logic authoring unlocks on the Basic £4.99 tier")


def _float(args: dict, key: str, default: float) -> float:
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return default


def install_aura_world_logic_tools() -> None:
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
            raise PermissionError("Aura World Logic-changing tools require an explicit matching Game Forge request in the member's latest message")
        args = dict(call.arguments or {})
        game = load_game(str(args.get("game_id") or ""))
        if call.name == "inspect_game_world_logic":
            return world_logic_state(game.id)
        _require_editing(self.member)
        if call.name == "create_game_world_logic_entity":
            body = CreateWorldLogicEntityRequest(
                name=str(args.get("name") or "World Logic Entity")[:160],
                preset=str(args.get("preset") or "npc_follow"),
                position=Vec3(x=_float(args, "x", 0.0), y=_float(args, "y", 0.0), z=_float(args, "z", 0.0)),
            )
            entity = create_world_logic_entity(game, body)
            return {"changed": True, "entity": entity.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "apply_game_world_logic_preset":
            body = ApplyWorldLogicPresetRequest(preset=str(args.get("preset") or "npc_follow"))
            entity = apply_world_logic_preset(game, str(args.get("entity_id") or ""), body.preset)
            return {"changed": True, "entity": entity.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "build_world_logic_playtest":
            game, _html = build_world_logic_playtest(game)
            return {
                "changed": True,
                "private_playtest_url": f"/game-creation/play/{game.id}",
                "runtime": game.latest_build.runtime if game.latest_build else None,
                "world_logic_runtime": True,
                "runtime_network_access": False,
                "arbitrary_creator_code": False,
            }
        raise ValueError("Unsupported Aura World Logic tool")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_world_logic_tools"]
