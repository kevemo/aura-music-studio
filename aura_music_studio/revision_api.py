from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY
from .revisions import compare_revisions, create_revision, list_revisions, restore_revision
from .tenant_storage import project_path

router = APIRouter(tags=["Project Revisions"])


class SnapshotRequest(BaseModel):
    label: str = Field(default="Manual snapshot", min_length=1, max_length=160)
    reason: str = Field(default="manual", max_length=80)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _project(name: str):
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _limit(member) -> int:
    if member.plan.has(DEEP_REVISION_HISTORY):
        return 200
    if member.plan.has(REVISION_HISTORY):
        return 20
    return 0


@router.get("/projects/{project_name}/revisions")
def revisions(project_name: str, request: Request):
    member = _member(request)
    keep = _limit(member)
    if keep <= 0:
        raise HTTPException(403, "Project revision history unlocks on Basic")
    rows = list_revisions(_project(project_name))
    return {
        "limit": keep,
        "deep_history": member.plan.has(DEEP_REVISION_HISTORY),
        "cross_editor_checkpoints": True,
        "revision_domains": ["music_daw", "creative_manifest", "professional_image_video_editor", "production_metadata"],
        "media_files_duplicated": False,
        "revisions": rows[:keep],
    }


@router.post("/projects/{project_name}/revisions")
def snapshot(project_name: str, body: SnapshotRequest, request: Request):
    member = _member(request)
    keep = _limit(member)
    if keep <= 0:
        raise HTTPException(403, "Project revision history unlocks on Basic")
    return create_revision(
        _project(project_name),
        label=body.label,
        reason=body.reason,
        actor=member.user.get("display_name") or "Member",
        keep=keep,
    )


@router.get("/projects/{project_name}/revisions/compare")
def compare_project_revisions(
    project_name: str,
    request: Request,
    left: str = Query(min_length=1, max_length=120),
    right: str = Query(min_length=1, max_length=120),
):
    member = _member(request)
    if not member.plan.has(DEEP_REVISION_HISTORY):
        raise HTTPException(403, "Cross-editor checkpoint comparison requires Pro")
    try:
        return compare_revisions(_project(project_name), left, right)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{project_name}/revisions/{revision_id}/restore")
def restore(project_name: str, revision_id: str, request: Request):
    member = _member(request)
    keep = _limit(member)
    if keep <= 0:
        raise HTTPException(403, "Project revision history unlocks on Basic")
    try:
        return restore_revision(_project(project_name), revision_id, create_backup=True, keep=keep)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


__all__ = ["router"]
