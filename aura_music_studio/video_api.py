from __future__ import annotations

import mimetypes
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .plans import (
    AUDIO_REACTIVE_VIDEO,
    BASIC_VIDEO_STUDIO,
    LYRIC_VIDEO,
    NEURAL_VIDEO,
    VIDEO_4K_EXPORT,
    VIDEO_EXPORT,
    VIDEO_STORYBOARD,
)
from .tenant_storage import project_path
from .video_engines import public_video_engine_status, render_neural_scene
from .video_studio import (
    MusicVideoRequest,
    build_storyboard,
    render_lyric_video,
    render_montage,
    render_visualizer,
    validate_music_video,
    video_dimensions,
)

router = APIRouter(prefix="/video", tags=["ESP Music Video Studio"])


class StoryboardRequest(BaseModel):
    audio_asset_id: str
    creative_direction: str = Field(default="cinematic music video", max_length=4000)
    scene_beats: int = Field(default=16, ge=4, le=64)


class NeuralSceneRequest(BaseModel):
    engine: str = "wan22"
    prompt: str = Field(min_length=3, max_length=8000)
    negative_prompt: str = Field(default="", max_length=4000)
    image_asset_id: str | None = None
    audio_asset_id: str | None = None
    duration_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    aspect: str = "16:9"
    fps: int = Field(default=24, ge=16, le=60)
    seed: int = -1


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _require(member, feature: str) -> None:
    if not member.plan.has(feature):
        raise HTTPException(403, f"{feature} requires a higher membership tier")


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "video"


def _asset(project: Path, asset_id: str):
    library = AssetLibrary(project)
    try:
        record = library.get(asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Asset not found") from exc
    path = (project / record.path).resolve()
    if project.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Asset file unavailable")
    return record, path


def _audio(project: Path, asset_id: str):
    record, path = _asset(project, asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "Choose an audio asset")
    return record, path


def _visual(project: Path, asset_id: str):
    record, path = _asset(project, asset_id)
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    if record.kind != "video" and path.suffix.lower() not in image_suffixes:
        raise HTTPException(400, "Visual assets must be an uploaded image or video")
    return record, path


def _video_output(project: Path, relative_path: str) -> Path:
    video_root = (project / "output" / "video").resolve()
    target = (video_root / relative_path).resolve()
    if video_root not in target.parents or not target.is_file() or target.suffix.lower() not in {".mp4", ".mov", ".webm", ".m4v"}:
        raise HTTPException(404, "Video output not found")
    return target


def _stream_url(project_name: str, output_relative: str) -> str:
    video_relative = Path(output_relative).relative_to("video").as_posix()
    return f"/video/projects/{quote(project_name, safe='')}/stream/{quote(video_relative, safe='/')}"


@router.get("/capabilities")
def capabilities(request: Request):
    member = _member(request)
    return {
        "plan": member.plan.id,
        "local_renderer": True,
        "neural_engines": public_video_engine_status(),
        "features": {
            "preview": member.plan.has(BASIC_VIDEO_STUDIO),
            "full_export": member.plan.has(VIDEO_EXPORT),
            "audio_reactive": member.plan.has(AUDIO_REACTIVE_VIDEO),
            "lyric_video": member.plan.has(LYRIC_VIDEO),
            "storyboard": member.plan.has(VIDEO_STORYBOARD),
            "neural_video": member.plan.has(NEURAL_VIDEO),
            "4k": member.plan.has(VIDEO_4K_EXPORT),
        },
        "aspects": ["16:9", "9:16", "1:1", "4:5"],
        "local_modes": ["visualizer", "lyric_video", "montage"],
    }


@router.post("/projects/{project_name}/storyboard")
def storyboard(project_name: str, body: StoryboardRequest, request: Request):
    member = _member(request)
    _require(member, VIDEO_STORYBOARD)
    project = _project(project_name)
    _, audio = _audio(project, body.audio_asset_id)
    return build_storyboard(audio, direction=body.creative_direction, scene_beats=body.scene_beats)


@router.post("/projects/{project_name}/render")
def render_local(project_name: str, body: MusicVideoRequest, request: Request):
    member = _member(request)
    _require(member, BASIC_VIDEO_STUDIO)
    project = _project(project_name)
    audio_record, audio = _audio(project, body.audio_asset_id)
    visuals = [_visual(project, item)[1] for item in body.visual_asset_ids]

    preview_only = not member.plan.has(VIDEO_EXPORT)
    if preview_only:
        if body.mode != "visualizer":
            raise HTTPException(403, "Free Video Studio supports the visualizer preview; full video modes unlock on Base")
        duration_limit = min(float(body.preview_seconds or 15.0), 15.0)
        quality = "preview"
    else:
        duration_limit = body.preview_seconds
        quality = body.quality

    if body.mode == "lyric_video":
        _require(member, LYRIC_VIDEO)
    if body.mode in {"visualizer", "montage"}:
        _require(member, AUDIO_REACTIVE_VIDEO if not preview_only else BASIC_VIDEO_STUDIO)
    if quality == "4k":
        _require(member, VIDEO_4K_EXPORT)

    out_dir = project / "output" / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(Path(audio_record.name).stem)
    target = out_dir / f"{stem}_{body.mode}_{body.aspect.replace(':', 'x')}_{quality}.mp4"
    first_visual = visuals[0] if visuals else None

    try:
        if body.mode == "lyric_video":
            rendered, report = render_lyric_video(
                audio, target, lyrics=body.lyrics, visual=first_visual, aspect=body.aspect,
                fps=body.fps, quality=quality, duration_limit=duration_limit,
                waveform=body.include_waveform,
            )
        elif body.mode == "montage":
            rendered, report = render_montage(
                audio, visuals, target, aspect=body.aspect, fps=body.fps, quality=quality,
                duration_limit=duration_limit, waveform=body.include_waveform,
            )
        else:
            rendered, report = render_visualizer(
                audio, target, visual=first_visual, aspect=body.aspect, fps=body.fps,
                quality=quality, duration_limit=duration_limit, waveform=body.include_waveform,
            )
    except (ValueError, FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(400, str(exc)) from exc

    relative = rendered.relative_to(project / "output").as_posix()
    return {
        "output": relative,
        "stream_url": _stream_url(project_name, relative),
        "report": report,
        "preview_only": preview_only,
        "download_unlocked": member.plan.has(VIDEO_EXPORT),
        "engine": "ESP local FFmpeg/librosa video renderer",
    }


@router.post("/projects/{project_name}/neural-scene")
def neural_scene(project_name: str, body: NeuralSceneRequest, request: Request):
    member = _member(request)
    _require(member, NEURAL_VIDEO)
    project = _project(project_name)
    if body.aspect not in {"16:9", "9:16", "1:1", "4:5"}:
        raise HTTPException(400, "Unsupported aspect ratio")
    width, height = video_dimensions(body.aspect, "hd")
    image = _visual(project, body.image_asset_id)[1] if body.image_asset_id else None
    audio = _audio(project, body.audio_asset_id)[1] if body.audio_asset_id else None
    target = project / "output" / "video" / "neural" / f"{_slug(body.engine)}_{_slug(body.prompt[:40])}.mp4"
    try:
        rendered, report = render_neural_scene(
            body.engine,
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            image=image,
            audio=audio,
            output=target,
            duration_seconds=body.duration_seconds,
            width=width,
            height=height,
            fps=body.fps,
            seed=body.seed,
        )
        if audio:
            validate_music_video(rendered, minimum_seconds=1.0)
    except (ValueError, FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(400, str(exc)) from exc
    relative = rendered.relative_to(project / "output").as_posix()
    return {"output": relative, "stream_url": _stream_url(project_name, relative), "report": report}


@router.get("/projects/{project_name}/stream/{relative_path:path}")
def stream_video(project_name: str, relative_path: str, request: Request):
    _require(_member(request), BASIC_VIDEO_STUDIO)
    project = _project(project_name)
    target = _video_output(project, relative_path)
    return FileResponse(
        target,
        media_type=mimetypes.guess_type(target.name)[0] or "video/mp4",
        headers={"Content-Disposition": "inline"},
    )
