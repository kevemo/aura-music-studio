from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .music_video_orchestrator import AuraMusicVideoDirector, MusicVideoError
from .plans import VIDEO_DIRECTOR, VIDEO_HIGH_QUALITY, VIDEO_PROVIDER_CONTROL
from .tenant_storage import project_path

router = APIRouter(prefix="/api/video/music-video", tags=["Aura Music Video Director"])
director = AuraMusicVideoDirector(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")


class CreateMusicVideoBody(BaseModel):
    project_name: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=240)
    concept: str = Field(min_length=3, max_length=12000)
    aspect_ratio: str = Field(default="16:9", pattern="^(16:9|9:16|1:1)$")
    provider: str = Field(default="auto", pattern="^(auto|local|openai|runway)$")
    quality: str = Field(default="standard", pattern="^(standard|high|professional)$")
    continuity: str = Field(
        default="consistent principal subject, wardrobe, locations, lighting, color palette and cinematic visual language",
        max_length=4000,
    )


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(VIDEO_DIRECTOR):
        raise HTTPException(403, "Aura Music Video Director is an Ultimate Pro feature")
    return member


@router.post("")
def create_music_video(body: CreateMusicVideoBody, request: Request):
    member = _member(request)
    if body.provider != "auto" and not member.plan.has(VIDEO_PROVIDER_CONTROL):
        raise HTTPException(403, "Manual video provider selection requires Pro")
    if body.quality != "standard" and not member.plan.has(VIDEO_HIGH_QUALITY):
        raise HTTPException(403, "High-quality video rendering requires Pro")
    try:
        source = project_path(body.project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Source music project not found") from exc
    try:
        return director.start(
            user_id=member.user_id,
            source_project=source,
            title=body.title,
            concept=body.concept,
            aspect_ratio=body.aspect_ratio,
            provider=body.provider,
            quality=body.quality,
            continuity=body.continuity,
        )
    except MusicVideoError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Music-video generation unavailable: {type(exc).__name__}: {exc}") from exc


@router.get("")
def list_music_videos(request: Request, limit: int = 30):
    member = _member(request)
    return {"music_videos": director.store.list_projects(member.user_id, limit=limit)}


@router.get("/{music_video_id}")
def get_music_video(music_video_id: str, request: Request):
    member = _member(request)
    try:
        return director.store.get_project(member.user_id, music_video_id)
    except KeyError as exc:
        raise HTTPException(404, "Music-video project not found") from exc


@router.post("/{music_video_id}/refresh")
def refresh_music_video(music_video_id: str, request: Request):
    member = _member(request)
    try:
        return director.refresh(user_id=member.user_id, project_id=music_video_id)
    except MusicVideoError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{music_video_id}/download")
def download_music_video(music_video_id: str, request: Request):
    member = _member(request)
    try:
        project = director.store.get_project(member.user_id, music_video_id)
    except KeyError as exc:
        raise HTTPException(404, "Music-video project not found") from exc
    if project["status"] != "completed" or not project.get("output_path"):
        raise HTTPException(409, "Music video is not ready")
    root = director.output_root.resolve()
    output = Path(project["output_path"]).resolve()
    if not output.is_relative_to(root) or not output.is_file():
        raise HTTPException(404, "Music-video output is unavailable")
    return FileResponse(output, media_type="video/mp4", filename=f"Aura_Music_Video_{music_video_id}.mp4")
