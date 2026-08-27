from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .creative_library import scan_creative_library
from .creative_project import CreativeProjectStore
from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game, remove_public_snapshot, save_game
from .plans import GAME_CREATE
from .tenant_storage import project_path


router = APIRouter(tags=["Aura Game Assets"])

GameAssetKind = Literal["image", "video", "audio", "music"]
_ALLOWED_MEDIA_KINDS = {"image", "video", "audio", "music"}
_ALLOWED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif",
    ".mp4", ".webm", ".mov", ".m4v", ".mkv",
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg",
}
_MAX_ASSET_BYTES = max(1, int(os.getenv("AURA_GAME_ASSET_MAX_BYTES", str(256 * 1024 * 1024))))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GameAssetRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"asset_{uuid4().hex}")
    game_id: str
    kind: GameAssetKind
    label: str = Field(min_length=1, max_length=200)
    role: str = Field(default="game asset", max_length=120)
    source_type: str = Field(default="creative_library", max_length=80)
    source_project: str = Field(min_length=1, max_length=120)
    source_element_id: str = Field(min_length=1, max_length=160)
    source_element_updated_at: str = ""
    source_media_sha256: str = Field(min_length=64, max_length=64)
    imported_filename: str = Field(min_length=1, max_length=240)
    byte_size: int = Field(ge=0)
    rights_confirmed: bool = False
    rights_attestation: str = Field(default="", max_length=2000)
    imported_at: str = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)


class GameAssetManifest(BaseModel):
    schema_version: int = 1
    game_id: str
    assets: list[GameAssetRecord] = Field(default_factory=list, max_length=500)
    updated_at: str = Field(default_factory=_now)


class AttachGameAssetRequest(BaseModel):
    source_id: str = Field(min_length=3, max_length=300)
    role: str = Field(default="game asset", max_length=120)
    rights_confirmed: bool = False
    rights_attestation: str = Field(default="", max_length=2000)


class UpdateGameAssetRightsRequest(BaseModel):
    rights_confirmed: bool
    rights_attestation: str = Field(default="", max_length=2000)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game asset editing unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise HTTPException(409, "Reopen this game before changing its assets.")


def _asset_root(game_id: str) -> Path:
    parent = game_dir(game_id).resolve()
    target = (parent / "assets").resolve()
    if parent not in target.parents:
        raise ValueError("Game asset storage escaped the game directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _manifest_path(game_id: str) -> Path:
    return _asset_root(game_id) / "manifest.json"


def load_asset_manifest(game_id: str) -> GameAssetManifest:
    path = _manifest_path(game_id)
    if not path.is_file():
        return GameAssetManifest(game_id=game_id)
    manifest = GameAssetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.game_id != game_id:
        raise ValueError("Game asset manifest identity mismatch")
    return manifest


def save_asset_manifest(manifest: GameAssetManifest) -> GameAssetManifest:
    manifest.updated_at = _now()
    path = _manifest_path(manifest.game_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return manifest


def _safe_source_id(source_id: str) -> tuple[str, str]:
    project_name, separator, element_id = str(source_id or "").partition(":")
    if not separator or not project_name or not element_id:
        raise ValueError("source_id must be '<creative-project>:<element-id>'")
    return project_name, element_id


def _resolve_creative_source(source_id: str):
    project_name, element_id = _safe_source_id(source_id)
    project = project_path(project_name).resolve()
    manifest = CreativeProjectStore(project).load()
    element = next((row for row in manifest.elements if row.id == element_id), None)
    if element is None:
        raise FileNotFoundError("Creative library element not found")
    if element.kind not in _ALLOWED_MEDIA_KINDS:
        raise ValueError("Only image, video, audio and music Creative Library outputs can be imported")
    source_ref = str(element.source_ref or "").strip()
    relative = Path(source_ref)
    if not source_ref or relative.is_absolute() or relative.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise ValueError("Creative Library element does not reference a supported local media file")
    source = (project / relative).resolve()
    if project not in source.parents or not source.is_file():
        raise ValueError("Creative Library media escaped its project or is unavailable")
    byte_size = source.stat().st_size
    if byte_size > _MAX_ASSET_BYTES:
        raise ValueError(f"Game asset exceeds the {_MAX_ASSET_BYTES} byte import limit")
    return project_name, manifest, element, source, byte_size


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_file(game_id: str, record: GameAssetRecord) -> Path:
    root = _asset_root(game_id).resolve()
    target = (root / Path(record.imported_filename).name).resolve()
    if root not in target.parents:
        raise ValueError("Game asset path escaped storage")
    return target


def _validate_rights(confirmed: bool, attestation: str) -> None:
    if confirmed and not str(attestation or "").strip():
        raise ValueError("A rights attestation is required when rights_confirmed is true")


def attach_creative_asset(game: GameDNA, body: AttachGameAssetRequest) -> GameAssetRecord:
    _validate_rights(body.rights_confirmed, body.rights_attestation)
    project_name, manifest, element, source, byte_size = _resolve_creative_source(body.source_id)
    record = GameAssetRecord(
        game_id=game.id,
        kind=element.kind,
        label=element.label,
        role=body.role.strip() or "game asset",
        source_type=element.source_type,
        source_project=project_name,
        source_element_id=element.id,
        source_element_updated_at=element.updated_at,
        source_media_sha256=_sha256(source),
        imported_filename="pending",
        byte_size=byte_size,
        rights_confirmed=body.rights_confirmed,
        rights_attestation=body.rights_attestation.strip(),
        metadata={
            "creative_library_id": body.source_id,
            "source_current_at_import": element.id in set(manifest.active_element_ids),
            "snapshot_import": True,
        },
    )
    record.imported_filename = f"{record.id}{source.suffix.lower()}"
    target = _asset_file(game.id, record)
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(source, tmp)
    if _sha256(tmp) != record.source_media_sha256:
        tmp.unlink(missing_ok=True)
        raise IOError("Imported game asset failed checksum verification")
    tmp.replace(target)
    asset_manifest = load_asset_manifest(game.id)
    asset_manifest.assets.append(record)
    save_asset_manifest(asset_manifest)
    return record


def list_game_assets(game_id: str) -> list[GameAssetRecord]:
    return list(load_asset_manifest(game_id).assets)


def find_game_asset(game_id: str, asset_id: str) -> GameAssetRecord:
    record = next((row for row in list_game_assets(game_id) if row.id == asset_id), None)
    if record is None:
        raise FileNotFoundError(asset_id)
    return record


def update_game_asset_rights(
    game_id: str,
    asset_id: str,
    *,
    rights_confirmed: bool,
    rights_attestation: str,
) -> GameAssetRecord:
    _validate_rights(rights_confirmed, rights_attestation)
    manifest = load_asset_manifest(game_id)
    record = next((row for row in manifest.assets if row.id == asset_id), None)
    if record is None:
        raise FileNotFoundError(asset_id)
    record.rights_confirmed = rights_confirmed
    record.rights_attestation = rights_attestation.strip()
    save_asset_manifest(manifest)
    return record


def detach_game_asset(game_id: str, asset_id: str) -> GameAssetRecord:
    manifest = load_asset_manifest(game_id)
    record = next((row for row in manifest.assets if row.id == asset_id), None)
    if record is None:
        raise FileNotFoundError(asset_id)
    manifest.assets = [row for row in manifest.assets if row.id != asset_id]
    save_asset_manifest(manifest)
    _asset_file(game_id, record).unlink(missing_ok=True)
    return record


def public_asset(record: GameAssetRecord) -> dict:
    return {
        "id": record.id,
        "game_id": record.game_id,
        "kind": record.kind,
        "label": record.label,
        "role": record.role,
        "source_type": record.source_type,
        "source_project": record.source_project,
        "source_element_id": record.source_element_id,
        "source_element_updated_at": record.source_element_updated_at,
        "source_media_sha256": record.source_media_sha256,
        "byte_size": record.byte_size,
        "rights_confirmed": record.rights_confirmed,
        "rights_attestation": record.rights_attestation,
        "imported_at": record.imported_at,
        "metadata": record.metadata,
        "media_url": f"/api/game-forge/games/{record.game_id}/assets/{record.id}/media",
        "filesystem_path_exposed": False,
    }


def asset_integrity_payload(game_id: str) -> list[dict]:
    rows = []
    for record in list_game_assets(game_id):
        rows.append(
            {
                "id": record.id,
                "kind": record.kind,
                "role": record.role,
                "source_project": record.source_project,
                "source_element_id": record.source_element_id,
                "source_element_updated_at": record.source_element_updated_at,
                "source_media_sha256": record.source_media_sha256,
                "byte_size": record.byte_size,
                "rights_confirmed": record.rights_confirmed,
                "rights_attestation": record.rights_attestation,
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def asset_publication_blockers(game_id: str) -> list[str]:
    blockers: list[str] = []
    for record in list_game_assets(game_id):
        if not record.rights_confirmed or not record.rights_attestation.strip():
            blockers.append(f"Rights confirmation is required for game asset '{record.label}'.")
        try:
            path = _asset_file(game_id, record)
            if not path.is_file() or path.stat().st_size != record.byte_size or _sha256(path) != record.source_media_sha256:
                blockers.append(f"Game asset '{record.label}' failed snapshot integrity verification.")
        except (OSError, ValueError):
            blockers.append(f"Game asset '{record.label}' failed snapshot integrity verification.")
    return blockers


def _invalidate_after_asset_change(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


@router.get("/api/game-forge/games/{game_id}/assets/library")
def game_asset_library(game_id: str, request: Request):
    member = _creator(request)
    game = _game(game_id)
    _require_editable(game)
    rows = [row for row in scan_creative_library(member) if row.get("kind") in _ALLOWED_MEDIA_KINDS]
    return {
        "game_id": game.id,
        "items": rows,
        "count": len(rows),
        "snapshot_import": True,
        "supported_kinds": sorted(_ALLOWED_MEDIA_KINDS),
        "filesystem_paths_exposed": False,
    }


@router.get("/api/game-forge/games/{game_id}/assets")
def game_assets(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    rows = list_game_assets(game.id)
    return {
        "game_id": game.id,
        "assets": [public_asset(row) for row in rows],
        "count": len(rows),
        "build_integrity_bound_to_assets": True,
        "filesystem_paths_exposed": False,
    }


@router.post("/api/game-forge/games/{game_id}/assets")
def import_game_asset(game_id: str, body: AttachGameAssetRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    try:
        record = attach_creative_asset(game, body)
        _invalidate_after_asset_change(game)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "asset": public_asset(record),
        "invalidated_previous_build_and_rating": True,
        "snapshot_import": True,
    }


@router.patch("/api/game-forge/games/{game_id}/assets/{asset_id}/rights")
def change_game_asset_rights(
    game_id: str,
    asset_id: str,
    body: UpdateGameAssetRightsRequest,
    request: Request,
):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    try:
        record = update_game_asset_rights(
            game.id,
            asset_id,
            rights_confirmed=body.rights_confirmed,
            rights_attestation=body.rights_attestation,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "Game asset not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _invalidate_after_asset_change(game)
    return {"asset": public_asset(record), "invalidated_previous_build_and_rating": True}


@router.delete("/api/game-forge/games/{game_id}/assets/{asset_id}")
def delete_game_asset(game_id: str, asset_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    try:
        record = detach_game_asset(game.id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Game asset not found") from exc
    _invalidate_after_asset_change(game)
    return {
        "deleted": True,
        "asset_id": record.id,
        "invalidated_previous_build_and_rating": True,
    }


@router.get(
    "/api/game-forge/games/{game_id}/assets/{asset_id}/media",
    response_class=FileResponse,
    include_in_schema=False,
)
def game_asset_media(game_id: str, asset_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        record = find_game_asset(game.id, asset_id)
        path = _asset_file(game.id, record)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Game asset not found") from exc
    if not path.is_file():
        raise HTTPException(409, "Imported game asset is unavailable")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=None,
        headers={
            "Cache-Control": "private, no-store",
            "ETag": f"\"{record.source_media_sha256}\"",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = [
    "router",
    "GameAssetRecord",
    "GameAssetManifest",
    "AttachGameAssetRequest",
    "UpdateGameAssetRightsRequest",
    "attach_creative_asset",
    "list_game_assets",
    "find_game_asset",
    "update_game_asset_rights",
    "detach_game_asset",
    "public_asset",
    "asset_integrity_payload",
    "asset_publication_blockers",
]
