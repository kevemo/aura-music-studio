from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .game_forge_assets import router as game_assets_router
from .game_forge_integrity import assess_game_integrity, game_integrity_hash
from .game_forge_runtime import private_play_html
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
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game World"])
router.include_router(game_assets_router)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game world editing unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str):
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _invalidate_game_after_world_change(game) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


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
    }


# These two routes deliberately precede the foundation API equivalents in app.py. They bind
# approval/publishing to Game DNA + Aura World DNA + imported Game Forge asset snapshots,
# not just the high-level game questionnaire.
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
    except FileNotFoundError as exc:
        raise HTTPException(409, "Private playtest build is unavailable") from exc
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
    }
