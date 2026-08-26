from __future__ import annotations

from typing import Any

from . import aura_agent_tools as tools
from .game_forge_api import CreateGameRequest, create_game_for_member
from .game_forge_ratings import assess_game
from .game_forge_runtime import build_private_playtest
from .game_forge_store import list_games, load_game, save_game

_INSTALLED = False
_GAME_TOOL_NAMES = {
    "list_game_projects",
    "inspect_game_project",
    "create_game_project",
    "scan_game_project",
    "build_game_playtest",
}


def _install_specs() -> None:
    specs = [
        tools.ToolSpec(
            "list_game_projects",
            "List the signed-in member's private Pulsar Game Forge projects and current build/rating state.",
            {},
        ),
        tools.ToolSpec(
            "inspect_game_project",
            "Read one private engine-independent Game DNA project, including mechanics, content disclosures, build and provisional rating state.",
            {"game_id": "Stable Game Forge id."},
        ),
        tools.ToolSpec(
            "create_game_project",
            "Create a new private Game DNA workspace. Basic allows one active editable game at a time; Pro allows unlimited active projects.",
            {
                "title": "Game title.",
                "prompt": "Detailed game description.",
                "genre": "Genre/niche.",
                "dimension": "2d or 3d.",
                "engine_target": "Optional phaser4, playcanvas, babylon or godot. Aura may omit to auto-route.",
                "synopsis": "Optional story/synopsis.",
                "art_direction": "Optional graphics direction.",
                "audio_direction": "Optional music/voice/SFX direction.",
                "rights_confirmed": "True only when the member explicitly confirms they own/have permission for requested source material.",
            },
            write=True,
        ),
        tools.ToolSpec(
            "scan_game_project",
            "Run the Pulsar provisional content/rating/privacy/monetisation preflight for the current Game DNA. This never issues an official ESRB/PEGI/IARC or authority rating.",
            {"game_id": "Stable Game Forge id."},
            write=True,
        ),
        tools.ToolSpec(
            "build_game_playtest",
            "Build the current private Game DNA into the isolated no-network browser playtest runtime. Arbitrary generated server code is never executed in the API host.",
            {"game_id": "Stable Game Forge id."},
            write=True,
        ),
    ]
    existing = {item.name for item in tools.TOOL_SPECS}
    for spec in specs:
        if spec.name not in existing:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec


def _explicit_game_write_allowed(name: str, message: str) -> bool:
    text = (message or "").lower()
    if name == "create_game_project":
        return any(x in text for x in ("create", "make", "build", "start", "generate")) and "game" in text
    if name == "scan_game_project":
        return any(x in text for x in ("scan", "rate", "rating", "check", "review")) and "game" in text
    if name == "build_game_playtest":
        return any(x in text for x in ("build", "playtest", "play test", "test", "generate")) and "game" in text
    return True


def _summary(game) -> dict:
    return {
        "id": game.id,
        "title": game.title,
        "genre": game.genre,
        "dimension": game.dimension,
        "engine_target": game.engine_target,
        "status": game.status,
        "version": game.version,
        "rights_confirmed": game.rights_confirmed,
        "latest_build": game.latest_build.model_dump(mode="json") if game.latest_build else None,
        "rating_assessment": game.rating_assessment.model_dump(mode="json") if game.rating_assessment else None,
        "public_id": game.public_id,
        "private_storage_exposed": False,
    }


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
        if call.name in {"create_game_project", "scan_game_project", "build_game_playtest"} and not _explicit_game_write_allowed(call.name, latest_user_message):
            raise PermissionError("Aura game-changing tools require an explicit game creation/build/scan request in the member's latest message")
        args = dict(call.arguments or {})
        if call.name == "list_game_projects":
            return [_summary(game) for game in list_games()]
        if call.name == "inspect_game_project":
            return load_game(str(args.get("game_id") or "")).model_dump(mode="json")
        if call.name == "create_game_project":
            body = CreateGameRequest(
                title=str(args.get("title") or "Untitled Game")[:160],
                prompt=str(args.get("prompt") or "").strip(),
                genre=str(args.get("genre") or "adventure")[:120],
                dimension=str(args.get("dimension") or "2d"),
                engine_target=(str(args.get("engine_target")) if args.get("engine_target") else None),
                synopsis=str(args.get("synopsis") or "")[:8000],
                art_direction=str(args.get("art_direction") or "")[:4000],
                audio_direction=str(args.get("audio_direction") or "")[:4000],
                rights_confirmed=bool(args.get("rights_confirmed", False)),
                rights_attestation=("Member explicitly confirmed source-material rights in the Aura request." if bool(args.get("rights_confirmed", False)) else ""),
            )
            game = create_game_for_member(self.member, body)
            return {"game": _summary(game), "created": True, "public": False}
        game = load_game(str(args.get("game_id") or ""))
        if call.name == "scan_game_project":
            assessment = assess_game(game)
            game.rating_assessment = assessment
            if assessment.public_test_allowed and game.latest_build and game.latest_build.content_hash == assessment.content_hash:
                game.status = "approved_test"
            else:
                game.status = "review_ready"
            save_game(game)
            return {
                "assessment": assessment.model_dump(mode="json"),
                "official_rating": False,
                "public_test_allowed": assessment.public_test_allowed,
            }
        if call.name == "build_game_playtest":
            game, _html = build_private_playtest(game)
            return {
                "game": _summary(game),
                "private_playtest_url": f"/game-creation/play/{game.id}",
                "arbitrary_server_code_executed": False,
                "network_access_enabled": False,
            }
        raise ValueError(f"Aura game tool is not implemented: {call.name}")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_game_tools"]
