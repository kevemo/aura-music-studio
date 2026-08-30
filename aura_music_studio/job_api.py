from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .jobs import StudioJobQueue
from .plans import PRIORITY_QUEUE
from .tenant_storage import project_path
from .tier2_daily_meter import TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID
from .tier2_provider_guard import Tier2ProviderGuard

router = APIRouter(tags=["Aura Production Jobs"])
store = AccountStore()
queue = StudioJobQueue(store)
tier2_guard = Tier2ProviderGuard()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _public(job: dict) -> dict:
    value = dict(job)
    # Payloads may contain private lyrics, prompts, voice settings or production instructions.
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


def _render_request_key(request: Request) -> str:
    """Return a bounded idempotency key without requiring legacy clients to send one."""
    supplied = request.headers.get("Idempotency-Key") or request.headers.get("X-Request-ID")
    if supplied is not None:
        value = supplied.strip()
        if not value or len(value) > 180:
            raise HTTPException(400, "A bounded idempotency request key is required")
        return value
    # Older clients did not send idempotency headers. A unique server key preserves their
    # behaviour while newer clients can make retries deterministic with Idempotency-Key.
    return f"music-render-{uuid4().hex}"


@router.post("/projects/{project_name}/render-jobs")
def submit_render(project_name: str, request: Request):
    member = _member(request)
    try:
        project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc

    plan_id = str(getattr(member.plan, "id", "") or "").strip().lower()
    priority = 100 if member.plan.has(PRIORITY_QUEUE) else 20

    if plan_id in {TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID}:
        request_key = _render_request_key(request)
        try:
            job, _admission = tier2_guard.execute(
                user_id=member.user_id,
                plan_id=plan_id,
                operation="music_create",
                request_key=request_key,
                provider_call=lambda: queue.submit(
                    member.user_id,
                    project_name,
                    job_type="produce",
                    priority=priority,
                ),
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _public(job)

    # Free and any legacy/non-paid membership stay on their existing entitlement path. The
    # Tier 2 cross-Studio allowance must not grant those accounts paid production capacity.
    start_full_song_slot(member, project_name)
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
