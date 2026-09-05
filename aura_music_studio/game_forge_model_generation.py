from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_api import _creator
from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game

router = APIRouter(tags=["Aura Game 3D Generation"])

GENERATION_SCHEMA_VERSION = 1
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
GenerationState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
GenerationCapability = Literal["text_to_3d", "image_to_3d"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _correlation(request: Request) -> str:
    supplied = str(request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID") or "").strip()
    if supplied and _OPAQUE_REF.fullmatch(supplied):
        return supplied
    return f"corr_{uuid4().hex}"


def _opaque(value: str | None, *, name: str, required: bool = False) -> str | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not _OPAQUE_REF.fullmatch(clean):
        raise ValueError(f"{name} must be an opaque identifier")
    return clean


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, {"code": "project_unauthorised", "message": "Game project not found"}) from exc


def _job_root(game_id: str) -> Path:
    parent = game_dir(game_id).resolve()
    root = (parent / "generation_jobs").resolve()
    if parent not in root.parents:
        raise ValueError("Generation job storage escaped the game directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_path(game_id: str, job_id: str) -> Path:
    clean = _opaque(job_id, name="job_id", required=True)
    root = _job_root(game_id)
    path = (root / f"{clean}.json").resolve()
    if root not in path.parents:
        raise ValueError("Generation job path escaped storage")
    return path


class CreateModelGenerationRequest(BaseModel):
    capability: GenerationCapability
    prompt: str = Field(min_length=1, max_length=3000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    quality_profile: Literal["draft", "standard", "high"] = "standard"
    target_poly_budget: int | None = Field(default=None, ge=100, le=5_000_000)
    generate_textures: bool = True
    entitlement_ref: str | None = Field(default=None, max_length=160)
    idempotency_key: str | None = Field(default=None, max_length=160)


class ModelGenerationJob(BaseModel):
    schema_version: int = GENERATION_SCHEMA_VERSION
    generation_request_id: str
    project_id: str
    target_asset_kind: Literal["model"] = "model"
    capability: GenerationCapability
    provider: str
    prompt_sha256: str
    reference_asset_ids: list[str] = Field(default_factory=list)
    quality_profile: Literal["draft", "standard", "high"]
    target_poly_budget: int | None = None
    generate_textures: bool
    state: GenerationState
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    provider_result_ref: str | None = None
    validation_state: Literal["pending", "validated", "failed"] = "pending"
    final_asset_version_ref: str | None = None
    entitlement_ref: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    correlation_id: str
    provenance: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @property
    def terminal(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}


def _provider_enabled(provider: str) -> bool:
    key = re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_")
    return os.getenv(f"AURA_GAME_3D_PROVIDER_{key}_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _default_provider() -> str | None:
    value = str(os.getenv("AURA_GAME_3D_PROVIDER", "")).strip()
    return value or None


def _save_job(job: ModelGenerationJob) -> ModelGenerationJob:
    job.updated_at = _now()
    path = _job_path(job.project_id, job.generation_request_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return job


def _load_job(game_id: str, job_id: str) -> ModelGenerationJob:
    path = _job_path(game_id, job_id)
    if not path.is_file():
        raise FileNotFoundError(job_id)
    job = ModelGenerationJob.model_validate_json(path.read_text(encoding="utf-8"))
    if job.project_id != game_id:
        raise ValueError("Generation job project identity mismatch")
    return job


def list_generation_jobs(game_id: str) -> list[ModelGenerationJob]:
    root = _job_root(game_id)
    rows: list[ModelGenerationJob] = []
    for path in sorted(root.glob("gfgen_*.json")):
        try:
            job = ModelGenerationJob.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if job.project_id == game_id:
            rows.append(job)
    return sorted(rows, key=lambda row: row.created_at, reverse=True)


def _job_public(job: ModelGenerationJob) -> dict:
    # Prompt text is intentionally not persisted or returned. The hash is enough for
    # idempotency/audit without turning project prompts into operational telemetry.
    return job.model_dump(mode="json")


def _request_fingerprint(game_id: str, creator_id: str, provider: str, body: CreateModelGenerationRequest) -> str:
    references = [_opaque(row, name="reference_asset_id", required=True) for row in body.reference_asset_ids]
    entitlement = _opaque(body.entitlement_ref, name="entitlement_ref")
    supplied = _opaque(body.idempotency_key, name="idempotency_key")
    if supplied:
        material = f"{creator_id}|{game_id}|{supplied}"
    else:
        material = "|".join(
            [
                creator_id,
                game_id,
                provider,
                body.capability,
                body.quality_profile,
                str(body.target_poly_budget or ""),
                str(bool(body.generate_textures)),
                entitlement or "",
                hashlib.sha256(body.prompt.encode("utf-8")).hexdigest(),
                ",".join(references),
            ]
        )
    return f"gfgen_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:28]}"


def create_generation_job(game: GameDNA, creator_id: str, body: CreateModelGenerationRequest, *, correlation_id: str) -> tuple[ModelGenerationJob, bool]:
    provider = str(body.provider or _default_provider() or "unconfigured").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", provider):
        raise ValueError("provider must be a safe provider identifier")
    references = [_opaque(row, name="reference_asset_id", required=True) for row in body.reference_asset_ids]
    if body.capability == "image_to_3d" and not references:
        raise ValueError("image_to_3d requires at least one project-owned reference asset ID")
    entitlement = _opaque(body.entitlement_ref, name="entitlement_ref")
    job_id = _request_fingerprint(game.id, creator_id, provider, body)
    try:
        existing = _load_job(game.id, job_id)
        return existing, True
    except FileNotFoundError:
        pass

    enabled = provider != "unconfigured" and _provider_enabled(provider)
    job = ModelGenerationJob(
        generation_request_id=job_id,
        project_id=game.id,
        capability=body.capability,
        provider=provider,
        prompt_sha256=hashlib.sha256(body.prompt.encode("utf-8")).hexdigest(),
        reference_asset_ids=references,
        quality_profile=body.quality_profile,
        target_poly_budget=body.target_poly_budget,
        generate_textures=body.generate_textures,
        entitlement_ref=entitlement,
        state="queued" if enabled else "failed",
        error_code=None if enabled else "generation_provider_unavailable",
        error_message=None if enabled else "No configured 3D generation provider is available for this capability.",
        correlation_id=correlation_id,
        provenance={
            "origin": "ai_generated_request",
            "provider": provider,
            "commercial_use_clearance_asserted": False,
            "reference_count": len(references),
        },
    )
    return _save_job(job), False


def claim_generation_job(game_id: str, job_id: str, *, provider: str, correlation_id: str) -> ModelGenerationJob:
    """Internal worker hook. Chat 10/provider workers call this after authoritative queue ownership."""
    job = _load_job(game_id, job_id)
    if job.state != "queued":
        raise ValueError("Only queued generation jobs can be claimed")
    if job.provider != provider:
        raise ValueError("Generation provider mismatch")
    job.state = "running"
    job.progress_percent = None
    job.correlation_id = correlation_id
    return _save_job(job)


def report_generation_progress(game_id: str, job_id: str, *, provider: str, progress_percent: int, correlation_id: str) -> ModelGenerationJob:
    """Internal worker hook. Progress is stored only when a provider/worker reports it."""
    job = _load_job(game_id, job_id)
    if job.state != "running":
        raise ValueError("Only running generation jobs can report progress")
    if job.provider != provider:
        raise ValueError("Generation provider mismatch")
    progress = int(progress_percent)
    if progress < 0 or progress > 99:
        raise ValueError("Running generation progress must be between 0 and 99")
    job.progress_percent = progress
    job.correlation_id = correlation_id
    return _save_job(job)


def fail_generation_job(game_id: str, job_id: str, *, provider: str, error_code: str, error_message: str, correlation_id: str) -> ModelGenerationJob:
    """Internal worker hook. Provider failures never create a successful asset reference."""
    job = _load_job(game_id, job_id)
    if job.state not in {"queued", "running"}:
        raise ValueError("Only queued or running generation jobs can fail")
    if job.provider != provider:
        raise ValueError("Generation provider mismatch")
    job.state = "failed"
    job.progress_percent = None
    job.provider_result_ref = None
    job.final_asset_version_ref = None
    job.validation_state = "failed"
    job.error_code = (_opaque(error_code, name="error_code", required=True) or "generation_failed")[:160]
    job.error_message = str(error_message or "Generation failed")[:600]
    job.correlation_id = correlation_id
    return _save_job(job)


def complete_generation_job(
    game_id: str,
    job_id: str,
    *,
    provider: str,
    provider_result_ref: str,
    final_asset_version_ref: str,
    correlation_id: str,
) -> ModelGenerationJob:
    """Internal worker hook called only after provider output has passed Game Forge asset validation/conversion."""
    job = _load_job(game_id, job_id)
    if job.state != "running":
        raise ValueError("Only running generation jobs can complete")
    if job.provider != provider:
        raise ValueError("Generation provider mismatch")
    result_ref = _opaque(provider_result_ref, name="provider_result_ref", required=True)
    asset_ref = _opaque(final_asset_version_ref, name="final_asset_version_ref", required=True)
    job.state = "succeeded"
    job.progress_percent = 100
    job.provider_result_ref = result_ref
    job.final_asset_version_ref = asset_ref
    job.validation_state = "validated"
    job.error_code = None
    job.error_message = None
    job.correlation_id = correlation_id
    return _save_job(job)


@router.get("/api/game-forge/games/{game_id}/model-generation")
def generation_jobs(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    return {
        "project_id": game.id,
        "jobs": [_job_public(row) for row in list_generation_jobs(game.id)],
        "provider_configured": bool(_default_provider() and _provider_enabled(_default_provider() or "")),
        "supported_capabilities": ["text_to_3d", "image_to_3d"],
        "supported_output_pipeline": ["glb", "gltf-embedded"],
        "provider_progress_simulated": False,
        "sample_model_used_as_generation_result": False,
        "prompt_text_persisted": False,
    }


@router.post("/api/game-forge/games/{game_id}/model-generation")
def request_model_generation(game_id: str, body: CreateModelGenerationRequest, request: Request):
    member = _creator(request)
    game = _game(game_id)
    if not game.actively_editable:
        raise HTTPException(409, {"code": "project_read_only", "message": "Reopen this game before generating 3D assets"})
    creator_id = str(getattr(member, "user_id", "") or "").strip()
    if not creator_id:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Creator identity unavailable"})
    try:
        job, replay = create_generation_job(game, creator_id, body, correlation_id=_correlation(request))
    except ValueError as exc:
        raise HTTPException(400, {"code": "asset_invalid", "message": str(exc)}) from exc
    status = 202 if job.state == "queued" else 503
    if job.state == "failed" and job.error_code == "generation_provider_unavailable":
        # Persisting the failed request gives an honest audit trail while the HTTP status prevents
        # clients from displaying a fake queued/success experience.
        raise HTTPException(status, {"code": job.error_code, "message": job.error_message, "job": _job_public(job), "idempotent_replay": replay})
    return {"job": _job_public(job), "idempotent_replay": replay, "provider_submission_deferred_to_worker": True}


@router.get("/api/game-forge/games/{game_id}/model-generation/{job_id}")
def generation_job(game_id: str, job_id: str, request: Request):
    _creator(request)
    _game(game_id)
    try:
        job = _load_job(game_id, job_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, {"code": "generation_failed", "message": "Generation job not found"}) from exc
    return {"job": _job_public(job)}


@router.delete("/api/game-forge/games/{game_id}/model-generation/{job_id}")
def cancel_generation_job(game_id: str, job_id: str, request: Request):
    _creator(request)
    _game(game_id)
    try:
        job = _load_job(game_id, job_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, {"code": "generation_failed", "message": "Generation job not found"}) from exc
    if job.state in {"succeeded", "failed", "cancelled"}:
        return {"job": _job_public(job), "idempotent": True}
    job.state = "cancelled"
    job.progress_percent = None
    job.error_code = "generation_cancelled"
    job.error_message = "Generation was cancelled before a validated model asset was produced."
    job.correlation_id = _correlation(request)
    _save_job(job)
    return {"job": _job_public(job), "idempotent": False}


__all__ = [
    "CreateModelGenerationRequest",
    "ModelGenerationJob",
    "cancel_generation_job",
    "claim_generation_job",
    "complete_generation_job",
    "create_generation_job",
    "fail_generation_job",
    "list_generation_jobs",
    "report_generation_progress",
    "router",
]
