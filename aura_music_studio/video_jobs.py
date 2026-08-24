from __future__ import annotations

import re
from pathlib import Path

from .assets import AssetLibrary
from .video_engines import render_neural_scene
from .video_studio import (
    MusicVideoRequest,
    render_lyric_video,
    render_montage,
    render_visualizer,
    video_dimensions,
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "video"


def _asset(project: Path, asset_id: str):
    library = AssetLibrary(project)
    record = library.get(asset_id)
    path = (project / record.path).resolve()
    if project.resolve() not in path.parents or not path.is_file():
        raise FileNotFoundError(f"Project asset unavailable: {asset_id}")
    return record, path


def _audio(project: Path, asset_id: str):
    record, path = _asset(project, asset_id)
    if record.kind != "audio":
        raise ValueError("Video job audio asset must be audio")
    return record, path


def _visual(project: Path, asset_id: str):
    record, path = _asset(project, asset_id)
    if record.kind != "video" and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("Video job visual asset must be an image/video")
    return record, path


def run_local_video_job(project: str | Path, payload: dict) -> dict:
    """Execute a previously entitlement-validated local Music Video Studio job."""
    root = Path(project).resolve()
    request = MusicVideoRequest.model_validate(payload["request"])
    preview_only = bool(payload.get("preview_only", False))
    effective_quality = str(payload.get("effective_quality") or request.quality)
    duration_limit = payload.get("duration_limit")

    audio_record, audio = _audio(root, request.audio_asset_id)
    visuals = [_visual(root, item)[1] for item in request.visual_asset_ids]
    out_dir = root / "output" / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (
        f"{_slug(Path(audio_record.name).stem)}_{request.mode}_"
        f"{request.aspect.replace(':', 'x')}_{effective_quality}.mp4"
    )
    first_visual = visuals[0] if visuals else None

    if request.mode == "lyric_video":
        rendered, report = render_lyric_video(
            audio,
            target,
            lyrics=request.lyrics,
            visual=first_visual,
            aspect=request.aspect,
            fps=request.fps,
            quality=effective_quality,
            duration_limit=duration_limit,
            waveform=request.include_waveform,
        )
    elif request.mode == "montage":
        rendered, report = render_montage(
            audio,
            visuals,
            target,
            aspect=request.aspect,
            fps=request.fps,
            quality=effective_quality,
            duration_limit=duration_limit,
            waveform=request.include_waveform,
        )
    else:
        rendered, report = render_visualizer(
            audio,
            target,
            visual=first_visual,
            aspect=request.aspect,
            fps=request.fps,
            quality=effective_quality,
            duration_limit=duration_limit,
            waveform=request.include_waveform,
        )
    relative = rendered.relative_to(root / "output").as_posix()
    return {
        "output": relative,
        "report": report,
        "preview_only": preview_only,
        "download_unlocked": not preview_only,
        "engine": "ESP local FFmpeg/librosa video renderer",
    }


def run_neural_video_job(project: str | Path, payload: dict) -> dict:
    """Execute an owner-approved neural scene job on a video-capable ESP node."""
    root = Path(project).resolve()
    engine = str(payload.get("engine") or "wan22")
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Neural video job requires a prompt")
    aspect = str(payload.get("aspect") or "16:9")
    if aspect not in {"16:9", "9:16", "1:1", "4:5"}:
        raise ValueError("Unsupported aspect ratio")
    width, height = video_dimensions(aspect, "hd")
    image_id = payload.get("image_asset_id")
    audio_id = payload.get("audio_asset_id")
    image = _visual(root, str(image_id))[1] if image_id else None
    audio = _audio(root, str(audio_id))[1] if audio_id else None
    output = root / "output" / "video" / "neural" / f"{_slug(engine)}_{_slug(prompt[:48])}.mp4"
    rendered, report = render_neural_scene(
        engine,
        prompt=prompt,
        negative_prompt=str(payload.get("negative_prompt") or ""),
        image=image,
        audio=audio,
        output=output,
        duration_seconds=float(payload.get("duration_seconds") or 5.0),
        width=width,
        height=height,
        fps=int(payload.get("fps") or 24),
        seed=int(payload.get("seed", -1)),
    )
    return {
        "output": rendered.relative_to(root / "output").as_posix(),
        "report": report,
        "engine": engine,
        "neural": True,
    }


def run_video_job(project: str | Path, job_type: str, payload: dict) -> dict:
    if job_type == "video:local":
        return run_local_video_job(project, payload)
    if job_type == "video:neural":
        return run_neural_video_job(project, payload)
    raise ValueError(f"Unsupported video job type: {job_type}")
