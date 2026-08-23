from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY
from .revisions import create_revision, list_revisions, restore_revision
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
        raise HTTPException(403, "Project revision history unlocks on Base")
    rows = list_revisions(_project(project_name))
    return {"limit": keep, "deep_history": member.plan.has(DEEP_REVISION_HISTORY), "revisions": rows[:keep]}


@router.post("/projects/{project_name}/revisions")
def snapshot(project_name: str, body: SnapshotRequest, request: Request):
    member = _member(request)
    keep = _limit(member)
    if keep <= 0:
        raise HTTPException(403, "Project revision history unlocks on Base")
    return create_revision(
        _project(project_name),
        label=body.label,
        reason=body.reason,
        actor=member.user.get("display_name") or "Member",
        keep=keep,
    )


@router.post("/projects/{project_name}/revisions/{revision_id}/restore")
def restore(project_name: str, revision_id: str, request: Request):
    member = _member(request)
    keep = _limit(member)
    if keep <= 0:
        raise HTTPException(403, "Project revision history unlocks on Base")
    try:
        return restore_revision(_project(project_name), revision_id, create_backup=True, keep=keep)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
