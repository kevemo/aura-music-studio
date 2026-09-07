from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .assets import AssetLibrary
from .image_effect_api import router as image_effect_router
from .restoration import AudioRestorer
from .spatial import SpatialRenderer
from .speech import AuraSpeechService
from .tenant_storage import project_path
from .tone import NeuralToneProcessor
from .video_sync import build_sync_map

router = APIRouter()


class RestoreRequest(BaseModel):
    asset_id: str
    hum_hz: float | None = None
    highpass_hz: float = 35.0
    neural: bool = True


class NeuralAmpRequest(BaseModel):
    audio_asset_id: str
    model_asset_id: str
    input_gain_db: float = 0.0
    output_gain_db: float = 0.0


class SpatialRequest(BaseModel):
    asset_id: str
    mode: str = "stereo"
    pan: float = 0.0
    width: float = 1.0
    azimuth_deg: float = 0.0
    elevation_deg: float = 0.0
    distance_m: float = 1.0


class VideoSyncRequest(BaseModel):
    video_asset_id: str
    audio_asset_id: str
    scene_threshold: float = 0.35
    snap_window_seconds: float = 0.35


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


@router.get("/speech/diagnostics")
def speech_diagnostics():
    return AuraSpeechService().diagnostics()


@router.post("/speech/command")
async def speech_command(
    audio: UploadFile = File(...),
    project_name: str | None = Form(default=None),
    speak_reply: bool = Form(default=True),
):
    suffix = Path(audio.filename or "command.wav").suffix or ".wav"
    with tempfile.TemporaryDirectory(prefix="aura-speech-api-") as tmp:
        source = Path(tmp) / f"command{suffix}"
        with source.open("wb") as handle:
            while chunk := await audio.read(1024 * 1024):
                handle.write(chunk)
        speech_out = Path(tmp) / "reply.wav" if speak_reply else None
        summary = {"project": project_name} if project_name else None
        try:
            result = AuraSpeechService().command(source, session_summary=summary, speech_output=speech_out)
        except Exception as exc:
            raise HTTPException(500, f"Speech processing failed: {type(exc).__name__}: {exc}") from exc
        return {
            "transcript": result.transcript,
            "plan": result.plan.model_dump(),
            "spoken_text": result.spoken_text,
            "speech_generated": bool(result.speech_file),
        }


@router.post("/projects/{project_name}/restore")
def restore(project_name: str, request: RestoreRequest):
    project = _project(project_name)
    library = AssetLibrary(project)
    try:
        asset = library.get(request.asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Audio asset not found") from exc
    if asset.kind != "audio":
        raise HTTPException(400, "Restoration requires an audio asset")
    output = project / "output" / "restoration" / f"{Path(asset.name).stem}_clean.wav"
    try:
        path, report = AudioRestorer().clean(
            project / asset.path,
            output,
            hum_hz=request.hum_hz,
            highpass_hz=request.highpass_hz,
            neural=request.neural,
        )
    except Exception as exc:
        raise HTTPException(500, f"Restoration failed: {type(exc).__name__}: {exc}") from exc
    return {"path": str(path), "report": report, "audio_origin": "real_audio_restoration"}


@router.post("/projects/{project_name}/neural-amp")
def neural_amp(project_name: str, request: NeuralAmpRequest):
    project = _project(project_name)
    library = AssetLibrary(project)
    try:
        audio = library.get(request.audio_asset_id)
        model = library.get(request.model_asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Audio or model asset not found") from exc
    if audio.kind != "audio" or model.kind != "model":
        raise HTTPException(400, "Neural amp requires one audio asset and one .nam/.onnx model asset")
    output = project / "output" / "tone" / f"{Path(audio.name).stem}_NAM.wav"
    try:
        path, report = NeuralToneProcessor().process(
            project / audio.path,
            project / model.path,
            output,
            input_gain_db=request.input_gain_db,
            output_gain_db=request.output_gain_db,
        )
    except Exception as exc:
        raise HTTPException(500, f"Neural amp processing failed: {type(exc).__name__}: {exc}") from exc
    return {"path": str(path), "report": report}


@router.post("/projects/{project_name}/spatial")
def spatial(project_name: str, request: SpatialRequest):
    project = _project(project_name)
    library = AssetLibrary(project)
    try:
        asset = library.get(request.asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Audio asset not found") from exc
    if asset.kind != "audio":
        raise HTTPException(400, "Spatial processing requires an audio asset")
    output = project / "output" / "spatial" / f"{Path(asset.name).stem}_{request.mode}.wav"
    renderer = SpatialRenderer()
    try:
        if request.mode == "stereo":
            path, report = renderer.stereo_position(project / asset.path, output, pan=request.pan, width=request.width)
        else:
            path, report = renderer.immersive(
                project / asset.path,
                output,
                mode=request.mode,
                azimuth_deg=request.azimuth_deg,
                elevation_deg=request.elevation_deg,
                distance_m=request.distance_m,
            )
    except Exception as exc:
        raise HTTPException(500, f"Spatial processing failed: {type(exc).__name__}: {exc}") from exc
    return {"path": str(path), "report": report}


@router.post("/projects/{project_name}/video-sync")
def video_sync(project_name: str, request: VideoSyncRequest):
    project = _project(project_name)
    library = AssetLibrary(project)
    try:
        video = library.get(request.video_asset_id)
        audio = library.get(request.audio_asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Video or audio asset not found") from exc
    if video.kind != "video" or audio.kind != "audio":
        raise HTTPException(400, "Video sync requires one video asset and one audio asset")
    output = project / "work" / "video_sync" / "sync_map.json"
    try:
        result = build_sync_map(
            project / video.path,
            project / audio.path,
            output,
            scene_threshold=request.scene_threshold,
            snap_window_seconds=request.snap_window_seconds,
        )
    except Exception as exc:
        raise HTTPException(500, f"Video sync analysis failed: {type(exc).__name__}: {exc}") from exc
    return result


# The engineering router is already mounted once by the canonical API. Nest the bounded image
# effect router here so its executable image tools inherit the same member/security middleware
# without creating a second application-level dispatch authority.
router.include_router(image_effect_router)
