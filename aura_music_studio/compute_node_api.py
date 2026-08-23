from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .compute_nodes import ComputeNodeRegistry
from .jobs import StudioJobQueue
from .node_transfer import apply_result_bundle, build_project_bundle
from .request_context import reset_current_user_id, set_current_user_id
from .revisions import create_revision
from .tenant_storage import project_path

router = APIRouter(prefix="/node-coordinator", tags=["ESP Compute Nodes"])
store = AccountStore()
registry = ComputeNodeRegistry(store)
queue = StudioJobQueue(store)


class EnrollRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    name: str = Field(default="ESP Compute Node", min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=lambda: ["music_generation", "engineering"], max_length=32)
    hardware: dict = Field(default_factory=dict)
    software: dict = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    capabilities: list[str] | None = Field(default=None, max_length=32)
    hardware: dict | None = None
    software: dict | None = None


class FailRequest(BaseModel):
    error: str = Field(min_length=1, max_length=8000)


def _enabled() -> None:
    if os.getenv("LSS_NODE_COORDINATOR_ENABLED", "true").lower() != "true":
        raise HTTPException(503, "ESP compute-node coordinator is disabled")


def _remote_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _node_auth(node_id: str | None, authorization: str | None) -> dict:
    _enabled()
    if not node_id or not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Compute-node authentication required")
    secret = authorization[7:].strip()
    try:
        return registry.authenticate(node_id, secret)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


def _job_types(capabilities: list[str]) -> list[str]:
    caps = {str(x).strip().lower() for x in capabilities}
    result: set[str] = set()
    if "music_generation" in caps or "all" in caps:
        result.update({"produce", "build_around"})
    if "engineering" in caps or "all" in caps:
        result.update({
            "engineering:split",
            "engineering:master",
            "engineering:autotune",
            "engineering:restore",
            "engineering:spatial",
        })
    if "stem_separation" in caps:
        result.add("engineering:split")
    if "mastering" in caps:
        result.add("engineering:master")
    if "autotune" in caps:
        result.add("engineering:autotune")
    if "restoration" in caps:
        result.add("engineering:restore")
    if "spatial_audio" in caps:
        result.add("engineering:spatial")
    return sorted(result)


def _worker_id(node_id: str) -> str:
    return f"node:{node_id}"


def _bundle_path(job_id: str) -> Path:
    root = Path(os.getenv("LSS_NODE_BUNDLE_DIR", "data/node_bundles"))
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{job_id}.zip"


def _public_claim(job: dict) -> dict:
    payload = {}
    try:
        payload = json.loads(job.get("payload_json") or "{}")
    except Exception:
        pass
    return {
        "id": job["id"],
        "job_type": job["job_type"],
        "project_name": job["project_name"],
        "priority": job["priority"],
        "attempts": job["attempts"],
        "payload": payload if isinstance(payload, dict) else {},
        "bundle_url": f"/node-coordinator/jobs/{job['id']}/bundle",
        "lease_url": f"/node-coordinator/jobs/{job['id']}/lease",
        "result_url": f"/node-coordinator/jobs/{job['id']}/result",
        "fail_url": f"/node-coordinator/jobs/{job['id']}/fail",
    }


@router.post("/enroll")
def enroll(body: EnrollRequest, request: Request):
    _enabled()
    try:
        return registry.enroll(
            body.token,
            name=body.name,
            capabilities=body.capabilities,
            hardware=body.hardware,
            software=body.software,
            remote_ip=_remote_ip(request),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/heartbeat")
def heartbeat(
    body: HeartbeatRequest,
    request: Request,
    x_esp_node_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    node = _node_auth(x_esp_node_id, authorization)
    return registry.heartbeat(
        node["id"],
        capabilities=body.capabilities,
        hardware=body.hardware,
        software=body.software,
        remote_ip=_remote_ip(request),
    )


@router.post("/claim")
def claim_job(
    request: Request,
    x_esp_node_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    node = _node_auth(x_esp_node_id, authorization)
    registry.heartbeat(node["id"], remote_ip=_remote_ip(request))
    allowed = _job_types(node.get("capabilities") or [])
    job = queue.claim_next_for_job_types(_worker_id(node["id"]), allowed)
    if not job:
        return {"job": None, "allowed_job_types": allowed}
    return {"job": _public_claim(job)}


@router.post("/jobs/{job_id}/lease")
def renew_lease(
    job_id: str,
    request: Request,
    x_esp_node_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    node = _node_auth(x_esp_node_id, authorization)
    registry.heartbeat(node["id"], remote_ip=_remote_ip(request))
    if not queue.renew_owned(job_id, _worker_id(node["id"])):
        raise HTTPException(409, "This node no longer owns the job lease")
    return {"renewed": True, "job_id": job_id}


@router.get("/jobs/{job_id}/bundle")
def download_bundle(
    job_id: str,
    x_esp_node_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    node = _node_auth(x_esp_node_id, authorization)
    job = queue.get(job_id)
    if not job or job.get("status") != "running" or job.get("worker_id") != _worker_id(node["id"]):
        raise HTTPException(404, "This node does not own the requested job lease")
    target = _bundle_path(job_id)
    try:
        target.unlink(missing_ok=True)
        build_project_bundle(job, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not build node job bundle: {type(exc).__name__}: {exc}") from exc
    return FileResponse(
        target,
        media_type="application/zip",
        filename=f"ESP_Node_Job_{job_id}.zip",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/jobs/{job_id}/result")
async def upload_result(
    job_id: str,
    result: UploadFile = File(...),
    x_esp_node_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    node = _node_auth(x_esp_node_id, authorization)
    worker_id = _worker_id(node["id"])
    job = queue.get(job_id)
    if not job or job.get("status") != "running" or job.get("worker_id") != worker_id:
        raise HTTPException(409, "This node no longer owns the job lease")

    max_bytes = max(64 * 1024 * 1024, int(os.getenv("LSS_NODE_MAX_BUNDLE_BYTES", str(2 * 1024**3))))
    with tempfile.TemporaryDirectory(prefix="lss-node-upload-") as tmp:
        archive = Path(tmp) / "result.zip"
        total = 0
        with archive.open("wb") as handle:
            while chunk := await result.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, "Node result bundle exceeds configured limit")
                handle.write(chunk)
        if total < 64:
            raise HTTPException(400, "Node result bundle is empty")

        context = set_current_user_id(job["user_id"])
        try:
            project = project_path(job["project_name"], must_exist=True)
            create_revision(
                project,
                label=f"Before applying ESP compute-node result {job_id[:8]}",
                reason="compute_node_result",
                actor=node.get("name") or "ESP Compute Node",
                keep=200,
            )
        finally:
            reset_current_user_id(context)

        try:
            merged = apply_result_bundle(job, archive)
        except Exception as exc:
            raise HTTPException(400, f"Node result rejected: {type(exc).__name__}: {exc}") from exc

    result_summary = {
        "remote_compute_node": node["id"],
        "remote_compute_node_name": node.get("name"),
        **merged,
    }
    if not queue.complete_owned(job_id, worker_id, result_summary):
        raise HTTPException(409, "Job lease changed before completion could be recorded")
    if job.get("job_type") in {"produce", "build_around"}:
        try:
            store.record_regeneration(job["user_id"], job["project_name"])
        except Exception:
            pass
    _bundle_path(job_id).unlink(missing_ok=True)
    return {"accepted": True, **result_summary}


@router.post("/jobs/{job_id}/fail")
def fail_job(
    job_id: str,
    body: FailRequest,
    x_esp_node_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    node = _node_auth(x_esp_node_id, authorization)
    ok = queue.fail_owned(job_id, _worker_id(node["id"]), f"Remote node {node.get('name')}: {body.error}")
    _bundle_path(job_id).unlink(missing_ok=True)
    if not ok:
        raise HTTPException(409, "This node no longer owns the job lease")
    return {"failed": True, "job_id": job_id}
