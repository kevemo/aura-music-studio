from __future__ import annotations

import json
from pathlib import Path

import soundfile as sf
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .daw import load_session, public_session, save_session
from .engineering_jobs import EngineeringJobRequest
from .jobs import StudioJobQueue
from .performance_inputs import get_input
from .plans import (
    ADVANCED_AUTOTUNE,
    ADVANCED_MASTERING,
    AUDIO_CLEANUP,
    BASIC_AUTOTUNE,
    BASIC_MASTERING,
    BASIC_STEM_SPLITTER,
    COVER_REMIX,
    DEEP_REVISION_HISTORY,
    MULTITRACK_DAW,
    PRIORITY_QUEUE,
    REFERENCE_MASTERING,
    REGION_REPAINT,
    SPATIAL_AUDIO,
    STANDARD_AUTOTUNE,
    STEM_SPLITTER,
)
from .revisions import create_revision
from .session import Clip
from .tenant_storage import project_path

router = APIRouter(tags=["Background Engineering Jobs"])
queue = StudioJobQueue()

_AUDIO_ROLES = {
    "vocals", "backing_vocals", "drums", "bass", "guitar", "piano", "keyboard", "strings",
    "synth", "percussion", "brass", "woodwinds", "fx", "other",
}


class EngineeringAssetDAWImport(BaseModel):
    asset_id: str = Field(min_length=1, max_length=128)
    role: str = Field(default="other", max_length=40)


class EngineeringAssetsDAWImportRequest(BaseModel):
    assets: list[EngineeringAssetDAWImport] = Field(min_length=1, max_length=24)


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

    if body.operation in {"smart_warp", "groove_follow"}:
        _require(member, MULTITRACK_DAW)
        return

    raise HTTPException(400, "Unsupported engineering operation")


def _validated_daw_assets(project: Path, body: EngineeringAssetsDAWImportRequest):
    """Resolve every requested asset before any DAW session mutation occurs."""
    library = AssetLibrary(project)
    root = project.resolve()
    seen: set[str] = set()
    validated = []
    for requested in body.assets:
        if requested.asset_id in seen:
            raise HTTPException(400, "Duplicate asset_id in DAW import request")
        seen.add(requested.asset_id)
        try:
            record = library.get(requested.asset_id)
        except KeyError as exc:
            raise HTTPException(404, "Engineering audio asset not found") from exc
        if record.kind != "audio":
            raise HTTPException(400, "The DAW can import audio engineering assets only")
        source = (root / record.path).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise HTTPException(400, "Engineering asset resolves outside the member project") from exc
        if not source.is_file():
            raise HTTPException(400, "Engineering audio asset is unavailable")
        try:
            info = sf.info(source)
            duration = float(info.frames / info.samplerate)
        except Exception as exc:
            raise HTTPException(400, "Engineering audio asset could not be inspected") from exc
        if duration <= 0:
            raise HTTPException(400, "Engineering audio asset has no usable duration")
        role = requested.role if requested.role in _AUDIO_ROLES else "other"
        validated.append((record, duration, role))
    return validated


def _import_validated_daw_assets(project: Path, validated, member, project_name: str) -> dict:
    session = load_session(project, create=True, name=project_name)
    existing_asset_ids = {
        str(clip.metadata.get("asset_id"))
        for track in session.tracks
        for clip in track.clips
        if clip.metadata.get("asset_id")
    }
    pending = [item for item in validated if item[0].id not in existing_asset_ids]

    if pending and (project / "aura_session.json").is_file():
        keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 40
        try:
            create_revision(
                project,
                label="Before importing engineering stem set",
                reason="daw_edit",
                actor="Studio member",
                keep=keep,
            )
        except Exception:
            pass

    imported = []
    for record, duration, role in pending:
        track = session.add_track(Path(record.name).stem or role.replace("_", " ").title(), role)
        clip = Clip(
            name=record.name,
            kind="audio",
            source=Path(record.path).as_posix(),
            start=0.0,
            duration=duration,
            metadata={
                "asset_id": record.id,
                "real_audio": True,
                "imported_to_daw": True,
                "engineering_asset": True,
            },
        )
        track.clips.append(clip)
        imported.append({
            "asset_id": record.id,
            "track_id": track.id,
            "clip_id": clip.id,
            "role": role,
        })

    if imported:
        save_session(project, session)

    return {
        "imported": imported,
        "imported_count": len(imported),
        "already_present_asset_ids": sorted(existing_asset_ids.intersection({item[0].id for item in validated})),
        "session": public_session(session),
        "atomic_validation": True,
        "source_paths_exposed": False,
    }


def _split_job_daw_request(job: dict) -> EngineeringAssetsDAWImportRequest:
    if job.get("job_type") != "engineering:split":
        raise HTTPException(400, "Only completed stem-split jobs can be imported directly into the DAW")
    if job.get("status") != "completed":
        raise HTTPException(409, "Stem-split job is not completed")
    raw = job.get("result_json")
    try:
        result = json.loads(raw) if raw else None
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "Completed stem-split job has no usable result") from exc
    if not isinstance(result, dict) or result.get("operation") != "split":
        raise HTTPException(409, "Completed stem-split job has no usable split result")
    stem_assets = result.get("stem_assets")
    if not isinstance(stem_assets, dict) or not stem_assets:
        raise HTTPException(409, "Completed stem-split job has no reusable stem assets")
    assets = []
    for role, descriptor in stem_assets.items():
        if not isinstance(descriptor, dict):
            raise HTTPException(409, "Completed stem-split job contains an invalid stem asset")
        asset_id = descriptor.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise HTTPException(409, "Completed stem-split job contains an invalid stem asset")
        assets.append(EngineeringAssetDAWImport(asset_id=asset_id.strip(), role=str(role)))
    try:
        return EngineeringAssetsDAWImportRequest(assets=assets)
    except ValueError as exc:
        raise HTTPException(409, "Completed stem-split job contains too many or invalid stem assets") from exc


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
    if body.operation == "smart_warp":
        try:
            target_input = get_input(project, str(body.target_performance_input_id or ""))
        except KeyError as exc:
            raise HTTPException(404, "Smart Warp target performance input not found") from exc
        if not target_input.rights_confirmed or not target_input.metadata.get("rights_record_id"):
            raise HTTPException(409, "Smart Warp target performance input has no complete rights/provenance record")
        if len(target_input.beat_times_seconds) < 4:
            raise HTTPException(409, "Smart Warp target requires at least four detected timing anchors")
    if body.operation == "groove_follow":
        try:
            target_input = get_input(project, str(body.target_performance_input_id or ""))
        except KeyError as exc:
            raise HTTPException(404, "Groove Follow target performance input not found") from exc
        if not target_input.rights_confirmed or not target_input.metadata.get("rights_record_id"):
            raise HTTPException(409, "Groove Follow target performance input has no complete rights/provenance record")
        if len(target_input.beat_times_seconds) < 4:
            raise HTTPException(409, "Groove Follow target requires at least four detected beats")
        if len(target_input.onset_times_seconds) < 4:
            raise HTTPException(409, "Groove Follow target requires at least four detected onsets")

    priority = 90 if member.plan.has(PRIORITY_QUEUE) else 15
    job = queue.submit(
        member.user_id,
        project_name,
        job_type=f"engineering:{body.operation}",
        priority=priority,
        payload=body.model_dump(mode="json"),
    )
    return _public(job)


@router.post("/production/projects/{project_name}/engineering-assets/import-daw")
def import_engineering_assets_to_daw(
    project_name: str,
    body: EngineeringAssetsDAWImportRequest,
    request: Request,
):
    member = _member(request)
    # A stem set is inherently multitrack. Gate before project lookup to avoid existence probing.
    _require(member, MULTITRACK_DAW)
    project = _project(project_name)

    # Validate the complete batch before loading/mutating the editable session. A bad final stem
    # must not leave the first stems partially imported.
    validated = _validated_daw_assets(project, body)
    return _import_validated_daw_assets(project, validated, member, project_name)


@router.post("/production/projects/{project_name}/engineering-jobs/{job_id}/import-daw")
def import_completed_split_job_to_daw(project_name: str, job_id: str, request: Request):
    member = _member(request)
    # Gate before job/project lookup so a denied tier cannot probe either resource.
    _require(member, MULTITRACK_DAW)
    job = queue.get(job_id, user_id=member.user_id)
    if not job:
        raise HTTPException(404, "Engineering job not found")
    if job.get("project_name") != project_name:
        raise HTTPException(404, "Engineering job not found")
    body = _split_job_daw_request(job)
    project = _project(project_name)
    validated = _validated_daw_assets(project, body)
    result = _import_validated_daw_assets(project, validated, member, project_name)
    result.update({
        "source_job_id": job_id,
        "source_job_type": "engineering:split",
        "job_result_paths_exposed": False,
    })
    return result
