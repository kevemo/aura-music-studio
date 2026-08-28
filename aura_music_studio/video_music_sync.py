from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .video_scene_timeline import _project_dir, _read as _read_timeline, _write as _write_timeline

router = APIRouter(prefix="/creative", tags=["video-music-sync"])

_MAX_CUES = 5000
_MAX_SCENE_CUES = 200
_MAX_PROJECT_SECONDS = 4 * 60 * 60
CueKind = Literal["beat", "downbeat", "section", "lyric"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _sync_path(project_name: str) -> Path:
    directory = _project_dir(project_name) / ".pulsar"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "video_music_sync.json"


def _empty(project_name: str) -> dict:
    return {
        "schema_version": 1,
        "project_name": project_name,
        "updated_at": _now(),
        "cues": [],
    }


def _read(project_name: str) -> dict:
    path = _sync_path(project_name)
    if not path.exists():
        return _empty(project_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Video music synchronization data is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("cues"), list):
        raise HTTPException(500, "Video music synchronization data has an unsupported format")
    return data


def _write(project_name: str, data: dict) -> dict:
    path = _sync_path(project_name)
    data = dict(data)
    data["schema_version"] = 1
    data["project_name"] = project_name
    data["updated_at"] = _now()
    serialized = json.dumps(data, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(prefix=".video-sync-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return data


def _sort_cues(cues: list[dict]) -> list[dict]:
    rank = {"downbeat": 0, "beat": 1, "section": 2, "lyric": 3}
    return sorted(cues, key=lambda cue: (float(cue["at_seconds"]), rank.get(str(cue["kind"]), 99), str(cue["id"])))


def _validate_cues(cues: list[dict]) -> None:
    if len(cues) > _MAX_CUES:
        raise HTTPException(400, f"A project can contain at most {_MAX_CUES} synchronization cues")
    seen: set[str] = set()
    for cue in cues:
        cue_id = str(cue.get("id") or "")
        if cue_id in seen:
            raise HTTPException(400, "Duplicate synchronization cue ID")
        seen.add(cue_id)
        start = float(cue["at_seconds"])
        end = cue.get("end_seconds")
        if start < 0 or start > _MAX_PROJECT_SECONDS:
            raise HTTPException(400, "Synchronization cue timing is invalid")
        if end is not None and (float(end) <= start or float(end) > _MAX_PROJECT_SECONDS):
            raise HTTPException(400, "Synchronization cue end timing is invalid")
        if cue.get("kind") in {"beat", "downbeat"} and end is not None:
            raise HTTPException(400, "Beat cues cannot contain an end time")


def _public(data: dict) -> dict:
    cues = _sort_cues(list(data["cues"]))
    return {
        "schema_version": data["schema_version"],
        "project_name": data["project_name"],
        "updated_at": data["updated_at"],
        "cues": cues,
        "cue_count": len(cues),
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


class SyncCueCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    kind: CueKind
    at_seconds: float = Field(ge=0, le=_MAX_PROJECT_SECONDS)
    end_seconds: float | None = Field(default=None, gt=0, le=_MAX_PROJECT_SECONDS)
    label: str = Field(default="", max_length=240)
    text: str = Field(default="", max_length=2000)
    source_element_id: str | None = Field(default=None, max_length=96)
    beat_index: int | None = Field(default=None, ge=0, le=1_000_000)
    bar_index: int | None = Field(default=None, ge=0, le=1_000_000)
    bpm: float | None = Field(default=None, gt=0, le=500)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_timing(self):
        if self.end_seconds is not None and self.end_seconds <= self.at_seconds:
            raise ValueError("end_seconds must be greater than at_seconds")
        if self.kind in {"beat", "downbeat"} and self.end_seconds is not None:
            raise ValueError("beat cues cannot contain end_seconds")
        return self


class SyncCueUpdateRequest(BaseModel):
    kind: CueKind | None = None
    at_seconds: float | None = Field(default=None, ge=0, le=_MAX_PROJECT_SECONDS)
    end_seconds: float | None = Field(default=None, gt=0, le=_MAX_PROJECT_SECONDS)
    label: str | None = Field(default=None, max_length=240)
    text: str | None = Field(default=None, max_length=2000)
    source_element_id: str | None = Field(default=None, max_length=96)
    beat_index: int | None = Field(default=None, ge=0, le=1_000_000)
    bar_index: int | None = Field(default=None, ge=0, le=1_000_000)
    bpm: float | None = Field(default=None, gt=0, le=500)
    confidence: float | None = Field(default=None, ge=0, le=1)


class SceneSyncBindingsRequest(BaseModel):
    cue_ids: list[str] = Field(default_factory=list, max_length=_MAX_SCENE_CUES)

    @model_validator(mode="after")
    def unique_ids(self):
        if len(self.cue_ids) != len(set(self.cue_ids)):
            raise ValueError("cue_ids cannot contain duplicates")
        return self


def _scene(project_name: str, scene_id: str) -> tuple[dict, dict]:
    timeline = _read_timeline(project_name)
    scene = next((item for item in timeline["scenes"] if item["id"] == scene_id), None)
    if scene is None:
        raise HTTPException(404, "Video scene not found")
    return timeline, scene


def _cue_overlaps_scene(cue: dict, scene: dict) -> bool:
    cue_start = float(cue["at_seconds"])
    cue_end = float(cue.get("end_seconds") if cue.get("end_seconds") is not None else cue_start)
    scene_start = float(scene["start_seconds"])
    scene_end = float(scene["end_seconds"])
    if cue_start == cue_end:
        return scene_start <= cue_start <= scene_end
    return cue_start < scene_end and cue_end > scene_start


def resolve_scene_cues(project_name: str, scene: dict) -> list[dict]:
    cue_ids = list(scene.get("sync_cue_ids") or [])
    if not cue_ids:
        return []
    data = _read(project_name)
    by_id = {str(cue["id"]): cue for cue in data["cues"]}
    missing = [cue_id for cue_id in cue_ids if cue_id not in by_id]
    if missing:
        raise HTTPException(409, "Scene references synchronization cues that no longer exist")
    cues = [by_id[cue_id] for cue_id in cue_ids]
    if any(not _cue_overlaps_scene(cue, scene) for cue in cues):
        raise HTTPException(409, "Scene synchronization cue falls outside the scene timing window")
    return _sort_cues(cues)


def sync_prompt(cues: list[dict]) -> str:
    if not cues:
        return ""
    lines = ["Music synchronization anchors (preserve timing intent):"]
    for cue in _sort_cues(cues):
        marker = f"{float(cue['at_seconds']):.3f}s"
        end = cue.get("end_seconds")
        if end is not None:
            marker += f"-{float(end):.3f}s"
        detail = str(cue.get("text") or cue.get("label") or "").strip()
        suffix = f": {detail}" if detail else ""
        lines.append(f"- {marker} [{cue['kind']}]{suffix}")
    return "\n".join(lines)


def _scene_sync_public(project_name: str, scene: dict, cues: list[dict]) -> dict:
    starts = [float(cue["at_seconds"]) for cue in cues]
    ends = [float(cue.get("end_seconds") if cue.get("end_seconds") is not None else cue["at_seconds"]) for cue in cues]
    return {
        "project_name": project_name,
        "scene_id": scene["id"],
        "cue_ids": list(scene.get("sync_cue_ids") or []),
        "cues": cues,
        "sync_window": {"start_seconds": min(starts), "end_seconds": max(ends)} if cues else None,
        "scene_timing_unchanged": True,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.get("/projects/{project_name}/video-sync")
def get_video_music_sync(project_name: str, request: Request):
    _member(request)
    return _public(_read(project_name))


@router.post("/projects/{project_name}/video-sync/cues")
def add_video_music_sync_cue(project_name: str, body: SyncCueCreateRequest, request: Request):
    _member(request)
    data = _read(project_name)
    if any(cue["id"] == body.id for cue in data["cues"]):
        raise HTTPException(409, "Synchronization cue ID already exists")
    cue = body.model_dump(mode="json")
    cue["created_at"] = _now()
    cue["updated_at"] = cue["created_at"]
    data["cues"].append(cue)
    _validate_cues(data["cues"])
    return _public(_write(project_name, data))


@router.patch("/projects/{project_name}/video-sync/cues/{cue_id}")
def update_video_music_sync_cue(project_name: str, cue_id: str, body: SyncCueUpdateRequest, request: Request):
    _member(request)
    data = _read(project_name)
    cue = next((item for item in data["cues"] if item["id"] == cue_id), None)
    if cue is None:
        raise HTTPException(404, "Synchronization cue not found")
    updates = body.model_dump(exclude_unset=True, mode="json")
    candidate = dict(cue)
    candidate.update(updates)
    _validate_cues([candidate])
    cue.update(updates)
    cue["updated_at"] = _now()
    _validate_cues(data["cues"])
    return _public(_write(project_name, data))


@router.delete("/projects/{project_name}/video-sync/cues/{cue_id}")
def delete_video_music_sync_cue(project_name: str, cue_id: str, request: Request):
    _member(request)
    data = _read(project_name)
    if not any(cue["id"] == cue_id for cue in data["cues"]):
        raise HTTPException(404, "Synchronization cue not found")
    timeline = _read_timeline(project_name)
    if any(cue_id in (scene.get("sync_cue_ids") or []) for scene in timeline["scenes"]):
        raise HTTPException(409, "Synchronization cue is still bound to a video scene")
    data["cues"] = [cue for cue in data["cues"] if cue["id"] != cue_id]
    return _public(_write(project_name, data))


@router.put("/projects/{project_name}/video-sync/scenes/{scene_id}/bindings")
def bind_video_scene_sync_cues(project_name: str, scene_id: str, body: SceneSyncBindingsRequest, request: Request):
    _member(request)
    timeline, scene = _scene(project_name, scene_id)
    data = _read(project_name)
    by_id = {str(cue["id"]): cue for cue in data["cues"]}
    missing = [cue_id for cue_id in body.cue_ids if cue_id not in by_id]
    if missing:
        raise HTTPException(400, "Every cue_id must reference an existing synchronization cue")
    cues = [by_id[cue_id] for cue_id in body.cue_ids]
    if any(not _cue_overlaps_scene(cue, scene) for cue in cues):
        raise HTTPException(409, "Synchronization cues must overlap the selected scene")
    scene["sync_cue_ids"] = list(body.cue_ids)
    scene["updated_at"] = _now()
    _write_timeline(project_name, timeline)
    return _scene_sync_public(project_name, scene, _sort_cues(cues))


@router.get("/projects/{project_name}/video-sync/scenes/{scene_id}")
def get_video_scene_sync(project_name: str, scene_id: str, request: Request):
    _member(request)
    _, scene = _scene(project_name, scene_id)
    cues = resolve_scene_cues(project_name, scene)
    return _scene_sync_public(project_name, scene, cues)


__all__ = ["router", "resolve_scene_cues", "sync_prompt"]
