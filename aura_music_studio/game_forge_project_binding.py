from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .creative_library import scan_creative_library
from .creative_project import CreativeProjectStore
from .game_forge_api import (
    CreateGameRequest,
    _creator,
    _invalidate_after_edit,
    _member,
    _public_game,
    create_game_for_member,
)
from .game_forge_assets import (
    AttachGameAssetRequest,
    _ALLOWED_MEDIA_KINDS,
    _invalidate_after_asset_change,
    _require_editable,
    _safe_source_id,
    attach_creative_asset,
    list_game_assets,
    public_asset,
)
from .game_forge_export_readiness import aura_web_export_readiness
from .game_forge_live_integration import router as game_live_router
from .game_forge_live_transport_guard import router as game_live_transport_guard_router
from .game_forge_model_generation import router as game_model_generation_router
from .game_forge_models import GameDNA
from .game_forge_shared_sky_transport import router as game_shared_sky_transport_router
from .game_forge_store import active_editable_games, list_games, load_game, save_game
from .game_forge_visual_logic import router as game_visual_logic_router
from .game_forge_visual_logic_portal import router as game_visual_logic_portal_router
from .plans import GAME_CREATE, GAME_CREATE_UNLIMITED
from .tenant_storage import project_path

router = APIRouter(tags=["Game Forge Creative Project Continuity"])
# The guard must be registered before the legacy live router so the established API paths
# synchronise any already-bound Chat 2 programme source instead of leaving stale ready state.
router.include_router(game_live_transport_guard_router)
router.include_router(game_live_router)
router.include_router(game_model_generation_router)
router.include_router(game_shared_sky_transport_router)
router.include_router(game_visual_logic_router)
router.include_router(game_visual_logic_portal_router)

_BINDING_KEY = "creative_project_name"


class BindGameProjectRequest(BaseModel):
    creative_project_name: str = Field(min_length=1, max_length=120)


def creative_project_name(game: GameDNA) -> str | None:
    value = str((game.metadata or {}).get(_BINDING_KEY) or "").strip()
    return value or None


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _creative_project(name: str) -> tuple[Path, object]:
    clean = str(name or "").strip()
    if not clean:
        raise HTTPException(400, "Creative project name is required")
    try:
        project = project_path(clean, must_exist=True).resolve()
    except ValueError as exc:
        raise HTTPException(400, "Invalid Creative project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Creative project not found") from exc
    store = CreativeProjectStore(project)
    try:
        manifest = store.load()
    except FileNotFoundError as exc:
        raise HTTPException(409, "This project has no Creative DNA manifest yet") from exc
    if str(manifest.project_name or "").strip() != project.name:
        raise HTTPException(409, "Creative project identity does not match its tenant storage")
    return project, manifest


def _binding_payload(game: GameDNA) -> dict:
    name = creative_project_name(game)
    return {
        "game_id": game.id,
        "creative_project_name": name,
        "project_bound": bool(name),
        "legacy_unbound_compatibility": not bool(name),
        "single_project_workspace": bool(name),
        "go_live_create_url": f"/game-creation/live/{game.id}",
        "visual_logic_capabilities_url": f"/api/game-forge/games/{game.id}/visual-logic",
        "visual_logic_editor_url_template": f"/game-creation/visual-logic/{game.id}/{{entity_id}}",
    }


def _project_game_payload(game: GameDNA) -> dict:
    return {
        **_public_game(game),
        **_binding_payload(game),
        "aura_web_export": aura_web_export_readiness(game),
    }


def _bind_game(game: GameDNA, project_name: str, *, invalidate_existing: bool) -> tuple[GameDNA, bool]:
    _creative_project(project_name)
    clean = project_name.strip()
    current = creative_project_name(game)
    if current:
        if current != clean:
            raise HTTPException(
                409,
                f"This Game DNA is already bound to Creative project '{current}'. Open that project instead of silently rebinding it.",
            )
        return game, False

    imported = list_game_assets(game.id)
    foreign = sorted({row.source_project for row in imported if row.source_project != clean})
    if foreign:
        raise HTTPException(
            409,
            "This legacy game already contains snapshots from another Creative project. Remove or migrate those snapshots before binding it.",
        )

    game.metadata = {
        **(game.metadata or {}),
        _BINDING_KEY: clean,
        "creative_project_bound": True,
        "creative_project_continuity": "shared_tenant_project",
    }
    if invalidate_existing:
        _invalidate_after_edit(game)
    save_game(game)
    return game, True


def _project_library(member, game: GameDNA) -> tuple[list[dict], str | None]:
    binding = creative_project_name(game)
    if not binding:
        rows = scan_creative_library(member)
        return [row for row in rows if row.get("kind") in _ALLOWED_MEDIA_KINDS], None
    project, _manifest = _creative_project(binding)
    rows = scan_creative_library(member, project_dirs=[project])
    return [row for row in rows if row.get("kind") in _ALLOWED_MEDIA_KINDS], binding


def _project_games(project_name: str) -> list[GameDNA]:
    clean = str(project_name or "").strip()
    return [row for row in list_games() if creative_project_name(row) == clean]


@router.get("/api/game-forge/projects/{project_name}/games")
def games_in_creative_project(project_name: str, request: Request):
    member = _member(request)
    _creative_project(project_name)
    clean = project_name.strip()
    rows = _project_games(clean)
    unlimited = member.plan.has(GAME_CREATE_UNLIMITED)
    can_create = member.plan.has(GAME_CREATE)
    return {
        "games": [_project_game_payload(row) for row in rows],
        "creative_project_name": clean,
        "project_bound_view": True,
        "can_create": can_create,
        "unlimited_active_projects": unlimited,
        # The create entitlement is global to Game Forge, so this remains deliberately unscoped.
        "active_editable_count": len(active_editable_games()),
        "basic_active_limit": None if unlimited else (1 if can_create else 0),
    }


@router.post("/api/game-forge/projects/{project_name}/games")
def create_game_in_creative_project(project_name: str, body: CreateGameRequest, request: Request):
    member = _creator(request)
    _creative_project(project_name)
    try:
        game = create_game_for_member(member, body)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
    game.metadata = {
        **(game.metadata or {}),
        _BINDING_KEY: project_name.strip(),
        "creative_project_bound": True,
        "creative_project_continuity": "shared_tenant_project",
    }
    save_game(game)
    return _project_game_payload(game)


@router.get("/api/game-forge/games/{game_id}/project-context")
def game_project_context(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    payload = _binding_payload(game)
    binding = creative_project_name(game)
    if binding:
        _creative_project(binding)
    return payload


@router.post("/api/game-forge/games/{game_id}/project-context")
def bind_game_project(game_id: str, body: BindGameProjectRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    game, changed = _bind_game(game, body.creative_project_name, invalidate_existing=True)
    return {
        **_binding_payload(game),
        "binding_changed": changed,
        "stale_build_invalidated": changed,
    }


@router.get("/api/game-forge/games/{game_id}/project-library")
def game_project_library(game_id: str, request: Request):
    member = _creator(request)
    game = _game(game_id)
    _require_editable(game)
    rows, binding = _project_library(member, game)
    return {
        "game_id": game.id,
        "creative_project_name": binding,
        "project_bound": bool(binding),
        "legacy_unbound_compatibility": not bool(binding),
        "items": rows,
        "count": len(rows),
        "snapshot_import": True,
        "supported_kinds": sorted(_ALLOWED_MEDIA_KINDS),
        "filesystem_paths_exposed": False,
    }


@router.post("/api/game-forge/games/{game_id}/project-assets")
def import_project_game_asset(game_id: str, body: AttachGameAssetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    binding = creative_project_name(game)
    try:
        source_project, _element_id = _safe_source_id(body.source_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if binding and source_project != binding:
        raise HTTPException(
            409,
            f"This Game DNA is bound to Creative project '{binding}'. Cross-project asset imports are blocked.",
        )
    if binding:
        _creative_project(binding)
    try:
        record = attach_creative_asset(game, body)
        _invalidate_after_asset_change(game)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "asset": public_asset(record),
        "creative_project_name": binding,
        "project_bound": bool(binding),
        "invalidated_previous_build_and_rating": True,
        "snapshot_import": True,
    }


__all__ = [
    "BindGameProjectRequest",
    "creative_project_name",
    "create_game_in_creative_project",
    "game_project_context",
    "game_project_library",
    "games_in_creative_project",
    "import_project_game_asset",
    "router",
]
