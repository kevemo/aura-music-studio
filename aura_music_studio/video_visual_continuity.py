from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .creative_project import CreativeProjectStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["video-visual-continuity"])

_MAX_PROFILES = 200


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


def _continuity_path(project_name: str) -> Path:
    directory = _project_dir(project_name) / ".pulsar"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "video_visual_continuity.json"


def _empty(project_name: str) -> dict:
    return {
        "schema_version": 1,
        "project_name": project_name,
        "updated_at": _now(),
        "profiles": [],
    }


def _read(project_name: str) -> dict:
    path = _continuity_path(project_name)
    if not path.exists():
        return _empty(project_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Video continuity data is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("profiles"), list):
        raise HTTPException(500, "Video continuity data has an unsupported format")
    return data


def _write(project_name: str, data: dict) -> dict:
    path = _continuity_path(project_name)
    data = dict(data)
    data["schema_version"] = 1
    data["project_name"] = project_name
    data["updated_at"] = _now()
    serialized = json.dumps(data, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(prefix=".video-continuity-", suffix=".json", dir=path.parent)
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
        "profiles": data["profiles"],
        "profile_count": len(data["profiles"]),
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


class ContinuityProfileCreate(BaseModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["character", "style", "environment", "prop"]
    description: str = Field(default="", max_length=4000)
    appearance_lock: str = Field(default="", max_length=3000)
    wardrobe_lock: str = Field(default="", max_length=2000)
    palette_lock: str = Field(default="", max_length=1000)
    negative_constraints: list[str] = Field(default_factory=list, max_length=50)
    reference_ids: list[str] = Field(default_factory=list, max_length=100)
    preserve_element_ids: list[str] = Field(default_factory=list, max_length=100)


class ContinuityProfileUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["character", "style", "environment", "prop"] | None = None
    description: str | None = Field(default=None, max_length=4000)
    appearance_lock: str | None = Field(default=None, max_length=3000)
    wardrobe_lock: str | None = Field(default=None, max_length=2000)
    palette_lock: str | None = Field(default=None, max_length=1000)
    negative_constraints: list[str] | None = Field(default=None, max_length=50)
    reference_ids: list[str] | None = Field(default=None, max_length=100)
    preserve_element_ids: list[str] | None = Field(default=None, max_length=100)


def resolve_profiles(project_name: str, profile_ids: list[str]) -> list[dict]:
    if not profile_ids:
        return []
    if len(profile_ids) != len(set(profile_ids)):
        raise HTTPException(400, "continuity_profile_ids cannot contain duplicates")
    data = _read(project_name)
    by_id = {str(profile.get("id")): profile for profile in data["profiles"]}
    missing = [profile_id for profile_id in profile_ids if profile_id not in by_id]
    if missing:
        raise HTTPException(409, "Scene references a missing video continuity profile")
    return [by_id[profile_id] for profile_id in profile_ids]


def continuity_prompt(profiles: list[dict]) -> str:
    blocks: list[str] = []
    for profile in profiles:
        lines = [f"Continuity lock — {profile['kind']} {profile['label']}"]
        for label, key in (
            ("Description", "description"),
            ("Appearance", "appearance_lock"),
            ("Wardrobe", "wardrobe_lock"),
            ("Palette", "palette_lock"),
        ):
            value = str(profile.get(key) or "").strip()
            if value:
                lines.append(f"{label}: {value}")
        negatives = [str(value).strip() for value in profile.get("negative_constraints") or [] if str(value).strip()]
        if negatives:
            lines.append("Do not change: " + "; ".join(negatives))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@router.get("/projects/{project_name}/video-continuity")
def get_video_continuity(project_name: str, request: Request):
    _member(request)
    return _public(_read(project_name))


@router.post("/projects/{project_name}/video-continuity/profiles")
def add_video_continuity_profile(project_name: str, body: ContinuityProfileCreate, request: Request):
    _member(request)
    data = _read(project_name)
    if len(data["profiles"]) >= _MAX_PROFILES:
        raise HTTPException(400, f"A project can contain at most {_MAX_PROFILES} video continuity profiles")
    if any(profile.get("id") == body.id for profile in data["profiles"]):
        raise HTTPException(409, "Continuity profile ID already exists")
    profile = body.model_dump(mode="json")
    profile["created_at"] = _now()
    profile["updated_at"] = profile["created_at"]
    data["profiles"].append(profile)
    return _public(_write(project_name, data))


@router.patch("/projects/{project_name}/video-continuity/profiles/{profile_id}")
def update_video_continuity_profile(project_name: str, profile_id: str, body: ContinuityProfileUpdate, request: Request):
    _member(request)
    data = _read(project_name)
    profile = next((item for item in data["profiles"] if item.get("id") == profile_id), None)
    if profile is None:
        raise HTTPException(404, "Video continuity profile not found")
    profile.update(body.model_dump(exclude_unset=True, mode="json"))
    profile["updated_at"] = _now()
    return _public(_write(project_name, data))


@router.delete("/projects/{project_name}/video-continuity/profiles/{profile_id}")
def delete_video_continuity_profile(project_name: str, profile_id: str, request: Request):
    _member(request)
    data = _read(project_name)
    before = len(data["profiles"])
    data["profiles"] = [item for item in data["profiles"] if item.get("id") != profile_id]
    if len(data["profiles"]) == before:
        raise HTTPException(404, "Video continuity profile not found")
    return _public(_write(project_name, data))


__all__ = ["router", "resolve_profiles", "continuity_prompt"]
