from __future__ import annotations

from html import escape
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .content_safety import enforce_creation_policy
from .game_forge_models import ENGINE_REGISTRY, GameContentDisclosure, GameDNA, GameEngine
from .game_forge_ratings import assess_game, rating_content_hash
from .game_forge_runtime import PLAYTEST_CSP, build_private_playtest, private_play_html
from .game_forge_store import (
    active_editable_games,
    create_game,
    list_games,
    list_public_games,
    load_game,
    public_manifest,
    public_play_html,
    publish_snapshot,
    remove_public_snapshot,
    save_game,
)
from .plans import GAME_CREATE, GAME_CREATE_UNLIMITED, GAME_PLAYTEST

router = APIRouter(tags=["Pulsar Game Forge"])


class CreateGameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=12000)
    genre: str = Field(default="adventure", min_length=1, max_length=120)
    niches: list[str] = Field(default_factory=list, max_length=30)
    dimension: str = Field(default="2d", pattern="^(2d|3d)$")
    engine_target: GameEngine | None = None
    synopsis: str = Field(default="", max_length=8000)
    art_direction: str = Field(default="", max_length=4000)
    audio_direction: str = Field(default="", max_length=4000)
    rights_confirmed: bool = False
    rights_attestation: str = Field(default="", max_length=2000)
    content: GameContentDisclosure = Field(default_factory=GameContentDisclosure)


class UpdateGameRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    prompt: str | None = Field(default=None, min_length=1, max_length=12000)
    genre: str | None = Field(default=None, min_length=1, max_length=120)
    niches: list[str] | None = Field(default=None, max_length=30)
    engine_target: GameEngine | None = None
    synopsis: str | None = Field(default=None, max_length=8000)
    mechanics: list[str] | None = Field(default=None, max_length=80)
    controls: list[str] | None = Field(default=None, max_length=40)
    scenes: list[str] | None = Field(default=None, max_length=80)
    art_direction: str | None = Field(default=None, max_length=4000)
    audio_direction: str | None = Field(default=None, max_length=4000)
    npc_direction: str | None = Field(default=None, max_length=4000)
    multiplayer_direction: str | None = Field(default=None, max_length=4000)
    rights_confirmed: bool | None = None
    rights_attestation: str | None = Field(default=None, max_length=2000)
    content: GameContentDisclosure | None = None


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _creator(request: Request):
    member = _member(request)
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game creation unlocks on the Basic £4.99 tier")
    return member


def _tester(request: Request):
    member = _member(request)
    if not member.plan.has(GAME_PLAYTEST):
        raise HTTPException(403, "Game playtesting is unavailable on this membership")
    return member


def _public_game(game: GameDNA) -> dict:
    return {
        "id": game.id,
        "title": game.title,
        "genre": game.genre,
        "niches": game.niches,
        "dimension": game.dimension,
        "engine_target": game.engine_target,
        "synopsis": game.synopsis,
        "status": game.status,
        "version": game.version,
        "rights_confirmed": game.rights_confirmed,
        "rating_assessment": game.rating_assessment.model_dump(mode="json") if game.rating_assessment else None,
        "latest_build": game.latest_build.model_dump(mode="json") if game.latest_build else None,
        "public_id": game.public_id,
        "updated_at": game.updated_at,
        "private_storage_exposed": False,
    }


def _creative_project_name(game: GameDNA) -> str:
    metadata = game.metadata if isinstance(game.metadata, dict) else {}
    return str(metadata.get("creative_project_name") or "").strip()


def _game_creation_url(game: GameDNA) -> str:
    params: list[tuple[str, str]] = []
    project = _creative_project_name(game)
    if project:
        params.append(("project", project))
    params.append(("game", str(game.id)))
    return f"/game-creation?{urlencode(params)}"


def _private_playtest_url(game: GameDNA, *, popout: bool = False) -> str:
    params: list[tuple[str, str]] = []
    project = _creative_project_name(game)
    if project:
        params.append(("project", project))
    params.append(("game", str(game.id)))
    if popout:
        params.append(("popout", "1"))
    return f"/game-creation/play/{quote(str(game.id), safe='')}?{urlencode(params)}"


def _invalidate_after_edit(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()


def _validate_target(dimension: str, engine: GameEngine) -> None:
    if dimension == "2d" and engine in {"aura3d", "playcanvas", "babylon"}:
        raise ValueError("This is a 2D Game DNA project. Use Aura Game Engine 2D, Phaser export, or Godot export.")
    if dimension == "3d" and engine in {"aura2d", "phaser4"}:
        raise ValueError("This is a 3D Game DNA project. Use Aura Game Engine 3D, PlayCanvas/Babylon export, or Godot export.")


def create_game_for_member(member, body: CreateGameRequest) -> GameDNA:
    enforce_creation_policy(body.title, body.prompt, body.synopsis, context="game creation")
    engine: GameEngine = body.engine_target or ("aura2d" if body.dimension == "2d" else "aura3d")
    _validate_target(body.dimension, engine)
    game = GameDNA(
        title=body.title.strip(),
        prompt=body.prompt.strip(),
        genre=body.genre.strip(),
        niches=[str(x).strip()[:120] for x in body.niches if str(x).strip()],
        dimension=body.dimension,
        engine_target=engine,
        synopsis=body.synopsis.strip(),
        art_direction=body.art_direction.strip(),
        audio_direction=body.audio_direction.strip(),
        rights_confirmed=body.rights_confirmed,
        rights_attestation=body.rights_attestation.strip(),
        content=body.content,
        metadata={
            "engine_independent_game_dna": True,
            "aura_orchestrated": True,
            "aura_native_engine": engine in {"aura2d", "aura3d"},
            "native_runtime": "aura_game_runtime_v1",
            "external_engines_are_export_adapters": True,
        },
    )
    return create_game(member, game)


@router.get("/api/game-forge/engines")
def game_engines(request: Request):
    _tester(request)
    return {
        "engines": ENGINE_REGISTRY,
        "router": "Aura-owned engine-independent Game DNA",
        "native_runtime": "aura_game_runtime_v1",
        "native_defaults": {"2d": "aura2d", "3d": "aura3d"},
        "external_engines_are_export_adapters": True,
        "generated_code_executes_on_api_host": False,
    }


@router.get("/api/game-forge/games")
def my_games(request: Request):
    member = _member(request)
    return {
        "games": [_public_game(row) for row in list_games()],
        "can_create": member.plan.has(GAME_CREATE),
        "unlimited_active_projects": member.plan.has(GAME_CREATE_UNLIMITED),
        "active_editable_count": len(active_editable_games()),
        "basic_active_limit": None if member.plan.has(GAME_CREATE_UNLIMITED) else (1 if member.plan.has(GAME_CREATE) else 0),
    }


@router.post("/api/game-forge/games")
def new_game(body: CreateGameRequest, request: Request):
    member = _creator(request)
    try:
        game = create_game_for_member(member, body)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
    return _public_game(game)


@router.get("/api/game-forge/games/{game_id}")
def game_detail(game_id: str, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    return {**_public_game(game), "game_dna": game.model_dump(mode="json")}


@router.patch("/api/game-forge/games/{game_id}")
def update_game(game_id: str, body: UpdateGameRequest, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    if not game.actively_editable:
        raise HTTPException(409, "This finished/public game is locked. Reopen it before editing; reopening removes its public test snapshot.")
    updates = body.model_dump(exclude_unset=True)
    next_engine = updates.get("engine_target", game.engine_target)
    _validate_target(game.dimension, next_engine)
    for key, value in updates.items():
        setattr(game, key, value)
    enforce_creation_policy(game.title, game.prompt, game.synopsis, context="game edit")
    _invalidate_after_edit(game)
    save_game(game)
    return _public_game(game)


@router.post("/api/game-forge/games/{game_id}/build")
def build_game(game_id: str, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
        game, _html = build_private_playtest(game)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    return {
        "game": _public_game(game),
        "private_playtest_url": _private_playtest_url(game),
        "runtime": game.latest_build.runtime if game.latest_build else None,
        "requested_engine": game.engine_target,
        "aura_native_runtime": True,
        "arbitrary_server_code_executed": False,
    }


@router.post("/api/game-forge/games/{game_id}/scan")
def scan_game(game_id: str, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    assessment = assess_game(game)
    game.rating_assessment = assessment
    if assessment.public_test_allowed and game.latest_build and game.latest_build.content_hash == assessment.content_hash:
        game.status = "approved_test"
    else:
        game.status = "review_ready"
    game.updated_at = assessment.generated_at
    save_game(game)
    return assessment.model_dump(mode="json")


@router.post("/api/game-forge/games/{game_id}/publish-test")
def publish_game_test(game_id: str, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    current_hash = rating_content_hash(game)
    if not game.latest_build or game.latest_build.content_hash != current_hash:
        raise HTTPException(409, "Build is missing or stale. Rebuild the game before public testing.")
    if not game.rating_assessment or game.rating_assessment.content_hash != current_hash:
        raise HTTPException(409, "Rating/compliance assessment is missing or stale. Re-scan the current build.")
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
    }


@router.post("/api/game-forge/games/{game_id}/archive")
def archive_game(game_id: str, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    game.status = "archived"
    game.touch()
    save_game(game)
    return _public_game(game)


@router.post("/api/game-forge/games/{game_id}/reopen")
def reopen_game(game_id: str, request: Request):
    member = _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    others = [row for row in active_editable_games() if row.id != game.id]
    if others and not member.plan.has(GAME_CREATE_UNLIMITED):
        raise HTTPException(409, "Basic can edit one active game at a time. Archive/finish the current active game first.")
    remove_public_snapshot(game)
    game.public_id = None
    game.status = "draft"
    game.touch()
    save_game(game)
    return _public_game(game)


@router.get("/api/game-forge/gallery")
def game_gallery(request: Request):
    _tester(request)
    return {"games": list_public_games(), "free_tier_playtesting": True}


def _host_page(
    title: str,
    frame_url: str,
    *,
    rating_line: str,
    popout: bool,
    return_url: str = "/game-creation",
    popout_url: str | None = None,
) -> HTMLResponse:
    safe_title = escape(title)
    safe_frame = escape(frame_url, quote=True)
    safe_return = escape(return_url, quote=True)
    safe_popout = escape(popout_url or "", quote=True)
    popup = ""
    if not popout and safe_popout:
        popup = f"<a href='{safe_popout}' target='_blank' rel='noopener noreferrer'>Pop Out for TikTok LIVE Studio</a>"
    html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{safe_title} — Playtest</title><style>html,body{{margin:0;height:100%;background:#03050a;color:white;font-family:system-ui}}header{{height:58px;padding:9px 12px;display:flex;align-items:center;justify-content:space-between;gap:10px;border-bottom:1px solid #ffffff22}}header small{{color:#c4cada}}button,a{{background:#1a2030;color:white;border:1px solid #ffffff2a;border-radius:9px;padding:8px 10px;text-decoration:none;font-weight:800}}iframe{{display:block;width:100%;height:calc(100% - 59px);border:0;background:#050611}}</style></head><body><header><div><b>{safe_title}</b><br><small>{escape(rating_line)}</small></div><div>{popup} <a href='{safe_return}'>Game Creation</a></div></header><iframe src='{safe_frame}' sandbox='allow-scripts allow-pointer-lock' referrerpolicy='no-referrer' allow='gamepad'></iframe></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/game-creation/play/{game_id}", response_class=HTMLResponse, include_in_schema=False)
def private_playtest(game_id: str, request: Request, popout: int = 0):
    _creator(request)
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    if not game.latest_build:
        raise HTTPException(409, "Build the game before opening its playtest")
    rating = game.rating_assessment.suggested_age_band if game.rating_assessment else "Not yet scanned"
    return _host_page(
        game.title,
        f"/api/game-forge/games/{game.id}/playtest-frame",
        rating_line=f"Pulsar provisional rating: {rating}",
        popout=bool(popout),
        return_url=_game_creation_url(game),
        popout_url=_private_playtest_url(game, popout=True),
    )


@router.get("/api/game-forge/games/{game_id}/playtest-frame", response_class=HTMLResponse, include_in_schema=False)
def private_playtest_frame(game_id: str, request: Request):
    _creator(request)
    try:
        game = load_game(game_id)
        html = private_play_html(game)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Playtest build not found") from exc
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": PLAYTEST_CSP,
            "Cache-Control": "no-store",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.get("/game-gallery/{public_id}", response_class=HTMLResponse, include_in_schema=False)
def public_game_host(public_id: str, request: Request, popout: int = 0):
    _tester(request)
    try:
        manifest = public_manifest(public_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Public game not found") from exc
    safe_public_id = quote(str(public_id), safe="")
    return _host_page(
        str(manifest.get("title") or "Pulsar Game"),
        f"/game-gallery/{safe_public_id}/frame",
        rating_line=f"Pulsar provisional assessment: {manifest.get('suggested_age_band') or 'unrated'} — not an official authority rating",
        popout=bool(popout),
        popout_url=f"/game-gallery/{safe_public_id}?popout=1",
    )


@router.get("/game-gallery/{public_id}/frame", response_class=HTMLResponse, include_in_schema=False)
def public_game_frame(public_id: str, request: Request):
    _tester(request)
    try:
        html = public_play_html(public_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Public game not found") from exc
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": PLAYTEST_CSP,
            "Cache-Control": "private, max-age=60",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )