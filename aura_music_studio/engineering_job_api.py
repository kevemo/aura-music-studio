from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .assets import AssetLibrary
from .engineering_jobs import EngineeringJobRequest
from .jobs import StudioJobQueue
from .plans import (
    ADVANCED_AUTOTUNE,
    ADVANCED_MASTERING,
    AUDIO_CLEANUP,
    BASIC_AUTOTUNE,
    BASIC_MASTERING,
    BASIC_STEM_SPLITTER,
    COVER_REMIX,
    PRIORITY_QUEUE,
    REFERENCE_MASTERING,
    REGION_REPAINT,
    SPATIAL_AUDIO,
    STANDARD_AUTOTUNE,
    STEM_SPLITTER,
)
from .tenant_storage import project_path

router = APIRouter(tags=["Background Engineering Jobs"])
queue = StudioJobQueue()


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


def _require(member, feature: str):
    if not member.plan.has(feature):
        raise HTTPException(403, f"{feature} requires a higher membership tier")


def _public(job: dict) -> dict:
    value = dict(job)
    value.pop("payload_json", None)
    value.pop("result_json", None)
    return value


def _validate_entitlement(member, body: EngineeringJobRequest) -> None:
    if body.operation == "split":
        if body.split_mode in {"two_stems", "four_stems"}:
            _require(member, BASIC_STEM_SPLITTER)
        elif body.split_mode in {"six_stems", "detailed"}:
            _require(member, STEM_SPLITTER)
        else:
            raise HTTPException(400, "Invalid split mode")
        return

    if body.operation == "master":
        _require(member, BASIC_MASTERING)
        advanced = any([
            abs(body.low_db) > .01,
            abs(body.mid_db) > .01,
            abs(body.high_db) > .01,
            body.stereo_width is not None,
            body.target_lufs is not None,
        ])
        if advanced:
            _require(member, ADVANCED_MASTERING)
        if body.reference_asset_id:
            _require(member, REFERENCE_MASTERING)
        return

    if body.operation == "autotune":
        _require(member, BASIC_AUTOTUNE)
        mode = body.tune_settings.mode
        if mode in {"classic", "hard"}:
            _require(member, STANDARD_AUTOTUNE)
        if mode in {"robot", "custom"}:
            _require(member, ADVANCED_AUTOTUNE)
        return

    if body.operation == "restore":
        _require(member, AUDIO_CLEANUP)
        return

    if body.operation == "spatial":
        _require(member, SPATIAL_AUDIO)
        return

    if body.operation == "cover":
        _require(member, COVER_REMIX)
        return

    if body.operation == "repaint":
        _require(member, REGION_REPAINT)
        return

    raise HTTPException(400, "Unsupported engineering operation")


@router.post("/production/projects/{project_name}/engineering-jobs")
def submit_engineering_job(project_name: str, body: EngineeringJobRequest, request: Request):
    member = _member(request)
    # Entitlement is checked before tenant/project lookup so a denied tier cannot probe project existence.
    _validate_entitlement(member, body)
    project = _project(project_name)

    library = AssetLibrary(project)
    try:
        asset = library.get(body.asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Audio asset not found") from exc
    if asset.kind != "audio":
        raise HTTPException(400, "Engineering jobs require an audio asset")
    if body.reference_asset_id:
        try:
            reference = library.get(body.reference_asset_id)
        except KeyError as exc:
            raise HTTPException(404, "Reference asset not found") from exc
        if reference.kind != "audio":
            raise HTTPException(400, "Reference mastering requires an audio asset")

    priority = 90 if member.plan.has(PRIORITY_QUEUE) else 15
    job = queue.submit(
        member.user_id,
        project_name,
        job_type=f"engineering:{body.operation}",
        priority=priority,
        payload=body.model_dump(mode="json"),
    )
    return _public(job)
