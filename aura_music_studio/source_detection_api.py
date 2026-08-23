from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .assets import AssetLibrary
from .plans import BUILD_AROUND_UPLOAD
from .source_detection import detect_source_role
from .tenant_storage import project_path

router = APIRouter(prefix="/production", tags=["production-source-analysis"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


@router.get("/projects/{project_name}/detect-source/{asset_id}")
def detect_source(project_name: str, asset_id: str, request: Request):
    member = _member(request)
    if not member.plan.has(BUILD_AROUND_UPLOAD):
        raise HTTPException(403, "Automatic build-around source detection unlocks on Base")
    try:
        project = project_path(project_name, must_exist=True)
        record = AssetLibrary(project).get(asset_id)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(404, "Project or asset not found") from exc
    if record.kind != "audio":
        raise HTTPException(400, "Source detection requires an audio asset")
    return detect_source_role(
        project / record.path,
        name=record.name,
        tags=record.tags,
    )
