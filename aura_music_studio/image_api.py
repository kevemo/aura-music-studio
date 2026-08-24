from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .image_editing import ImageEditError, ImageEditRequest, ImageEditingService
from .image_generation import ImageGenerationError, ImageGenerationRequest, ImageGenerationService
from .image_jobs import ImageJobStore
from .plans import (
    ADVANCED_IMAGE_GENERATION,
    IMAGE_GENERATION,
    IMAGE_HIGH_QUALITY,
    IMAGE_PROVIDER_CONTROL,
    IMAGE_TRANSPARENT_BACKGROUND,
    POSTER_GENERATION,
)

router = APIRouter(prefix="/api/image", tags=["image"])
service = ImageGenerationService()
editing = ImageEditingService(service.output_root)
job_store = ImageJobStore(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")


class GenerateImageBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=32000)
    mode: str = "image"
    aspect_ratio: str = "1:1"
    quality: str = "standard"
    provider: str = "auto"
    background: str = "opaque"
    project_id: str | None = None
    title_text: str | None = Field(default=None, max_length=500)
    subtitle_text: str | None = Field(default=None, max_length=1000)
    call_to_action: str | None = Field(default=None, max_length=500)
    brand_direction: str | None = Field(default=None, max_length=4000)


class EditImageBody(BaseModel):
    source_job_id: str = Field(min_length=8, max_length=120)
    prompt: str = Field(min_length=1, max_length=32000)
    quality: str = "standard"
    provider: str = "auto"
    aspect_ratio: str = "1:1"
    preserve_subject: bool = True
    edit_strength: float = Field(default=0.65, ge=0.0, le=1.0)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(IMAGE_GENERATION):
        raise HTTPException(403, "Image & Poster Studio unlocks on Base ($4.99/month)")
    return member


def _enforce_generation_tier(member, body: GenerateImageBody) -> None:
    plan = member.plan
    if body.mode == "poster" and not plan.has(POSTER_GENERATION):
        raise HTTPException(403, "Poster generation requires Base or Pro")
    if body.quality.strip().lower() != "standard" and not plan.has(IMAGE_HIGH_QUALITY):
        raise HTTPException(403, "High-quality/professional image rendering is a Pro feature")
    if body.provider.strip().lower() != "auto" and not plan.has(IMAGE_PROVIDER_CONTROL):
        raise HTTPException(403, "Manual image-engine selection is a Pro feature")
    if body.background.strip().lower() == "transparent" and not plan.has(IMAGE_TRANSPARENT_BACKGROUND):
        raise HTTPException(403, "Transparent image generation is a Pro feature")
    advanced_fields = [body.subtitle_text, body.call_to_action, body.brand_direction]
    if any(value and value.strip() for value in advanced_fields) and not plan.has(ADVANCED_IMAGE_GENERATION):
        raise HTTPException(403, "Advanced poster/image direction requires Pro")


def _enforce_edit_tier(member, body: EditImageBody) -> None:
    plan = member.plan
    if body.quality.strip().lower() != "standard" and not plan.has(IMAGE_HIGH_QUALITY):
        raise HTTPException(403, "High-quality image editing requires Pro")
    if body.provider.strip().lower() != "auto" and not plan.has(IMAGE_PROVIDER_CONTROL):
        raise HTTPException(403, "Manual image-editor selection requires Pro")
    advanced = (not body.preserve_subject) or abs(float(body.edit_strength) - 0.65) > 1e-9
    if advanced and not plan.has(ADVANCED_IMAGE_GENERATION):
        raise HTTPException(403, "Advanced edit strength/composition control requires Pro")


def _owned_completed_source(user_id: str, source_job_id: str) -> tuple[dict, Path]:
    source_job = job_store.get_for_user(user_id, source_job_id)
    if not source_job:
        raise HTTPException(404, "Source image job not found")
    if source_job.get("status") != "completed" or not source_job.get("output_path"):
        raise HTTPException(409, "Source image is not ready for editing")
    root = service.output_root.resolve()
    source = Path(source_job["output_path"]).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise HTTPException(404, "Source image output is unavailable")
    return source_job, source


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("/generate")
def generate_image(body: GenerateImageBody, request: Request):
    member = _member(request)
    _enforce_generation_tier(member, body)
    try:
        result = service.generate(ImageGenerationRequest(**body.model_dump()))
    except ImageGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provenance_hash = service.provenance_hash(result)
    job_store.save(
        user_id=member.user_id,
        result=result.to_dict(),
        mode=body.mode,
        prompt=body.prompt,
        project_id=body.project_id,
        provenance_hash=provenance_hash,
    )
    return {
        **result.to_dict(),
        "provenance_hash": provenance_hash,
        "membership_tier": member.plan.id,
        "non_destructive": True,
    }


@router.post("/edit")
def edit_image(body: EditImageBody, request: Request):
    """Create a child revision of a user-owned completed image without overwriting the source."""
    member = _member(request)
    _enforce_edit_tier(member, body)
    source_job, source = _owned_completed_source(member.user_id, body.source_job_id)
    source_sha = _sha256(source)
    edit_request = ImageEditRequest(
        source_job_id=body.source_job_id,
        prompt=body.prompt,
        quality=body.quality,
        provider=body.provider,
        aspect_ratio=body.aspect_ratio,
        project_id=source_job.get("project_id"),
        preserve_subject=body.preserve_subject,
        edit_strength=body.edit_strength,
    )
    try:
        result = editing.edit(source, edit_request)
    except ImageEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provenance_hash = editing.provenance_hash(result, source_sha256=source_sha)
    job_store.save(
        user_id=member.user_id,
        result=result.to_dict(),
        mode="edit",
        prompt=body.prompt,
        project_id=source_job.get("project_id"),
        provenance_hash=provenance_hash,
    )
    lineage = job_store.save_edit_lineage(
        user_id=member.user_id,
        parent_job_id=body.source_job_id,
        child_job_id=result.id,
        edit_prompt=body.prompt,
        source_sha256=source_sha,
    )
    return {
        **result.to_dict(),
        "provenance_hash": provenance_hash,
        "parent_job_id": body.source_job_id,
        "lineage": lineage,
        "non_destructive": True,
        "source_preserved": True,
        "membership_tier": member.plan.id,
    }


@router.get("/jobs")
def list_image_jobs(request: Request, limit: int = 50):
    member = _member(request)
    return {"jobs": job_store.list_for_user(member.user_id, limit=limit)}


@router.get("/jobs/{job_id}/lineage")
def image_job_lineage(job_id: str, request: Request):
    member = _member(request)
    try:
        return job_store.lineage_for_user(member.user_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, "Image job not found") from exc


@router.get("/jobs/{job_id}/download")
def download_image_job(job_id: str, request: Request):
    member = _member(request)
    job = job_store.get_for_user(member.user_id, job_id)
    if not job:
        raise HTTPException(404, "Image job not found")
    if job["status"] != "completed" or not job.get("output_path"):
        raise HTTPException(409, "Image is not ready for download")
    root = service.output_root.resolve()
    output = Path(job["output_path"]).resolve()
    if not output.is_relative_to(root) or not output.is_file():
        raise HTTPException(404, "Image output is unavailable")
    return FileResponse(output, media_type="image/png", filename=f"4infinity-creative-studios-{job_id}.png")


@router.get("/capabilities")
def image_capabilities():
    return {
        "minimum_plan": "base",
        "minimum_plan_price_usd": "4.99",
        "modes": ["image", "poster", "cover_art", "social_graphic", "thumbnail"],
        "editing": {
            "non_destructive": True,
            "source_job_lineage": True,
            "source_never_overwritten": True,
            "voice_or_text_via_aura": True,
        },
        "aspect_ratios": ["1:1", "4:5", "3:2", "2:3", "16:9", "9:16"],
        "providers": ["auto", "local", "openai"],
        "recommended_openai_model": "gpt-image-2",
        "tiers": {
            "free": {"generation": False, "editing": False},
            "base": {
                "generation": True,
                "editing": True,
                "poster_generation": True,
                "quality": ["standard"],
                "provider_selection": "automatic",
                "background": ["opaque", "auto"],
                "basic_editing": True,
                "basic_visual_fx": True,
            },
            "pro": {
                "generation": True,
                "editing": True,
                "poster_generation": True,
                "quality": ["standard", "high", "professional"],
                "provider_selection": "automatic_or_manual",
                "transparent_background": True,
                "advanced_image_edit": True,
                "edit_strength_control": True,
                "composition_reinterpretation": True,
                "layer_compositor": True,
                "brand_kit_compositor": True,
                "visual_fx_studio": True,
            },
        },
    }
