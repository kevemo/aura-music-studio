from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .game_forge_assets import (
    asset_publication_blockers,
    private_runtime_asset_path,
    runtime_asset_manifest,
)
from .game_forge_integrity import game_integrity_hash
from .game_forge_models import ENGINE_REGISTRY, GameDNA
from .game_forge_state_machine_runtime import private_play_html
from .game_forge_store import game_dir, load_game
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Export"])

ExportTarget = Literal["aura_web", "phaser4", "playcanvas", "babylon", "godot"]

_EXPORT_CAPABILITIES: dict[str, dict] = {
    "aura_web": {
        "label": "Aura Web Package",
        "production_ready": True,
        "executable_export": True,
        "format": "deterministic_zip",
        "runtime": "existing reviewed Aura playtest runtime",
    },
    "phaser4": {
        "label": "Phaser 4 adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The Phaser adapter is still planned and is not presented as generated executable code.",
    },
    "playcanvas": {
        "label": "PlayCanvas adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The PlayCanvas adapter is still planned and is not presented as generated executable code.",
    },
    "babylon": {
        "label": "Babylon.js adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The Babylon.js adapter is still planned and is not presented as generated executable code.",
    },
    "godot": {
        "label": "Godot adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The Godot adapter is still planned and is not presented as generated executable code.",
    },
}


class CreateGameExportRequest(BaseModel):
    target: ExportTarget = "aura_web"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game export unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _exports_root(game_id: str) -> Path:
    parent = game_dir(game_id).resolve()
    target = (parent / "exports").resolve()
    if parent not in target.parents:
        raise ValueError("Game export storage escaped the game directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_export_id(export_id: str) -> str:
    value = str(export_id or "").strip()
    if not value.startswith("export_") or not value[7:].isalnum() or len(value) > 80:
        raise ValueError("Invalid game export id")
    return value


def _export_path(game_id: str, export_id: str) -> Path:
    root = _exports_root(game_id).resolve()
    target = (root / f"{_safe_export_id(export_id)}.zip").resolve()
    if root not in target.parents:
        raise ValueError("Game export path escaped storage")
    return target


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_info(name: str) -> ZipInfo:
    # Fixed metadata makes identical logical inputs byte-for-byte reproducible.
    info = ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _write_zip_entry(zf: ZipFile, name: str, data: bytes) -> None:
    clean = str(name or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        raise ValueError("Unsafe export archive path")
    zf.writestr(_zip_info(clean), data)


def _validate_exportable(game: GameDNA, target: ExportTarget) -> str:
    capability = _EXPORT_CAPABILITIES[target]
    if not capability["production_ready"]:
        raise ValueError(str(capability.get("reason") or "Requested export adapter is not production ready"))
    if target != "aura_web":
        raise ValueError("Only the Aura Web export is production ready")
    if not game.rights_confirmed or not str(game.rights_attestation or "").strip():
        raise ValueError("Confirm the project's creation/usage rights before export")
    current_hash = game_integrity_hash(game)
    if not game.latest_build or game.latest_build.content_hash != current_hash:
        raise ValueError("Game build is missing or stale. Rebuild the current game before export")
    blockers = asset_publication_blockers(game.id)
    if blockers:
        raise ValueError("; ".join(blockers))
    return current_hash


def create_aura_web_export(game: GameDNA) -> dict:
    content_hash = _validate_exportable(game, "aura_web")
    html = private_play_html(game).encode("utf-8")
    assets = runtime_asset_manifest(game.id)
    media_entries: list[dict] = []
    media_bytes: list[tuple[str, bytes]] = []
    for row in assets:
        media_url = str(row.get("media_url") or "")
        if not media_url.startswith("media/"):
            raise ValueError("Runtime media URL escaped the same-origin export contract")
        filename = media_url.split("/", 1)[1]
        path, record = private_runtime_asset_path(game.id, filename)
        data = path.read_bytes()
        if len(data) != int(row.get("byte_size") or -1) or _sha256_bytes(data) != str(row.get("sha256") or ""):
            raise ValueError(f"Game asset '{record.label}' failed export integrity verification")
        media_entries.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "label": row["label"],
                "role": row["role"],
                "media_url": media_url,
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "byte_size": row["byte_size"],
            }
        )
        media_bytes.append((media_url, data))

    export_id = f"export_{uuid4().hex}"
    manifest = {
        "schema_version": 1,
        "export_id": export_id,
        "target": "aura_web",
        "product": "Elevate Souls Productions Content Creation Command Center",
        "powered_by": "Aura AI",
        "game": {
            "id": game.id,
            "title": game.title,
            "genre": game.genre,
            "dimension": game.dimension,
            "requested_engine": game.engine_target,
            "build_id": game.latest_build.build_id,
            "runtime": game.latest_build.runtime,
            "content_hash": content_hash,
        },
        "assets": media_entries,
        "provenance": {
            "generated_at": _now(),
            "game_rights_confirmed": True,
            "asset_rights_verified_before_export": True,
            "content_integrity_bound": True,
            "same_origin_media_only": True,
            "creator_private_paths_included": False,
            "server_secrets_included": False,
            "session_data_included": False,
            "external_network_dependency_added": False,
            "llm_generated_executable_code_included": False,
        },
        "launch": {"entrypoint": "play.html", "serve_over_http": True},
    }

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
        _write_zip_entry(zf, "play.html", html)
        _write_zip_entry(
            zf,
            "manifest.json",
            (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        for name, data in sorted(media_bytes, key=lambda item: item[0]):
            _write_zip_entry(zf, name, data)
    payload = buffer.getvalue()
    target = _export_path(game.id, export_id)
    tmp = target.with_suffix(".zip.tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)
    return {
        "export_id": export_id,
        "target": "aura_web",
        "filename": target.name,
        "byte_size": len(payload),
        "sha256": _sha256_bytes(payload),
        "content_hash": content_hash,
        "runtime": game.latest_build.runtime,
        "asset_count": len(media_entries),
        "download_url": f"/api/game-forge/games/{game.id}/exports/{export_id}/download",
        "production_ready": True,
        "creator_private_paths_included": False,
        "server_secrets_included": False,
        "llm_generated_executable_code_included": False,
    }


def export_capabilities() -> dict:
    engines = {
        key: {
            "label": value.get("label"),
            "adapter_stage": value.get("adapter_stage"),
            "native": bool(value.get("native")),
        }
        for key, value in ENGINE_REGISTRY.items()
    }
    return {
        "targets": _EXPORT_CAPABILITIES,
        "engine_registry": engines,
        "production_ready_targets": ["aura_web"],
        "external_adapters_claimed_ready": False,
    }


@router.get("/api/game-forge/games/{game_id}/exports/capabilities")
def game_export_capabilities(game_id: str, request: Request):
    _creator(request)
    _game(game_id)
    return {"game_id": game_id, **export_capabilities()}


@router.post("/api/game-forge/games/{game_id}/exports")
def create_game_export(game_id: str, body: CreateGameExportRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        if body.target != "aura_web":
            _validate_exportable(game, body.target)
        return create_aura_web_export(game)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get(
    "/api/game-forge/games/{game_id}/exports/{export_id}/download",
    response_class=FileResponse,
    include_in_schema=False,
)
def download_game_export(game_id: str, export_id: str, request: Request):
    _creator(request)
    _game(game_id)
    try:
        path = _export_path(game_id, export_id)
    except ValueError as exc:
        raise HTTPException(404, "Game export not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Game export not found")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = [
    "router",
    "CreateGameExportRequest",
    "export_capabilities",
    "create_aura_web_export",
]
