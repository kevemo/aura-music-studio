from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .jobs import StudioJobQueue
from .plans import PRIORITY_QUEUE
from .renderer_runtime import RendererUnavailable, require_render_capacity
from .tenant_storage import project_path

router = APIRouter(tags=["Aura Production Jobs"])
store = AccountStore()
queue = StudioJobQueue(store)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _public(job: dict) -> dict:
    value = dict(job)
    value.pop("payload_json", None)
    raw = value.pop("result_json", None)
    if raw:
        try:
            value["result"] = json.loads(raw)
        except Exception:
            value["result"] = None
    return value


def start_full_song_slot(member, project_name: str) -> dict:
    try:
        slot = store.start_song_slot(
            member.user_id,
            project_name,
            datetime.now(timezone.utc).date().isoformat(),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if slot.get("state") == "confirmed":
        raise HTTPException(403, "This track is already confirmed. Start a new project for another finished song.")
    return slot


@router.post("/projects/{project_name}/render-jobs")
def submit_render(project_name: str, request: Request):
    member = _member(request)
    try:
        project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc

    # Renderer admission happens before the Base daily slot is touched. An offline GPU must never
    # consume a member's daily confirmed-song allowance.
    try:
        capacity = require_render_capacity(task_type="text2music")
    except RendererUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    start_full_song_slot(member, project_name)
    priority = 100 if member.plan.has(PRIORITY_QUEUE) else 20
    job = queue.submit(member.user_id, project_name, job_type="produce", priority=priority)
    public = _public(job)
    public["renderer_admission"] = capacity
    return public


@router.get("/jobs")
def list_jobs(request: Request, limit: int = 50):
    member = _member(request)
    return [_public(job) for job in queue.list_for_user(member.user_id, limit=limit)]


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    member = _member(request)
    job = queue.get(job_id, user_id=member.user_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _public(job)


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, request: Request):
    member = _member(request)
    if not queue.cancel_queued(job_id, member.user_id):
        raise HTTPException(409, "Only queued jobs can be cancelled")
    return {"cancelled": True, "job_id": job_id}
