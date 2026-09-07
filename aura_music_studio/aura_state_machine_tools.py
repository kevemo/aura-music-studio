from __future__ import annotations

import re
from typing import Any

from . import aura_agent_tools as tools
from .game_forge_export_portal import router as game_export_portal_router
from .game_forge_state_machine import (
    CreateStateMachineEntityRequest,
    ReplaceStateMachineRequest,
    StateMachineDNA,
    StateOffsetDNA,
    create_state_machine_entity,
    delete_state_machine_entity,
    replace_state_machine,
    router as game_state_machine_router,
    state_machine_state,
)
from .game_forge_state_machine_runtime import build_state_machine_playtest
from .game_forge_store import load_game
from .plans import GAME_CREATE

_INSTALLED = False
_TOOL_NAMES = {
    "inspect_game_state_machines",
    "create_game_state_machine",
    "replace_game_state_machine",
    "delete_game_state_machine",
    "build_state_machine_playtest",
}
_WRITE_NAMES = _TOOL_NAMES - {"inspect_game_state_machines"}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec(
            "inspect_game_state_machines",
            "Inspect typed Game Forge State Machines, declared Adventure flags and hard runtime limits.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "create_game_state_machine",
            "Create a dedicated typed State Machine actor. States and transitions are structured data only; no expressions or callbacks are accepted.",
            {
                "game_id": "Stable Game Forge id.",
                "name": "Actor name.",
                "x": "World X position.", "y": "World Y position.", "z": "World Z position.",
                "machine": "StateMachineDNA object with initial_state, states and transitions.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "replace_game_state_machine",
            "Replace the typed machine graph on one non-core, non-conflicting World entity.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact World entity id.", "machine": "Validated StateMachineDNA object."},
            write=True,
        ),
        tools.ToolSpec(
            "delete_game_state_machine",
            "Delete one dedicated Aura State Machine actor.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact State Machine actor id."},
            write=True,
        ),
        tools.ToolSpec(
            "build_state_machine_playtest",
            "Build the cumulative Aura runtime with State Machines over World Events, World Logic, Adventure and gameplay/physics.",
            {"game_id": "Stable Game Forge id."},
            write=True,
        ),
    ]
    existing = {row.name for row in tools.TOOL_SPECS}
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
    terms = ("state machine", "state graph", "transition", "states")
    if name == "build_state_machine_playtest":
        return _contains(latest, ("build", "playtest", "play test", "run game", "test game"))
    if name == "delete_game_state_machine":
        return _contains(latest, ("delete", "remove")) and _contains(latest, terms)
    if name == "create_game_state_machine":
        return _contains(latest, ("add", "create", "make")) and _contains(latest, terms)
    if name == "replace_game_state_machine":
        return _contains(latest, ("replace", "update", "change", "edit")) and _contains(latest, terms)
    return True


def _require_editing(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("State Machine authoring unlocks on the Basic £4.99 tier")


def _float(args: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return default


def _machine(args: dict) -> StateMachineDNA:
    raw = args.get("machine")
    if not isinstance(raw, dict):
        raise ValueError("machine must be a structured StateMachineDNA object")
    return StateMachineDNA.model_validate(raw)


def install_aura_state_machine_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # game_state_machine_router is already an owned Game Forge composition boundary. Mounting the
    # export surface here keeps both API and creator UI reachable without editing shared app.py.
    game_state_machine_router.include_router(game_export_portal_router)
    _install_specs()
    original_execute = tools.AuraToolRegistry.execute

    def execute(self, call: tools.ToolCall, *, latest_user_message: str) -> Any:
        if call.name not in _TOOL_NAMES:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        if call.name in _WRITE_NAMES and not _explicit_write_allowed(call.name, latest_user_message):
            raise PermissionError("Aura State Machine-changing tools require an explicit matching Game Forge request in the member's latest message")
        args = dict(call.arguments or {})
        game = load_game(str(args.get("game_id") or ""))
        if call.name == "inspect_game_state_machines":
            return state_machine_state(game.id)
        _require_editing(self.member)
        if call.name == "create_game_state_machine":
            row = create_state_machine_entity(
                game,
                CreateStateMachineEntityRequest(
                    name=str(args.get("name") or "State Actor")[:160],
                    position=StateOffsetDNA(x=_float(args, "x"), y=_float(args, "y"), z=_float(args, "z")),
                    machine=_machine(args),
                ),
            )
            return {"changed": True, "entity": row.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "replace_game_state_machine":
            body = ReplaceStateMachineRequest(machine=_machine(args))
            row = replace_state_machine(game, str(args.get("entity_id") or ""), body.machine)
            return {"changed": True, "entity": row.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "delete_game_state_machine":
            delete_state_machine_entity(game, str(args.get("entity_id") or ""))
            return {"changed": True, "deleted": True, "invalidated_previous_build_and_rating": True}
        if call.name == "build_state_machine_playtest":
            game, _html = build_state_machine_playtest(game)
            return {
                "changed": True,
                "private_playtest_url": f"/game-creation/play/{game.id}",
                "runtime": game.latest_build.runtime if game.latest_build else None,
                "state_machine_runtime": True,
                "max_transitions_per_frame": 1,
                "adventure_flags_only": True,
                "runtime_network_access": False,
                "arbitrary_creator_code": False,
            }
        raise ValueError("Unsupported Aura State Machine tool")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_state_machine_tools"]
