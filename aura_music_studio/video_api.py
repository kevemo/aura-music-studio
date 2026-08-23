from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .plans import (
    ADVANCED_VIDEO_GENERATION,
    VIDEO_EXTENDED_DURATION,
    VIDEO_GENERATION,
    VIDEO_HIGH_QUALITY,
    VIDEO_PROVIDER_CONTROL,
    VIDEO_TO_VIDEO,
)
from .video_generation import VideoGenerationError, VideoGenerationRequest, VideoGenerationService
from .video_jobs import VideoJobStore

router = APIRouter(prefix="/api/video", tags=["video"])
service = VideoGenerationService()
job_store = VideoJobStore(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")


class GenerateVideoBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=32000)
    mode: str = "text_to_video"
    aspect_ratio: str = "9:16"
    duration_seconds: int = Field(default=8, ge=1, le=60)
    provider: str = "auto"
    quality: str = "standard"
    reference_url: str | None = None
    negative_prompt: str | None = None
    project_id: str | None = None
    target_platform: str | None = None


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(VIDEO_GENERATION):
        raise HTTPException(403, "Video Studio unlocks on Base ($4.99/month)")
    return member


def _enforce_tier(member, body: GenerateVideoBody) -> None:
    """Keep core video creation on Base while reserving professional controls for Pro."""
    plan = member.plan
    if body.mode == "video_to_video" and not plan.has(VIDEO_TO_VIDEO):
        raise HTTPException(403, "Video-to-video transformation is a Pro feature")
    if body.duration_seconds > 12 and not plan.has(VIDEO_EXTENDED_DURATION):
        raise HTTPException(403, "Video generations longer than 12 seconds require Pro")
    if body.quality.strip().lower() != "standard" and not plan.has(VIDEO_HIGH_QUALITY):
        raise HTTPException(403, "High-quality video rendering is a Pro feature")
    if body.provider.strip().lower() != "auto" and not plan.has(VIDEO_PROVIDER_CONTROL):
        raise HTTPException(403, "Manual video-engine selection is a Pro feature")
    if body.negative_prompt and body.negative_prompt.strip() and not plan.has(ADVANCED_VIDEO_GENERATION):
        raise HTTPException(403, "Advanced video generation controls require Pro")


@router.post("/generate")
def generate_video(body: GenerateVideoBody, request: Request):
    member = _member(request)
    _enforce_tier(member, body)
    try:
        result = service.generate(VideoGenerationRequest(**body.model_dump()))
    except VideoGenerationError as exc:
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
def list_video_jobs(request: Request, limit: int = 50):
    member = _member(request)
    return {"jobs": job_store.list_for_user(member.user_id, limit=limit)}


@router.post("/jobs/{job_id}/refresh")
def refresh_video_job(job_id: str, request: Request):
    member = _member(request)
    job = job_store.get_for_user(member.user_id, job_id)
    if not job:
        raise HTTPException(404, "Video job not found")
    if job["status"] == "completed" and job.get("output_path"):
        return job
    try:
        refreshed = service.refresh(
            result_id=job["id"],
            provider=job["provider"],
            provider_job_id=job.get("provider_job_id"),
        )
    except VideoGenerationError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    try:
        return job_store.update_status(
            user_id=member.user_id,
            job_id=job_id,
            status=refreshed.get("status") or job["status"],
            output_url=refreshed.get("output_url"),
            output_path=refreshed.get("output_path"),
            error=refreshed.get("error"),
        )
    except KeyError as exc:
        raise HTTPException(404, "Video job not found") from exc


@router.get("/jobs/{job_id}/download")
def download_video_job(job_id: str, request: Request):
    member = _member(request)
    job = job_store.get_for_user(member.user_id, job_id)
    if not job:
        raise HTTPException(404, "Video job not found")
    if job["status"] != "completed" or not job.get("output_path"):
        raise HTTPException(409, "Video is not ready for download")
    root = service.output_root.resolve()
    output = Path(job["output_path"]).resolve()
    if not output.is_relative_to(root) or not output.is_file():
        raise HTTPException(404, "Video output is unavailable")
    return FileResponse(output, media_type="video/mp4", filename=f"live-sound-studio-{job_id}.mp4")


@router.get("/capabilities")
def video_capabilities():
    return {
        "minimum_plan": "base",
        "minimum_plan_price_usd": "4.99",
        "modes": ["text_to_video", "image_to_video", "video_to_video"],
        "aspect_ratios": ["9:16", "16:9", "1:1"],
        "providers": ["auto", "local", "openai", "runway"],
        "workflows": [
            "music_video",
            "lyric_video",
            "audio_visualizer",
            "cover_art_animation",
            "short_form_social",
            "landscape_release_video",
        ],
        "tiers": {
            "free": {
                "generation": False,
                "message": "Video Studio is visible but generation unlocks on Base.",
            },
            "base": {
                "generation": True,
                "text_to_video": True,
                "image_to_video": True,
                "max_generation_seconds": 12,
                "quality": ["standard"],
                "provider_selection": "automatic",
            },
            "pro": {
                "generation": True,
                "text_to_video": True,
                "image_to_video": True,
                "video_to_video": True,
                "max_generation_seconds": 60,
                "max_generation_seconds_note": "Renderer-dependent; unsupported requests fail rather than being silently shortened.",
                "quality": ["standard", "high", "professional"],
                "provider_selection": "automatic_or_manual",
                "advanced_generation_controls": True,
                "aura_video_director": True,
                "advanced_exports": True,
                "priority_processing": True,
            },
        },
    }
