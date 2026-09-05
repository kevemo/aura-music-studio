from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .game_forge_mesh import GameModelError, extract_static_mesh
from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game, remove_public_snapshot, save_game
from .plans import GAME_CREATE


router = APIRouter(tags=["Aura Game Models"])
_MODEL_SUFFIXES = {".glb", ".gltf"}
_MAX_MODEL_BYTES = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_BYTES", str(64 * 1024 * 1024))))
_MAX_MODELS = max(1, int(os.getenv("AURA_GAME_MAX_MODELS", "100")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GameModelAssetRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"model_{uuid4().hex}")
    game_id: str
    kind: Literal["model"] = "model"
    label: str = Field(min_length=1, max_length=200)
    role: str = Field(default="3d model", max_length=120)
    original_filename: str = Field(default="model.glb", max_length=240)
    stored_filename: str = Field(min_length=1, max_length=240)
    source_sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=1)
    rights_confirmed: bool = False
    rights_attestation: str = Field(default="", max_length=2000)
    imported_at: str = Field(default_factory=_now)
    mesh_summary: dict = Field(default_factory=dict)


class GameModelManifest(BaseModel):
    schema_version: int = 1
    game_id: str
    models: list[GameModelAssetRecord] = Field(default_factory=list, max_length=100)
    updated_at: str = Field(default_factory=_now)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "3D model import unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise HTTPException(409, "Reopen this game before changing its 3D models")


def _model_root(game_id: str) -> Path:
    parent = game_dir(game_id).resolve()
    root = (parent / "models").resolve()
    if parent not in root.parents:
        raise ValueError("Game model storage escaped the game directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manifest_path(game_id: str) -> Path:
    return _model_root(game_id) / "manifest.json"


def load_model_manifest(game_id: str) -> GameModelManifest:
    path = _manifest_path(game_id)
    if not path.is_file():
        return GameModelManifest(game_id=game_id)
    manifest = GameModelManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.game_id != game_id:
        raise ValueError("Game model manifest identity mismatch")
    return manifest


def save_model_manifest(manifest: GameModelManifest) -> GameModelManifest:
    manifest.updated_at = _now()
    path = _manifest_path(manifest.game_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return manifest


def _model_file(game_id: str, record: GameModelAssetRecord) -> Path:
    root = _model_root(game_id).resolve()
    target = (root / Path(record.stored_filename).name).resolve()
    if root not in target.parents:
        raise ValueError("Game model path escaped storage")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_capabilities(mesh_summary: dict | None = None) -> dict:
    summary = mesh_summary or {}
    animations_present = bool(summary.get("animations_present"))
    skins_present = bool(summary.get("skins_present"))
    warnings: list[str] = []
    if animations_present:
        warnings.append("Animation clips are present in the source asset but are not executed by the current Aura3D static-model runtime.")
    if skins_present:
        warnings.append("Skin/rig data is present in the source asset but skinning is not executed by the current Aura3D static-model runtime.")
    return {
        "projection_mode": "closed_static_mesh",
        "runtime_mesh_projection": True,
        "skeletal_animation_runtime": False,
        "skinning_runtime": False,
        "animation_clips_runtime": False,
        "animations_present": animations_present,
        "skins_present": skins_present,
        "source_animation_or_skin_data_executed": False,
        "embedded_materials_executed": False,
        "external_resources_allowed": False,
        "runtime_network_required": False,
        "warnings": warnings,
    }


def _public_model(record: GameModelAssetRecord) -> dict:
    return {
        "id": record.id,
        "game_id": record.game_id,
        "kind": "model",
        "label": record.label,
        "role": record.role,
        "original_filename": record.original_filename,
        "sha256": record.source_sha256,
        "byte_size": record.byte_size,
        "rights_confirmed": record.rights_confirmed,
        "rights_attestation": record.rights_attestation,
        "imported_at": record.imported_at,
        "mesh_summary": record.mesh_summary,
        "runtime_capabilities": _runtime_capabilities(record.mesh_summary),
        "raw_model_browser_url": None,
        "filesystem_path_exposed": False,
    }


def list_game_models(game_id: str) -> list[GameModelAssetRecord]:
    return list(load_model_manifest(game_id).models)


def find_game_model(game_id: str, model_id: str) -> GameModelAssetRecord:
    record = next((row for row in list_game_models(game_id) if row.id == model_id), None)
    if record is None:
        raise FileNotFoundError(model_id)
    return record


def _verified_mesh(record: GameModelAssetRecord) -> dict:
    path = _model_file(record.game_id, record)
    if not path.is_file() or path.stat().st_size != record.byte_size or _sha256(path) != record.source_sha256:
        raise GameModelError(f"Game model '{record.label}' failed snapshot integrity verification")
    return extract_static_mesh(path)


def runtime_model_manifest(game_id: str) -> list[dict]:
    rows: list[dict] = []
    for record in list_game_models(game_id):
        mesh = _verified_mesh(record)
        rows.append(
            {
                "id": record.id,
                "kind": "model",
                "label": record.label,
                "role": record.role,
                "sha256": record.source_sha256,
                "byte_size": record.byte_size,
                "mesh": mesh,
                "runtime_capabilities": _runtime_capabilities(mesh),
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def model_integrity_payload(game_id: str) -> list[dict]:
    rows = []
    for record in list_game_models(game_id):
        rows.append(
            {
                "id": record.id,
                "kind": "model",
                "role": record.role,
                "source_sha256": record.source_sha256,
                "byte_size": record.byte_size,
                "rights_confirmed": record.rights_confirmed,
                "rights_attestation": record.rights_attestation,
                "mesh_summary": record.mesh_summary,
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def model_publication_blockers(game_id: str) -> list[str]:
    blockers: list[str] = []
    for record in list_game_models(game_id):
        if not record.rights_confirmed or not record.rights_attestation.strip():
            blockers.append(f"Rights confirmation is required for game model '{record.label}'.")
        try:
            _verified_mesh(record)
        except (OSError, ValueError, GameModelError):
            blockers.append(f"Game model '{record.label}' failed closed-mesh integrity verification.")
    return blockers


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


async def _store_upload(game: GameDNA, upload: UploadFile, *, label: str, role: str, rights_confirmed: bool, rights_attestation: str) -> GameModelAssetRecord:
    original_name = Path(str(upload.filename or "model.glb")).name
    suffix = Path(original_name).suffix.lower()
    if suffix not in _MODEL_SUFFIXES:
        raise GameModelError("Aura3D model upload accepts .glb or .gltf only")
    if rights_confirmed and not rights_attestation.strip():
        raise GameModelError("A rights attestation is required when model rights are confirmed")
    manifest = load_model_manifest(game.id)
    if len(manifest.models) >= _MAX_MODELS:
        raise GameModelError(f"This game has reached its {_MAX_MODELS} model asset limit")

    record_id = f"model_{uuid4().hex}"
    root = _model_root(game.id)
    temporary = root / f".{record_id}.upload{suffix}"
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_MODEL_BYTES:
                    raise GameModelError(f"3D model exceeds the {_MAX_MODEL_BYTES} byte import limit")
                digest.update(chunk)
                handle.write(chunk)
        if total <= 0:
            raise GameModelError("Uploaded 3D model is empty")
        mesh = extract_static_mesh(temporary)
        summary = {key: value for key, value in mesh.items() if key != "vertices"}
        final_name = f"{record_id}{suffix}"
        final_path = (root / final_name).resolve()
        if root.resolve() not in final_path.parents:
            raise GameModelError("Game model destination escaped storage")
        temporary.replace(final_path)
        record = GameModelAssetRecord(
            id=record_id,
            game_id=game.id,
            label=(label.strip() or Path(original_name).stem or "3D Model")[:200],
            role=(role.strip() or "3d model")[:120],
            original_filename=original_name[:240],
            stored_filename=final_name,
            source_sha256=digest.hexdigest(),
            byte_size=total,
            rights_confirmed=rights_confirmed,
            rights_attestation=rights_attestation.strip(),
            mesh_summary=summary,
        )
        manifest.models.append(record)
        save_model_manifest(manifest)
        _invalidate(game)
        return record
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@router.get("/api/game-forge/games/{game_id}/models")
def game_models(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    rows = list_game_models(game.id)
    return {
        "game_id": game.id,
        "models": [_public_model(row) for row in rows],
        "count": len(rows),
        "supported_formats": ["glb", "gltf-embedded"],
        "external_gltf_resources_allowed": False,
        "runtime_mesh_projection": True,
        "model_runtime_capabilities": _runtime_capabilities(),
        "filesystem_paths_exposed": False,
    }


@router.post("/api/game-forge/games/{game_id}/models")
async def import_game_model(
    game_id: str,
    request: Request,
    file: UploadFile = File(...),
    label: str = Form(default=""),
    role: str = Form(default="3d model"),
    rights_confirmed: bool = Form(default=False),
    rights_attestation: str = Form(default=""),
):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    try:
        record = await _store_upload(
            game,
            file,
            label=label,
            role=role,
            rights_confirmed=rights_confirmed,
            rights_attestation=rights_attestation,
        )
    except (GameModelError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "model": _public_model(record),
        "invalidated_previous_build_and_rating": True,
        "runtime_network_required": False,
        "generated_model_code_executed": False,
        "runtime_capabilities": _runtime_capabilities(record.mesh_summary),
    }


@router.delete("/api/game-forge/games/{game_id}/models/{model_id}")
def delete_game_model(game_id: str, model_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    manifest = load_model_manifest(game.id)
    record = next((row for row in manifest.models if row.id == model_id), None)
    if record is None:
        raise HTTPException(404, "Game model not found")
    from .game_forge_model_bindings import clear_model_bindings

    bindings_removed = clear_model_bindings(game.id, model_id)
    manifest.models = [row for row in manifest.models if row.id != model_id]
    save_model_manifest(manifest)
    _model_file(game.id, record).unlink(missing_ok=True)
    _invalidate(game)
    return {
        "deleted": True,
        "model_id": model_id,
        "bindings_removed": bindings_removed,
        "invalidated_previous_build_and_rating": True,
    }


__all__ = [
    "router",
    "GameModelAssetRecord",
    "GameModelManifest",
    "load_model_manifest",
    "save_model_manifest",
    "list_game_models",
    "find_game_model",
    "runtime_model_manifest",
    "model_integrity_payload",
    "model_publication_blockers",
]
