from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .export_provenance import store as export_provenance_store
from .jobs import StudioJobQueue
from .plans import AUTOMATION, BASIC_TIMELINE, MUSIC_VIDEO_DOWNLOAD, PRIORITY_QUEUE, get_plan
from .professional_editor import ProfessionalEditorStore
from .professional_editor_render_api import EditorRenderRequest
from .professional_editor_renderer import EditorRenderError, EditorRenderUnsupported, ProfessionalEditorRenderer
from .professional_image_compositor import AdvancedImageCompositor
from .professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Professional Creative Editor Render Jobs"])
account_store = AccountStore()
queue = StudioJobQueue(account_store)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    value = dict(job)
    value.pop("payload_json", None)
    raw = value.pop("result_json", None)
    if raw:
        try:
            value["result"] = json.loads(raw)
        except Exception:
            value["result"] = None
    return value


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional creative rendering unlocks on the Basic membership tier")
    return member


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _renderer(project: Path) -> ProfessionalEditorRenderer:
    store = ProfessionalEditorStore(project)
    if not store.exists():
        raise FileNotFoundError("Professional editor is not initialized for this project")
    return ProfessionalEditorRenderer(project)


def _validate_render(plan, renderer: ProfessionalEditorRenderer, sequence_id: str, body: EditorRenderRequest) -> tuple[dict, str]:
    if body.commercial_use and not body.rights_attested:
        raise ValueError(
            "Commercial-use export requires an explicit confirmation that you own or are licensed to use all supplied material."
        )

    state = renderer.store.public_state()
    sequences = {value["id"]: value for value in state["branch"].get("sequences", [])}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        raise KeyError(sequence_id)

    expected = "mp4" if sequence["kind"] == "video" else body.format
    if sequence["kind"] == "video" and body.format != "mp4":
        raise ValueError("Video sequences export as MP4")
    if sequence["kind"] == "image" and body.format == "mp4":
        raise ValueError("Image sequences export as PNG, WebP or JPEG")
    if sequence["kind"] == "video" and body.frame_time != 0.0:
        raise ValueError("frame_time applies to still-image sequence exports only")

    advanced = renderer.advanced_state(state, sequence_id)
    if advanced["advanced"] and not plan.has(AUTOMATION):
        raise PermissionError(
            "This sequence contains Pro masks, effects or keyframes. The project is preserved, but Pro is required to render that advanced state."
        )
    if sequence["kind"] == "video" and not plan.has(MUSIC_VIDEO_DOWNLOAD):
        raise PermissionError("Video export requires a membership tier with video downloads")
    return sequence, expected


def _active_plan(store: AccountStore, user_id: str):
    user = store.get_user(user_id)
    if not user or user.get("status") != "active":
        raise PermissionError("Active membership required")
    return get_plan(str(user.get("plan_id") or "free"))


def run_editor_render_job(
    project: Path,
    payload: dict[str, Any],
    *,
    user_id: str,
    account_store: AccountStore | None = None,
) -> dict[str, Any]:
    """Execute one editor export after re-checking the member's current plan."""
    store = account_store or AccountStore()
    plan = _active_plan(store, user_id)
    if not plan.has(BASIC_TIMELINE):
        raise PermissionError("Professional creative rendering unlocks on the Basic membership tier")

    sequence_id = str(payload.get("sequence_id") or "").strip()
    if not sequence_id:
        raise ValueError("sequence_id is required")
    body = EditorRenderRequest.model_validate(payload.get("render") or {})
    renderer = _renderer(project)
    sequence, expected = _validate_render(plan, renderer, sequence_id, body)

    if sequence["kind"] == "video":
        result = GroupedUnifiedAdvancedVideoCompositor(project).render_video_advanced(sequence_id)
    else:
        result = AdvancedImageCompositor(project).render_image_advanced(
            sequence_id,
            format=expected,
            quality=body.quality,
            frame_time=body.frame_time,
        )

    output_path = renderer.resolve_export(result.filename)
    provenance = export_provenance_store.record_export(
        user_id=user_id,
        project_name=project.name,
        sequence_id=sequence_id,
        filename=result.filename,
        media_kind=sequence["kind"],
        format=expected,
        path=output_path,
        commercial_use_requested=body.commercial_use,
        rights_attested=body.rights_attested,
    )
    return {
        "export": result.model_dump(mode="json"),
        "download_url": f"/creative/projects/{project.name}/editor/exports/{result.filename}",
        "provenance": provenance,
        "commercial_release_status": "review_required" if body.commercial_use else "not_requested",
        "automatic_legal_clearance": False,
        "non_destructive": True,
        "source_media_mutated": False,
        "frame_time": body.frame_time if sequence["kind"] == "image" else None,
    }


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/render-jobs")
def submit_editor_render_job(
    project_name: str,
    sequence_id: str,
    body: EditorRenderRequest,
    request: Request,
):
    member = _member(request)
    user_id = str(getattr(member, "user_id", "") or "")
    if not user_id:
        raise HTTPException(401, "Authenticated member identity unavailable")

    project = _project(project_name)
    try:
        renderer = _renderer(project)
        _validate_render(member.plan, renderer, sequence_id, body)
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (EditorRenderError, EditorRenderUnsupported, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    priority = 100 if member.plan.has(PRIORITY_QUEUE) else 20
    job = queue.submit(
        user_id,
        project_name,
        job_type="editor_render",
        priority=priority,
        payload={"sequence_id": sequence_id, "render": body.model_dump(mode="json")},
    )
    return _public_job(job)


__all__ = [
    "router",
    "run_editor_render_job",
    "submit_editor_render_job",
    "_validate_render",
]
