from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import game_forge_api as foundation_game_api
from .aura_adventure_tools import install_aura_adventure_tools
from .aura_gameplay_tools import install_aura_gameplay_tools
from .aura_state_machine_tools import install_aura_state_machine_tools
from .aura_world_events_tools import install_aura_world_events_tools
from .aura_world_logic_tools import install_aura_world_logic_tools
from .game_forge_adventure import router as game_adventure_router
from .game_forge_adventure_portal import router as game_adventure_portal_router
from .game_forge_asset_bindings import router as game_asset_bindings_router
from .game_forge_assets import public_runtime_asset_path
from .game_forge_assets import router as game_assets_router
from .game_forge_assets import snapshot_public_assets
from .game_forge_aura_commands import router as game_aura_commands_router
from .game_forge_cinematics import router as game_cinematics_router
from .game_forge_export_portal import router as game_export_portal_router
from .game_forge_gameplay import router as game_gameplay_router
from .game_forge_gameplay_portal import router as game_gameplay_portal_router
from .game_forge_integrity import assess_game_integrity, game_integrity_hash
from .game_forge_state_machine import router as game_state_machine_router
from .game_forge_state_machine_portal import router as game_state_machine_portal_router
from .game_forge_state_machine_runtime import build_state_machine_playtest, private_play_html
from .game_forge_store import load_game, publish_snapshot, remove_public_snapshot, save_game
from .game_forge_world import (
    GameWorldDNA,
    ensure_world,
    generate_foundation_world,
    load_world,
    save_world,
    world_stream_index,
    world_summary,
)
from .game_forge_world_events import router as game_world_events_router
from .game_forge_world_events_portal import router as game_world_events_portal_router
from .game_forge_world_logic import router as game_world_logic_router
from .game_forge_world_logic_portal import router as game_world_logic_portal_router
from .plans import GAME_CREATE, GAME_PLAYTEST
from .production_readiness import router as production_readiness_router
from .tier2_daily_meter import TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID
from .tier2_provider_guard import Tier2ProviderGuard

# This module is imported before the central install_aura_game_tools() call in app.py. These
# dedicated wrappers become lower layers in AuraToolRegistry's chain; existing game/media tools
# remain authoritative for their own names and delegate gameplay/Adventure/World Logic/Event/
# State Machine names safely.
install_aura_gameplay_tools()
install_aura_adventure_tools()
install_aura_world_logic_tools()
install_aura_world_events_tools()
install_aura_state_machine_tools()

router = APIRouter(tags=["Aura Game World"])
tier2_guard = Tier2ProviderGuard()
T = TypeVar("T")
# Binding/cinematic/gameplay/Adventure/World Logic/World Event/State Machine/export routes and
# global operations readiness are composed here so consumers mounting Game Forge get the complete
# subsystem without relying on application import order.
router.include_router(production_readiness_router)
router.include_router(game_asset_bindings_router)
router.include_router(game_assets_router)
router.include_router(game_aura_commands_router)
router.include_router(game_cinematics_router)
router.include_router(game_gameplay_router)
router.include_router(game_gameplay_portal_router)
router.include_router(game_adventure_router)
router.include_router(game_adventure_portal_router)
router.include_router(game_world_logic_router)
router.include_router(game_world_logic_portal_router)
router.include_router(game_world_events_router)
router.include_router(game_world_events_portal_router)
router.include_router(game_state_machine_router)
router.include_router(game_state_machine_portal_router)
router.include_router(game_export_portal_router)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _creator(request: Request):
    member = _member(request)
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game world editing unlocks on the Basic £4.99 tier")
    return member


def _tester(request: Request):
    member = _member(request)
    if not member.plan.has(GAME_PLAYTEST):
        raise HTTPException(403, "Game playtesting is unavailable on this membership")
    return member


def _game(game_id: str):
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _game_operation_request_key(request: Request, operation: str) -> str:
    """Resolve one bounded retry key without breaking clients that pre-date idempotency headers."""
    supplied = request.headers.get("Idempotency-Key") or request.headers.get("X-Request-ID")
    if supplied is not None:
        value = supplied.strip()
        if not value or len(value) > 180:
            raise HTTPException(400, "A bounded idempotency request key is required")
        return value
    return f"{operation}-{uuid4().hex}"


def _execute_game_operation(
    member,
    request: Request,
    *,
    operation: str,
    provider_call: Callable[[], T],
) -> T:
    """Meter eligible paid Game Forge mutations immediately around their real storage mutation."""
    plan_id = str(getattr(member.plan, "id", "") or "").strip().lower()
    if plan_id not in {TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID}:
        # Free/legacy memberships retain their separately-authorized entitlement path.
        return provider_call()
    user_id = str(getattr(member, "user_id", "") or "").strip()
    if not user_id:
        raise HTTPException(401, "Authenticated member identity unavailable")
    result, _admission = tier2_guard.execute(
        user_id=user_id,
        plan_id=plan_id,
        operation=operation,
        request_key=_game_operation_request_key(request, operation),
        provider_call=provider_call,
    )
    return result


# These authoritative create/edit routes are mounted before the foundation Game Forge router in
# app.py. They preserve the existing native Game DNA implementation while adding the shared Tier 2
# cross-Studio admission boundary. Safety/authorization validation still runs before paid admission.
@router.post("/api/game-forge/games")
def create_game_with_tier2_admission(body: foundation_game_api.CreateGameRequest, request: Request):
    member = _creator(request)
    try:
        return foundation_game_api._public_game(
            _execute_game_operation(
                member,
                request,
                operation="game_create",
                provider_call=lambda: foundation_game_api.create_game_for_member(member, body),
            )
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.patch("/api/game-forge/games/{game_id}")
def update_game_with_tier2_admission(
    game_id: str,
    body: foundation_game_api.UpdateGameRequest,
    request: Request,
):
    member = _creator(request)
    game = _game(game_id)
    if not game.actively_editable:
        raise HTTPException(409, "This finished/public game is locked. Reopen it before editing; reopening removes its public test snapshot.")
    updates = body.model_dump(exclude_unset=True)
    next_engine = updates.get("engine_target", game.engine_target)
    try:
        foundation_game_api._validate_target(game.dimension, next_engine)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    def persist_edit():
        for key, value in updates.items():
            setattr(game, key, value)
        foundation_game_api.enforce_creation_policy(game.title, game.prompt, game.synopsis, context="game edit")
        foundation_game_api._invalidate_after_edit(game)
        save_game(game)
        return game

    try:
        edited = _execute_game_operation(
            member,
            request,
            operation="game_edit",
            provider_call=persist_edit,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return foundation_game_api._public_game(edited)


def _invalidate_game_after_world_change(game) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def _editor_urls(game_id: str) -> dict[str, str]:
    return {
        "gameplay_editor_url": f"/game-creation/gameplay/{game_id}",
        "adventure_editor_url": f"/game-creation/adventure/{game_id}",
        "world_logic_editor_url": f"/game-creation/world-logic/{game_id}",
        "world_events_editor_url": f"/game-creation/world-events/{game_id}",
        "state_machine_editor_url": f"/game-creation/state-machines/{game_id}",
        "export_studio_url": f"/game-creation/export/{game_id}",
        "godot_source_preview_url": f"/game-creation/godot-export/{game_id}",
    }


@router.get("/api/game-forge/games/{game_id}/world")
def get_world(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    world = ensure_world(game)
    return {
        "world": world.model_dump(mode="json"),
        "summary": world_summary(world),
        "stream_index": world_stream_index(world),
        "native_engine": True,
        "arbitrary_script_source_allowed": False,
        **_editor_urls(game.id),
    }


@router.post("/api/game-forge/games/{game_id}/world/regenerate")
def regenerate_world(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    _invalidate_game_after_world_change(game)
    world = generate_foundation_world(game)
    return {
        "world": world.model_dump(mode="json"),
        "summary": world_summary(world),
        "invalidated_previous_build_and_rating": True,
        **_editor_urls(game.id),
    }


@router.put("/api/game-forge/games/{game_id}/world")
def replace_world(game_id: str, body: GameWorldDNA, request: Request):
    _creator(request)
    game = _game(game_id)
    if body.game_id != game.id:
        raise HTTPException(400, "World game_id does not match this game")
    if body.dimension != game.dimension:
        raise HTTPException(400, "World dimension must match the current Game DNA")
    try:
        current = load_world(game.id)
        if body.world_id != current.world_id:
            raise HTTPException(409, "World identity cannot be replaced through an edit; regenerate explicitly instead")
        if body.revision < current.revision:
            raise HTTPException(409, "Stale world revision")
    except FileNotFoundError:
        pass
    body.touch()
    try:
        save_world(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _invalidate_game_after_world_change(game)
    return {
        "world": body.model_dump(mode="json"),
        "summary": world_summary(body),
        "invalidated_previous_build_and_rating": True,
        **_editor_urls(game.id),
    }


# This route deliberately precedes the foundation API equivalent in app.py. The normal portal/API
# Build action produces the cumulative State Machine runtime, which progressively delegates to the
# previously validated World Events/World Logic/Adventure runtimes when no State Machines exist.
@router.post("/api/game-forge/games/{game_id}/build")
def build_world_state_machine(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    ensure_world(game)
    try:
        game, _html = build_state_machine_playtest(game)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, f"Aura State Machine build failed: {exc}") from exc
    return {
        "game": {
            "id": game.id,
            "title": game.title,
            "status": game.status,
            "version": game.version,
            "latest_build": game.latest_build.model_dump(mode="json") if game.latest_build else None,
        },
        "private_playtest_url": f"/game-creation/play/{game.id}",
        **_editor_urls(game.id),
        "runtime": game.latest_build.runtime if game.latest_build else None,
        "requested_engine": game.engine_target,
        "aura_native_runtime": True,
        "declarative_gameplay_runtime": True,
        "adventure_state_runtime": True,
        "advanced_world_logic_runtime": True,
        "world_events_runtime": True,
        "state_machine_runtime": True,
        "max_state_machine_transitions_per_frame": 1,
        "state_machine_adventure_flags_only": True,
        "verified_same_origin_world_audio": True,
        "external_world_audio_urls_allowed": False,
        "browser_local_save": True,
        "server_save_sync": False,
        "world_dna_physics": True,
        "arbitrary_server_code_executed": False,
        "runtime_network_access": False,
    }


# These routes deliberately precede foundation API equivalents in app.py. They bind approval and
# publishing to Game DNA + World DNA + verified media/models + cinematic/VFX + gameplay + Adventure
# State + Advanced World Logic + World Events + typed State Machines.
@router.post("/api/game-forge/games/{game_id}/scan")
def scan_world_integrity(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    ensure_world(game)
    assessment = assess_game_integrity(game)
    game.rating_assessment = assessment
    if assessment.public_test_allowed and game.latest_build and game.latest_build.content_hash == assessment.content_hash:
        game.status = "approved_test"
    else:
        game.status = "review_ready"
    game.updated_at = assessment.generated_at
    save_game(game)
    return assessment.model_dump(mode="json")


@router.post("/api/game-forge/games/{game_id}/publish-test")
def publish_world_integrity(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    ensure_world(game)
    current_hash = game_integrity_hash(game)
    if not game.latest_build or game.latest_build.content_hash != current_hash:
        raise HTTPException(409, "Build is missing or stale. Rebuild the current Aura world before public testing.")
    if not game.rating_assessment or game.rating_assessment.content_hash != current_hash:
        raise HTTPException(409, "Rating/compliance assessment is missing or stale. Re-scan the current Aura world.")
    if not game.rating_assessment.public_test_allowed:
        raise HTTPException(409, {"message": "Game is blocked from public testing", "blockers": game.rating_assessment.blockers})
    try:
        html = private_play_html(game)
        game.public_id = publish_snapshot(game, html)
        public_assets = snapshot_public_assets(game.id, game.public_id)
    except FileNotFoundError as exc:
        raise HTTPException(409, "Private playtest build is unavailable") from exc
    except (OSError, ValueError) as exc:
        remove_public_snapshot(game)
        game.public_id = None
        raise HTTPException(409, f"Public media snapshot failed: {exc}") from exc
    game.status = "public_test"
    save_game(game)
    return {
        "published": True,
        "public_id": game.public_id,
        "play_url": f"/game-gallery/{game.public_id}",
        "suggested_age_band": game.rating_assessment.suggested_age_band,
        "official_rating": False,
        "rating_note": game.rating_assessment.note,
        "integrity_bound_to_world": True,
        "integrity_bound_to_assets": True,
        "integrity_bound_to_cinematics": True,
        "integrity_bound_to_gameplay": True,
        "integrity_bound_to_adventure_state": True,
        "integrity_bound_to_advanced_world_logic": True,
        "integrity_bound_to_world_events": True,
        "integrity_bound_to_state_machines": True,
        "verified_same_origin_world_audio": True,
        "player_save_storage": "browser_local_only",
        "verified_media_snapshot_count": len(public_assets),
        "external_media_urls_included": False,
    }


@router.get(
    "/game-gallery/{public_id}/media/{filename}",
    response_class=FileResponse,
    include_in_schema=False,
)
def public_game_runtime_media(public_id: str, filename: str, request: Request):
    _tester(request)
    try:
        path, row = public_runtime_asset_path(public_id, filename)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise HTTPException(404, "Published game media not found") from exc
    return FileResponse(
        path,
        media_type=str(row.get("mime_type") or "application/octet-stream"),
        filename=None,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{row.get("sha256") or ""}"',
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
