from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .video_scene_render import _member_identity
from .video_scene_timeline import _project_dir, _read as read_timeline, _validate_timeline, _write as write_timeline

router = APIRouter(prefix="/creative", tags=["video-music-sync"])

MarkerKind = Literal["beat", "onset", "lyric", "section"]
BoundaryKind = Literal["start", "end"]
_MAX_MARKERS = 10000
_MAX_SECONDS = 4 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(project_name: str):
    folder = _project_dir(project_name) / ".pulsar"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "video_music_sync.json"


def _empty(project_name: str) -> dict:
    return {"schema_version": 1, "project_name": project_name, "updated_at": _now(), "markers": []}


def _read(project_name: str) -> dict:
    path = _path(project_name)
    if not path.exists():
        return _empty(project_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Video/music synchronization map is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("markers"), list):
        raise HTTPException(500, "Video/music synchronization map has an unsupported format")
    return data


def _write(project_name: str, data: dict) -> dict:
    path = _path(project_name)
    payload = dict(data)
    payload.update({"schema_version": 1, "project_name": project_name, "updated_at": _now()})
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(prefix=".video-music-sync-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return payload


class SyncMarker(BaseModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    kind: MarkerKind
    time_seconds: float = Field(ge=0, le=_MAX_SECONDS)
    end_seconds: float | None = Field(default=None, ge=0, le=_MAX_SECONDS)
    text: str = Field(default="", max_length=1000)
    section_label: str = Field(default="", max_length=200)
    source_element_id: str | None = Field(default=None, max_length=96)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_seconds is not None and self.end_seconds < self.time_seconds:
            raise ValueError("end_seconds cannot precede time_seconds")
        if self.kind == "lyric" and not self.text.strip():
            raise ValueError("lyric markers require text")
        if self.kind == "section" and not self.section_label.strip():
            raise ValueError("section markers require section_label")
        return self


class ReplaceSyncMapRequest(BaseModel):
    markers: list[SyncMarker] = Field(default_factory=list, max_length=_MAX_MARKERS)


class SnapPlanRequest(BaseModel):
    marker_kinds: list[MarkerKind] = Field(default_factory=lambda: ["beat", "onset", "lyric", "section"])
    max_distance_seconds: float = Field(default=0.35, gt=0, le=10)


class SceneBoundarySnap(BaseModel):
    scene_id: str = Field(min_length=1, max_length=96)
    boundary: BoundaryKind
    marker_id: str = Field(min_length=1, max_length=96)


class ApplySnapRequest(BaseModel):
    snaps: list[SceneBoundarySnap] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_boundaries(self):
        keys = [(item.scene_id, item.boundary) for item in self.snaps]
        if len(keys) != len(set(keys)):
            raise ValueError("Each scene boundary can be snapped at most once per request")
        return self


def _public(data: dict) -> dict:
    return {
        "schema_version": data["schema_version"],
        "project_name": data["project_name"],
        "updated_at": data["updated_at"],
        "markers": data["markers"],
        "marker_count": len(data["markers"]),
        "raw_filesystem_paths_exposed": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


def _validate_markers(markers: list[dict]) -> None:
    if len(markers) > _MAX_MARKERS:
        raise HTTPException(400, f"Synchronization maps can contain at most {_MAX_MARKERS} markers")
    ids = [str(item["id"]) for item in markers]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Synchronization marker IDs must be unique")


@router.get("/projects/{project_name}/video-music-sync")
def get_video_music_sync(project_name: str, request: Request):
    _member_identity(request)
    return _public(_read(project_name))


@router.put("/projects/{project_name}/video-music-sync")
def replace_video_music_sync(project_name: str, body: ReplaceSyncMapRequest, request: Request):
    _member_identity(request)
    markers = [item.model_dump(mode="json") for item in body.markers]
    _validate_markers(markers)
    markers.sort(key=lambda item: (float(item["time_seconds"]), str(item["id"])))
    return _public(_write(project_name, {"markers": markers}))


@router.post("/projects/{project_name}/video-music-sync/snap-plan")
def plan_scene_boundary_snaps(project_name: str, body: SnapPlanRequest, request: Request):
    _member_identity(request)
    sync = _read(project_name)
    timeline = read_timeline(project_name)
    allowed = set(body.marker_kinds)
    markers = [item for item in sync["markers"] if item.get("kind") in allowed]
    suggestions: list[dict] = []
    for scene in timeline["scenes"]:
        for boundary, key in (("start", "start_seconds"), ("end", "end_seconds")):
            current = float(scene[key])
            candidates = [(abs(float(marker["time_seconds"]) - current), marker) for marker in markers]
            if not candidates:
                continue
            distance, marker = min(candidates, key=lambda item: (item[0], float(item[1]["time_seconds"]), str(item[1]["id"])))
            if distance <= body.max_distance_seconds:
                suggestions.append({
                    "scene_id": scene["id"],
                    "boundary": boundary,
                    "current_seconds": current,
                    "suggested_seconds": float(marker["time_seconds"]),
                    "offset_seconds": float(marker["time_seconds"]) - current,
                    "marker_id": marker["id"],
                    "marker_kind": marker["kind"],
                    "marker_text": marker.get("text", ""),
                    "section_label": marker.get("section_label", ""),
                })
    return {
        "project_name": project_name,
        "suggestions": suggestions,
        "suggestion_count": len(suggestions),
        "timeline_mutated": False,
        "frame_accurate_renderer_sync_guaranteed": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.post("/projects/{project_name}/video-music-sync/apply-snaps")
def apply_scene_boundary_snaps(project_name: str, body: ApplySnapRequest, request: Request):
    _member_identity(request)
    sync = _read(project_name)
    timeline = read_timeline(project_name)
    markers = {str(item["id"]): item for item in sync["markers"]}
    candidate = deepcopy(timeline)
    candidate_scenes = {str(item["id"]): item for item in candidate["scenes"]}
    applied: list[dict] = []
    for snap in body.snaps:
        scene = candidate_scenes.get(snap.scene_id)
        if scene is None:
            raise HTTPException(404, f"Video scene not found: {snap.scene_id}")
        marker = markers.get(snap.marker_id)
        if marker is None:
            raise HTTPException(404, f"Synchronization marker not found: {snap.marker_id}")
        key = "start_seconds" if snap.boundary == "start" else "end_seconds"
        previous = float(scene[key])
        scene[key] = float(marker["time_seconds"])
        scene["updated_at"] = _now()
        applied.append({"scene_id": snap.scene_id, "boundary": snap.boundary, "marker_id": snap.marker_id, "from_seconds": previous, "to_seconds": scene[key]})
    _validate_timeline(candidate["scenes"])
    saved = write_timeline(project_name, candidate)
    return {
        "project_name": project_name,
        "applied": applied,
        "timeline": saved,
        "frame_accurate_renderer_sync_guaranteed": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = ["router", "SyncMarker", "replace_video_music_sync", "plan_scene_boundary_snaps", "apply_scene_boundary_snaps"]
