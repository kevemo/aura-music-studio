from __future__ import annotations

import re
from typing import Any

from . import aura_agent_tools as tools
from .game_forge_adventure import (
    CreateDialogueRequest,
    CreateGateRequest,
    CreateItemRequest,
    CreateObjectiveRequest,
    add_dialogue,
    add_gate,
    add_item,
    add_objective,
    adventure_runtime_payload,
    delete_adventure_row,
    ensure_adventure,
)
from .game_forge_adventure_runtime import build_adventure_playtest
from .game_forge_store import load_game
from .plans import GAME_CREATE

_INSTALLED = False
_TOOL_NAMES = {
    "inspect_game_adventure",
    "add_game_inventory_item",
    "add_game_objective",
    "add_game_dialogue",
    "add_game_gate",
    "delete_game_adventure_entry",
    "build_adventure_playtest",
}
_WRITE_NAMES = _TOOL_NAMES - {"inspect_game_adventure"}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec(
            "inspect_game_adventure",
            "Inspect one Game Forge project's typed Adventure State DNA: inventory definitions, objectives, dialogue, gates and browser-local save policy.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "add_game_inventory_item",
            "Add a typed inventory item definition to Adventure State DNA. No executable creator code is accepted.",
            {"game_id": "Stable Game Forge id.", "name": "Item name.", "description": "Optional description.", "max_stack": "1-9999.", "consumable": "Boolean."},
            write=True,
        ),
        tools.ToolSpec(
            "add_game_objective",
            "Add an integrity-bound Adventure objective. Kinds: collect, reach, talk, flag or timer.",
            {
                "game_id": "Stable Game Forge id.", "title": "Objective title.", "description": "Optional description.",
                "kind": "collect, reach, talk, flag or timer.", "target_entity_id": "Required for collect/reach/talk.",
                "target_count": "Target count.", "flag": "Required for flag objectives.", "seconds": "Required for timer objectives.",
                "reward_item_id": "Optional defined item id.", "reward_quantity": "Optional reward quantity.", "completion_flag": "Optional flag set on completion.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "add_game_dialogue",
            "Add safe dialogue bound to an exact trigger entity. Dialogue strings are displayed as text, never interpreted as HTML/code.",
            {"game_id": "Stable Game Forge id.", "trigger_entity_id": "Exact World DNA entity id.", "speaker": "Speaker name.", "lines": "List of dialogue lines.", "completion_flag": "Optional completion flag.", "once": "Boolean."},
            write=True,
        ),
        tools.ToolSpec(
            "add_game_gate",
            "Add a conditional Adventure gate/door using a required flag or inventory item.",
            {"game_id": "Stable Game Forge id.", "trigger_entity_id": "Exact trigger entity id.", "door_entity_id": "Optional door entity id.", "label": "Gate label.", "requires_flag": "Optional required flag.", "requires_item_id": "Optional required item id.", "consume_item": "Boolean.", "open_flag": "Optional flag set when opened."},
            write=True,
        ),
        tools.ToolSpec(
            "delete_game_adventure_entry",
            "Delete one Adventure State item, objective, dialogue or gate. Referenced items remain protected by DNA validation.",
            {"game_id": "Stable Game Forge id.", "collection": "items, objectives, dialogues or gates.", "row_id": "Exact Adventure State id."},
            write=True,
        ),
        tools.ToolSpec(
            "build_adventure_playtest",
            "Build the private Aura playtest with gameplay plus Adventure State objectives, inventory, dialogue, gates and browser-local content-hash-versioned saves.",
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
    if name == "build_adventure_playtest":
        return _contains(latest, ("build", "playtest", "play test", "run game", "test game"))
    if name == "delete_game_adventure_entry":
        return _contains(latest, ("delete", "remove")) and _contains(latest, ("item", "objective", "quest", "dialogue", "gate", "door"))
    if name == "add_game_inventory_item":
        return _contains(latest, ("add", "create", "make", "define")) and _contains(latest, ("item", "inventory"))
    if name == "add_game_objective":
        return _contains(latest, ("add", "create", "make", "define")) and _contains(latest, ("objective", "quest", "mission"))
    if name == "add_game_dialogue":
        return _contains(latest, ("add", "create", "make", "write")) and _contains(latest, ("dialogue", "conversation", "talk"))
    if name == "add_game_gate":
        return _contains(latest, ("add", "create", "make", "lock")) and _contains(latest, ("gate", "door", "lock"))
    return True


def _require_editing(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("Adventure State authoring unlocks on the Basic £4.99 tier")


def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _float_or_none(args: dict, key: str) -> float | None:
    if args.get(key) is None:
        return None
    try:
        return float(args.get(key))
    except (TypeError, ValueError):
        return None


def install_aura_adventure_tools() -> None:
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
            raise PermissionError("Aura Adventure-changing tools require an explicit matching Game Forge request in the member's latest message")
        args = dict(call.arguments or {})
        game = load_game(str(args.get("game_id") or ""))
        if call.name == "inspect_game_adventure":
            state = ensure_adventure(game)
            return {"adventure": state.model_dump(mode="json"), "runtime": adventure_runtime_payload(game), "server_save_sync": False}
        _require_editing(self.member)
        if call.name == "add_game_inventory_item":
            row = add_item(game, CreateItemRequest(name=str(args.get("name") or "Item")[:120], description=str(args.get("description") or "")[:600], max_stack=_int(args, "max_stack", 99, 1, 9999), consumable=bool(args.get("consumable", False))))
            return {"changed": True, "item": row.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "add_game_objective":
            row = add_objective(game, CreateObjectiveRequest(
                title=str(args.get("title") or "Objective")[:160], description=str(args.get("description") or "")[:800], kind=str(args.get("kind") or "reach"),
                target_entity_id=(str(args.get("target_entity_id"))[:160] if args.get("target_entity_id") else None), target_count=_int(args, "target_count", 1, 1, 1_000_000),
                flag=(str(args.get("flag"))[:120] if args.get("flag") else None), seconds=_float_or_none(args, "seconds"),
                reward_item_id=(str(args.get("reward_item_id"))[:120] if args.get("reward_item_id") else None), reward_quantity=_int(args, "reward_quantity", 1, 1, 9999),
                completion_flag=(str(args.get("completion_flag"))[:120] if args.get("completion_flag") else None),
            ))
            return {"changed": True, "objective": row.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "add_game_dialogue":
            raw_lines = args.get("lines") or []
            if isinstance(raw_lines, str):
                raw_lines = [raw_lines]
            lines = [str(x)[:400] for x in list(raw_lines)[:20] if str(x).strip()]
            row = add_dialogue(game, CreateDialogueRequest(
                trigger_entity_id=str(args.get("trigger_entity_id") or "")[:160], speaker=str(args.get("speaker") or "Character")[:120],
                lines=lines, choices=[], once=bool(args.get("once", True)), completion_flag=(str(args.get("completion_flag"))[:120] if args.get("completion_flag") else None),
            ))
            return {"changed": True, "dialogue": row.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "add_game_gate":
            row = add_gate(game, CreateGateRequest(
                trigger_entity_id=str(args.get("trigger_entity_id") or "")[:160], door_entity_id=(str(args.get("door_entity_id"))[:160] if args.get("door_entity_id") else None),
                label=str(args.get("label") or "Gate")[:120], requires_flag=(str(args.get("requires_flag"))[:120] if args.get("requires_flag") else None),
                requires_item_id=(str(args.get("requires_item_id"))[:120] if args.get("requires_item_id") else None), consume_item=bool(args.get("consume_item", False)),
                open_flag=(str(args.get("open_flag"))[:120] if args.get("open_flag") else None),
            ))
            return {"changed": True, "gate": row.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "delete_game_adventure_entry":
            delete_adventure_row(game, str(args.get("collection") or ""), str(args.get("row_id") or ""))
            return {"changed": True, "deleted": True, "invalidated_previous_build_and_rating": True}
        if call.name == "build_adventure_playtest":
            game, _html = build_adventure_playtest(game)
            return {"changed": True, "private_playtest_url": f"/game-creation/play/{game.id}", "runtime": game.latest_build.runtime if game.latest_build else None, "adventure_runtime": True, "browser_local_save": True, "server_save_sync": False, "network_access_enabled": False}
        raise ValueError("Unsupported Aura Adventure tool")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_adventure_tools"]
