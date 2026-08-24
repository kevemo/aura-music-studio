from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .jobs import StudioJobQueue
from .music_video_orchestrator import AuraMusicVideoDirector, MusicVideoError
from .plans import PRIORITY_QUEUE, VIDEO_DIRECTOR, VIDEO_HIGH_QUALITY, VIDEO_PROVIDER_CONTROL
from .tenant_storage import project_path

router = APIRouter(prefix="/api/video/music-video", tags=["Aura Music Video Director"])
db_path = os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
director = AuraMusicVideoDirector(db_path)
queue = StudioJobQueue(AccountStore(db_path))


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


def _public_job(job: dict) -> dict:
    result = {k: v for k, v in job.items() if k not in {"payload_json", "result_json"}}
    if job.get("result_json"):
        try:
            result["result"] = json.loads(job["result_json"])
        except Exception:
            result["result"] = None
    return result


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
    if not (source / "output" / "Aura_Final_Master.wav").is_file():
        raise HTTPException(409, "Finish the song master before starting Aura Music Video Director")

    priority = 100 if member.plan.has(PRIORITY_QUEUE) else 25
    job = queue.submit(
        member.user_id,
        body.project_name,
        job_type="music_video_start",
        priority=priority,
        payload={
            "title": body.title,
            "concept": body.concept,
            "aspect_ratio": body.aspect_ratio,
            "provider": body.provider,
            "quality": body.quality,
            "continuity": body.continuity,
        },
    )
    return {
        "queued": True,
        "submission_job": _public_job(job),
        "message": "Aura Music Video Director has been queued. The production worker will submit the storyboard shots.",
    }


@router.get("/submissions/{job_id}")
def music_video_submission(job_id: str, request: Request):
    member = _member(request)
    job = queue.get(job_id, user_id=member.user_id)
    if not job or job.get("job_type") != "music_video_start":
        raise HTTPException(404, "Music-video submission job not found")
    return _public_job(job)


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
