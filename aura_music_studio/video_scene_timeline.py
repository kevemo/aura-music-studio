from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .creative_project import CreativeProjectStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["video-scene-timeline"])

_MAX_SCENES = 500
_MAX_PROJECT_SECONDS = 4 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _project_dir(project_name: str) -> Path:
    try:
        directory = project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    try:
        CreativeProjectStore(directory).load()
    except FileNotFoundError as exc:
        raise HTTPException(404, "Creative manifest not initialized for this project") from exc
    return directory


def _timeline_path(project_name: str) -> Path:
    directory = _project_dir(project_name) / ".pulsar"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "video_scene_timeline.json"


def _empty_timeline(project_name: str) -> dict:
    return {
        "schema_version": 1,
        "project_name": project_name,
        "updated_at": _now(),
        "scenes": [],
    }


def _read(project_name: str) -> dict:
    path = _timeline_path(project_name)
    if not path.exists():
        return _empty_timeline(project_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Video scene timeline is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("scenes"), list):
        raise HTTPException(500, "Video scene timeline has an unsupported format")
    return data


def _write(project_name: str, data: dict) -> dict:
    path = _timeline_path(project_name)
    data = dict(data)
    data["schema_version"] = 1
    data["project_name"] = project_name
    data["updated_at"] = _now()
    serialized = json.dumps(data, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(prefix=".video-scene-", suffix=".json", dir=path.parent)
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


def _public(data: dict) -> dict:
    return {
        "schema_version": data["schema_version"],
        "project_name": data["project_name"],
        "updated_at": data["updated_at"],
        "scenes": data["scenes"],
        "scene_count": len(data["scenes"]),
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


def _validate_timeline(scenes: list[dict]) -> None:
    if len(scenes) > _MAX_SCENES:
        raise HTTPException(400, f"A video timeline can contain at most {_MAX_SCENES} scenes")
    ordered = sorted(scenes, key=lambda item: (float(item["start_seconds"]), int(item["order"])))
    prior_end = 0.0
    seen_ids: set[str] = set()
    for scene in ordered:
        scene_id = str(scene["id"])
        if scene_id in seen_ids:
            raise HTTPException(400, "Duplicate scene ID")
        seen_ids.add(scene_id)
        start = float(scene["start_seconds"])
        end = float(scene["end_seconds"])
        if start < 0 or end <= start or end > _MAX_PROJECT_SECONDS:
            raise HTTPException(400, "Scene timing is invalid")
        if start < prior_end - 0.000001:
            raise HTTPException(409, "Video scenes cannot overlap")
        prior_end = end


def _validate_source_image_element(project_name: str, element_id: str | None) -> None:
    if not element_id:
        return
    project = _project_dir(project_name)
    manifest = CreativeProjectStore(project).load()
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None:
        raise HTTPException(404, "Scene source image element not found")
    if element.kind != "image":
        raise HTTPException(400, "Scene source_image_element_id must reference a project image element")


class SceneCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=200)
    start_seconds: float = Field(ge=0, le=_MAX_PROJECT_SECONDS)
    end_seconds: float = Field(gt=0, le=_MAX_PROJECT_SECONDS)
    description: str = Field(default="", max_length=4000)
    shot_type: str = Field(default="", max_length=120)
    camera_direction: str = Field(default="", max_length=1000)
    continuity_notes: str = Field(default="", max_length=2000)
    continuity_profile_ids: list[str] = Field(default_factory=list, max_length=50)
    preserve_element_ids: list[str] = Field(default_factory=list, max_length=100)
    reference_ids: list[str] = Field(default_factory=list, max_length=100)
    source_image_element_id: str | None = Field(default=None, min_length=1, max_length=96)
    output_element_id: str | None = Field(default=None, max_length=96)
    status: Literal["planned", "ready", "rendering", "rendered", "approved"] = "planned"

    @model_validator(mode="after")
    def timing(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if len(self.continuity_profile_ids) != len(set(self.continuity_profile_ids)):
            raise ValueError("continuity_profile_ids cannot contain duplicates")
        return self


class SceneUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    start_seconds: float | None = Field(default=None, ge=0, le=_MAX_PROJECT_SECONDS)
    end_seconds: float | None = Field(default=None, gt=0, le=_MAX_PROJECT_SECONDS)
    description: str | None = Field(default=None, max_length=4000)
    shot_type: str | None = Field(default=None, max_length=120)
    camera_direction: str | None = Field(default=None, max_length=1000)
    continuity_notes: str | None = Field(default=None, max_length=2000)
    continuity_profile_ids: list[str] | None = Field(default=None, max_length=50)
    preserve_element_ids: list[str] | None = Field(default=None, max_length=100)
    reference_ids: list[str] | None = Field(default=None, max_length=100)
    source_image_element_id: str | None = Field(default=None, min_length=1, max_length=96)
    output_element_id: str | None = Field(default=None, max_length=96)
    status: Literal["planned", "ready", "rendering", "rendered", "approved"] | None = None

    @model_validator(mode="after")
    def continuity_ids(self):
        if self.continuity_profile_ids is not None and len(self.continuity_profile_ids) != len(set(self.continuity_profile_ids)):
            raise ValueError("continuity_profile_ids cannot contain duplicates")
        return self


class ReorderScenesRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1, max_length=_MAX_SCENES)


@router.get("/projects/{project_name}/video-timeline")
def get_video_timeline(project_name: str, request: Request):
    _member(request)
    return _public(_read(project_name))


@router.post("/projects/{project_name}/video-timeline/scenes")
def add_video_scene(project_name: str, body: SceneCreateRequest, request: Request):
    _member(request)
    _validate_source_image_element(project_name, body.source_image_element_id)
    data = _read(project_name)
    if any(scene["id"] == body.id for scene in data["scenes"]):
        raise HTTPException(409, "Scene ID already exists")
    scene = body.model_dump(mode="json")
    scene["order"] = len(data["scenes"])
    scene["created_at"] = _now()
    scene["updated_at"] = scene["created_at"]
    data["scenes"].append(scene)
    _validate_timeline(data["scenes"])
    return _public(_write(project_name, data))


@router.patch("/projects/{project_name}/video-timeline/scenes/{scene_id}")
def update_video_scene(project_name: str, scene_id: str, body: SceneUpdateRequest, request: Request):
    _member(request)
    updates = body.model_dump(exclude_unset=True, mode="json")
    if "source_image_element_id" in updates:
        _validate_source_image_element(project_name, updates.get("source_image_element_id"))
    data = _read(project_name)
    scene = next((item for item in data["scenes"] if item["id"] == scene_id), None)
    if scene is None:
        raise HTTPException(404, "Video scene not found")
    scene.update(updates)
    scene["updated_at"] = _now()
    _validate_timeline(data["scenes"])
    return _public(_write(project_name, data))


@router.delete("/projects/{project_name}/video-timeline/scenes/{scene_id}")
def delete_video_scene(project_name: str, scene_id: str, request: Request):
    _member(request)
    data = _read(project_name)
    before = len(data["scenes"])
    data["scenes"] = [item for item in data["scenes"] if item["id"] != scene_id]
    if len(data["scenes"]) == before:
        raise HTTPException(404, "Video scene not found")
    for index, scene in enumerate(data["scenes"]):
        scene["order"] = index
        scene["updated_at"] = _now()
    return _public(_write(project_name, data))


@router.post("/projects/{project_name}/video-timeline/reorder")
def reorder_video_scenes(project_name: str, body: ReorderScenesRequest, request: Request):
    _member(request)
    data = _read(project_name)
    current_ids = [scene["id"] for scene in data["scenes"]]
    if len(body.scene_ids) != len(set(body.scene_ids)) or set(body.scene_ids) != set(current_ids):
        raise HTTPException(400, "scene_ids must contain every current scene exactly once")
    by_id = {scene["id"]: scene for scene in data["scenes"]}
    data["scenes"] = [by_id[scene_id] for scene_id in body.scene_ids]
    for index, scene in enumerate(data["scenes"]):
        scene["order"] = index
        scene["updated_at"] = _now()
    return _public(_write(project_name, data))
