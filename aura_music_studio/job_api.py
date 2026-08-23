from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .jobs import StudioJobQueue
from .plans import PRIORITY_QUEUE
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
    raw = value.pop("result_json", None)
    if raw:
        try:
            value["result"] = json.loads(raw)
        except Exception:
            value["result"] = None
    return value


@router.post("/projects/{project_name}/render-jobs")
def submit_render(project_name: str, request: Request):
    member = _member(request)
    try:
        project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc

    if member.plan.confirmed_songs_per_day is not None:
        try:
            slot = store.start_song_slot(
                member.user_id,
                project_name,
                datetime.now(timezone.utc).date().isoformat(),
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        if slot.get("state") == "confirmed":
            raise HTTPException(403, "This track is already confirmed. Start a new daily project.")

    priority = 100 if member.plan.has(PRIORITY_QUEUE) else 20
    job = queue.submit(member.user_id, project_name, job_type="produce", priority=priority)
    return _public(job)


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
