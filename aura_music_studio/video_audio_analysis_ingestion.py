from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .creative_project import CreativeProjectStore
from .video_music_sync import SyncMarker, _read as read_sync_map, _validate_markers, _write as write_sync_map
from .video_scene_render import _member_identity
from .video_scene_timeline import _project_dir
from .video_sync import audio_beats

router = APIRouter(prefix="/creative", tags=["video-audio-analysis"])

_ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
_MAX_ANALYSIS_BYTES = 512 * 1024 * 1024
_MAX_ANALYSIS_MARKERS = 9000


class AnalyzeProjectAudioRequest(BaseModel):
    element_id: str = Field(min_length=1, max_length=96)
    replace_existing_analysis: bool = True


def _resolve_project_audio(project_name: str, element_id: str) -> tuple[Path, object]:
    project_dir = _project_dir(project_name).resolve()
    manifest = CreativeProjectStore(project_dir).load()
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None:
        raise HTTPException(404, "Creative audio element not found")
    if element.kind not in {"audio", "music"}:
        raise HTTPException(400, "Synchronization analysis requires a music or audio element")
    if element.status != "ready":
        raise HTTPException(409, "Creative audio element must be ready before synchronization analysis")
    source_ref = str(element.source_ref or "").strip()
    if not source_ref or "://" in source_ref or source_ref.startswith(("/", "\\")):
        raise HTTPException(400, "Creative audio element does not reference project-owned media")
    source = (project_dir / source_ref).resolve()
    if project_dir not in source.parents:
        raise HTTPException(400, "Creative audio source is outside the member project")
    if not source.is_file():
        raise HTTPException(404, "Creative audio source file is unavailable")
    if source.suffix.lower() not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(415, "Creative audio source format is not supported for timing analysis")
    if source.stat().st_size > _MAX_ANALYSIS_BYTES:
        raise HTTPException(413, "Creative audio source is too large for synchronization analysis")
    return source, element


def _analysis_markers(element_id: str, analysis: dict) -> list[dict]:
    beat_times = [float(value) for value in list(analysis.get("beat_times") or [])]
    onset_times = [float(value) for value in list(analysis.get("onset_times") or [])]
    if len(beat_times) + len(onset_times) > _MAX_ANALYSIS_MARKERS:
        raise HTTPException(413, "Audio analysis produced too many synchronization markers")
    markers: list[dict] = []
    for index, timestamp in enumerate(beat_times):
        marker = SyncMarker(
            id=f"analysis.{element_id}.beat.{index:05d}",
            kind="beat",
            time_seconds=timestamp,
            source_element_id=element_id,
        )
        markers.append(marker.model_dump(mode="json"))
    for index, timestamp in enumerate(onset_times):
        marker = SyncMarker(
            id=f"analysis.{element_id}.onset.{index:05d}",
            kind="onset",
            time_seconds=timestamp,
            source_element_id=element_id,
        )
        markers.append(marker.model_dump(mode="json"))
    markers.sort(key=lambda item: (float(item["time_seconds"]), str(item["id"])))
    return markers


def _merge_analysis_markers(existing: list[dict], generated: list[dict], *, element_id: str, replace_existing: bool) -> list[dict]:
    if replace_existing:
        existing = [
            item
            for item in existing
            if not (
                item.get("source_element_id") == element_id
                and item.get("kind") in {"beat", "onset"}
                and str(item.get("id") or "").startswith(f"analysis.{element_id}.")
            )
        ]
    combined = [*existing, *generated]
    _validate_markers(combined)
    combined.sort(key=lambda item: (float(item["time_seconds"]), str(item["id"])))
    return combined


@router.post("/projects/{project_name}/video-music-sync/analyze-audio")
def analyze_project_audio(project_name: str, body: AnalyzeProjectAudioRequest, request: Request):
    _member_identity(request)
    source, element = _resolve_project_audio(project_name, body.element_id)
    try:
        analysis = audio_beats(source)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, "Project audio timing analysis failed") from exc
    generated = _analysis_markers(body.element_id, analysis)
    current = read_sync_map(project_name)
    merged = _merge_analysis_markers(
        list(current["markers"]),
        generated,
        element_id=body.element_id,
        replace_existing=body.replace_existing_analysis,
    )
    saved = write_sync_map(project_name, {"markers": merged})
    return {
        "project_name": project_name,
        "source_element_id": body.element_id,
        "source_kind": element.kind,
        "tempo_bpm": float(analysis.get("tempo_bpm") or 0.0),
        "generated_marker_count": len(generated),
        "beat_count": sum(1 for item in generated if item["kind"] == "beat"),
        "onset_count": sum(1 for item in generated if item["kind"] == "onset"),
        "marker_count": len(saved["markers"]),
        "raw_filesystem_paths_exposed": False,
        "arbitrary_client_paths_accepted": False,
        "timeline_mutated": False,
        "frame_accurate_renderer_sync_guaranteed": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = [
    "AnalyzeProjectAudioRequest",
    "_analysis_markers",
    "_merge_analysis_markers",
    "_resolve_project_audio",
    "analyze_project_audio",
    "router",
]
