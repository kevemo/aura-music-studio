from __future__ import annotations

import re
from typing import Any

from . import aura_agent_tools as tools
from .game_forge_store import load_game
from .game_forge_world import Vec3
from .game_forge_world_events import (
    ApplyWorldEventPresetRequest,
    CreateWorldEventEntityRequest,
    apply_world_event_preset,
    create_world_event_entity,
    delete_world_event_entity,
    world_event_state,
)
from .game_forge_world_events_runtime import build_world_events_playtest
from .plans import GAME_CREATE

_INSTALLED = False
_TOOL_NAMES = {
    "inspect_game_world_events",
    "create_game_world_event",
    "apply_game_world_event_preset",
    "delete_game_world_event",
    "build_world_events_playtest",
}
_WRITE_NAMES = _TOOL_NAMES - {"inspect_game_world_events"}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec(
            "inspect_game_world_events",
            "Inspect one Game Forge project's safe World Events & Atmosphere DNA: spawn portals, verified-media audio zones and bounded particle emitters.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "create_game_world_event",
            "Create a typed World Event using a safe preset. Presets: spawn_portal, audio_zone or particle_emitter. Audio zones accept imported game asset ids only, never URLs.",
            {
                "game_id": "Stable Game Forge id.",
                "name": "Event/entity name.",
                "preset": "spawn_portal, audio_zone or particle_emitter.",
                "x": "World X position.",
                "y": "World Y position.",
                "z": "World Z position.",
                "target_spawn_id": "Existing World spawn id for spawn_portal.",
                "audio_asset_id": "Imported audio/music game asset id for audio_zone.",
                "radius": "Audio-zone radius, 0.25 to 100000.",
                "volume": "Audio-zone volume, 0 to 1.",
                "particle_color": "Particle #RRGGBB color.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "apply_game_world_event_preset",
            "Convert one non-core World entity to a safe World Event preset using only typed references and bounded parameters.",
            {
                "game_id": "Stable Game Forge id.",
                "entity_id": "Exact World DNA entity id.",
                "preset": "spawn_portal, audio_zone or particle_emitter.",
                "target_spawn_id": "Existing World spawn id for spawn_portal.",
                "audio_asset_id": "Imported audio/music game asset id for audio_zone.",
                "radius": "Audio-zone radius.",
                "volume": "Audio-zone volume.",
                "particle_color": "Particle #RRGGBB color.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "delete_game_world_event",
            "Delete one authored World Event entity. Core player, camera and spawn entities remain protected.",
            {"game_id": "Stable Game Forge id.", "entity_id": "Exact authored World Event entity id."},
            write=True,
        ),
        tools.ToolSpec(
            "build_world_events_playtest",
            "Build the cumulative Aura private playtest with gameplay, Adventure State, Advanced World Logic and World Events & Atmosphere.",
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
    event_terms = ("world event", "portal", "spawn", "audio zone", "ambient audio", "particle", "emitter", "atmosphere")
    if name == "build_world_events_playtest":
        return _contains(latest, ("build", "playtest", "play test", "run game", "test game"))
    if name == "delete_game_world_event":
        return _contains(latest, ("delete", "remove")) and _contains(latest, event_terms)
    if name == "create_game_world_event":
        return _contains(latest, ("add", "create", "make")) and _contains(latest, event_terms)
    if name == "apply_game_world_event_preset":
        return _contains(latest, ("apply", "make", "turn", "convert", "set")) and _contains(latest, event_terms)
    return True


def _require_editing(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("World Events authoring unlocks on the Basic £4.99 tier")


def _float(args: dict, key: str, default: float) -> float:
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return default


def _optional_text(args: dict, key: str, limit: int = 160) -> str | None:
    value = str(args.get(key) or "").strip()
    return value[:limit] if value else None


def _preset_body(args: dict) -> ApplyWorldEventPresetRequest:
    return ApplyWorldEventPresetRequest(
        preset=str(args.get("preset") or "particle_emitter"),
        target_spawn_id=_optional_text(args, "target_spawn_id"),
        audio_asset_id=_optional_text(args, "audio_asset_id"),
        radius=_float(args, "radius", 5.0),
        volume=_float(args, "volume", 0.65),
        particle_color=str(args.get("particle_color") or "#5be1ff")[:16],
    )


def install_aura_world_events_tools() -> None:
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
            raise PermissionError("Aura World Events-changing tools require an explicit matching Game Forge request in the member's latest message")
        args = dict(call.arguments or {})
        game = load_game(str(args.get("game_id") or ""))
        if call.name == "inspect_game_world_events":
            return world_event_state(game.id)
        _require_editing(self.member)
        if call.name == "create_game_world_event":
            body = CreateWorldEventEntityRequest(
                name=str(args.get("name") or "World Event")[:160],
                preset=str(args.get("preset") or "particle_emitter"),
                position=Vec3(
                    x=_float(args, "x", 0.0),
                    y=_float(args, "y", 0.0),
                    z=_float(args, "z", 0.0),
                ),
                target_spawn_id=_optional_text(args, "target_spawn_id"),
                audio_asset_id=_optional_text(args, "audio_asset_id"),
                radius=_float(args, "radius", 5.0),
                volume=_float(args, "volume", 0.65),
                particle_color=str(args.get("particle_color") or "#5be1ff")[:16],
            )
            entity = create_world_event_entity(game, body)
            return {"changed": True, "entity": entity.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "apply_game_world_event_preset":
            entity = apply_world_event_preset(game, str(args.get("entity_id") or ""), _preset_body(args))
            return {"changed": True, "entity": entity.model_dump(mode="json"), "invalidated_previous_build_and_rating": True}
        if call.name == "delete_game_world_event":
            delete_world_event_entity(game, str(args.get("entity_id") or ""))
            return {"changed": True, "deleted": True, "invalidated_previous_build_and_rating": True}
        if call.name == "build_world_events_playtest":
            game, _html = build_world_events_playtest(game)
            return {
                "changed": True,
                "private_playtest_url": f"/game-creation/play/{game.id}",
                "runtime": game.latest_build.runtime if game.latest_build else None,
                "world_events_runtime": True,
                "verified_same_origin_media_only": True,
                "external_media_urls_allowed": False,
                "runtime_network_access": False,
                "arbitrary_creator_code": False,
            }
        raise ValueError("Unsupported Aura World Events tool")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_world_events_tools"]
