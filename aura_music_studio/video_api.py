from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .video_generation import VideoGenerationError, VideoGenerationRequest, VideoGenerationService

router = APIRouter(prefix="/api/video", tags=["video"])
service = VideoGenerationService()


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


@router.post("/generate")
def generate_video(body: GenerateVideoBody):
    try:
        result = service.generate(VideoGenerationRequest(**body.model_dump()))
    except VideoGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result.to_dict(), "provenance_hash": service.provenance_hash(result)}


@router.get("/capabilities")
def video_capabilities():
    return {
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
    }
