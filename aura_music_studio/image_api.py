from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

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


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(IMAGE_GENERATION):
        raise HTTPException(403, "Image & Poster Studio unlocks on Base ($4.99/month)")
    return member


def _enforce_tier(member, body: GenerateImageBody) -> None:
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


@router.post("/generate")
def generate_image(body: GenerateImageBody, request: Request):
    member = _member(request)
    _enforce_tier(member, body)
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
    }


@router.get("/jobs")
def list_image_jobs(request: Request, limit: int = 50):
    member = _member(request)
    return {"jobs": job_store.list_for_user(member.user_id, limit=limit)}


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
    return FileResponse(output, media_type="image/png", filename=f"live-sound-studio-{job_id}.png")


@router.get("/capabilities")
def image_capabilities():
    return {
        "minimum_plan": "base",
        "minimum_plan_price_usd": "4.99",
        "modes": ["image", "poster", "cover_art", "social_graphic", "thumbnail"],
        "aspect_ratios": ["1:1", "4:5", "3:2", "2:3", "16:9", "9:16"],
        "providers": ["auto", "local", "openai"],
        "recommended_openai_model": "gpt-image-2",
        "tiers": {
            "free": {"generation": False},
            "base": {
                "generation": True,
                "poster_generation": True,
                "quality": ["standard"],
                "provider_selection": "automatic",
                "background": ["opaque", "auto"],
                "basic_editing": True,
                "basic_visual_fx": True,
            },
            "pro": {
                "generation": True,
                "poster_generation": True,
                "quality": ["standard", "high", "professional"],
                "provider_selection": "automatic_or_manual",
                "transparent_background": True,
                "advanced_image_edit": True,
                "layer_compositor": True,
                "brand_kit_compositor": True,
                "visual_fx_studio": True,
            },
        },
    }
