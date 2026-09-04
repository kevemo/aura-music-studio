from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeVar
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .export_provenance import store as export_provenance_store
from .plans import AUTOMATION, BASIC_TIMELINE, MUSIC_VIDEO_DOWNLOAD
# Production inheritance remains explicit for regression/audit tooling:
# professional_video_mask_effects_colour_compositor -> professional_video_mask_crop_compositor ->
# professional_video_track_keyframe_universal_compositor -> professional_video_track_keyframe_compositor ->
# professional_keyframed_mask_video_compositor -> professional_chroma_key_video_compositor ->
# professional_universal_scoped_visual_video_compositor -> grouped professional compositor.
# Wave 12 allows supported item effects + authored masks + actually rendered item colour controls
# to coexist in their established non-destructive stage order. Automatic tracking, unsupported
# colour controls and unsupported automation paths remain fail-closed.
from .professional_video_mask_effects_colour_compositor import UniversalVisualVideoCompositor
from .professional_editor import ProfessionalEditorStore
from .professional_editor_renderer import (
    EditorRenderError,
    EditorRenderUnsupported,
    ProfessionalEditorRenderer,
)
from .professional_universal_image_compositor import UniversalImageCompositor
from .tenant_storage import project_path
from .tier2_daily_meter import TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID
from .tier2_provider_guard import Tier2ProviderGuard

router = APIRouter(prefix="/creative", tags=["Professional Creative Editor Rendering"])
tier2_guard = Tier2ProviderGuard()
T = TypeVar("T")


class EditorRenderRequest(BaseModel):
    format: Literal["png", "webp", "jpeg", "mp4"]
    quality: int = Field(default=92, ge=1, le=100)
    frame_time: float = Field(default=0.0, ge=0.0, le=86400.0)
    commercial_use: bool = False
    rights_attested: bool = False


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional creative rendering unlocks on the Basic membership tier")
    return member


def _project(project_name: str):
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _renderer(project_name: str) -> ProfessionalEditorRenderer:
    project = _project(project_name)
    store = ProfessionalEditorStore(project)
    if not store.exists():
        raise HTTPException(404, "Professional editor is not initialized for this project")
    return ProfessionalEditorRenderer(project)


def _video_render_request_key(request: Request) -> str:
    """Return a bounded retry key while preserving clients that pre-date idempotency headers."""
    supplied = request.headers.get("Idempotency-Key") or request.headers.get("X-Request-ID")
    if supplied is not None:
        value = supplied.strip()
        if not value or len(value) > 180:
            raise HTTPException(400, "A bounded idempotency request key is required")
        return value
    return f"video-edit-render-{uuid4().hex}"


def _execute_video_render(member, request: Request, provider_call: Callable[[], T]) -> T:
    """Meter paid video editing immediately around the self-hosted compositor execution."""
    plan_id = str(getattr(member.plan, "id", "") or "").strip().lower()
    if plan_id not in {TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID}:
        # Free/legacy accounts retain their existing, separately-authorized entitlement path.
        return provider_call()
    result, _admission = tier2_guard.execute(
        user_id=member.user_id,
        plan_id=plan_id,
        operation="video_edit",
        request_key=_video_render_request_key(request),
        provider_call=provider_call,
    )
    return result


def _sequence_has_non_normal_item_blend(state: dict, sequence_id: str) -> bool:
    branch = state.get("branch") or {}
    sequences = {value.get("id"): value for value in branch.get("sequences", [])}
    tracks = {value.get("id"): value for value in branch.get("tracks", [])}
    items = {value.get("id"): value for value in branch.get("items", [])}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return False
    for track_id in sequence.get("track_ids", []):
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for item_id in track.get("item_ids", []):
            item = items.get(item_id)
            if not item or not item.get("enabled", True):
                continue
            if str(item.get("blend_mode") or "normal").strip().lower() != "normal":
                return True
    return False


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/render")
def render_editor_sequence(
    project_name: str,
    sequence_id: str,
    body: EditorRenderRequest,
    request: Request,
):
    member = _member(request)
    user_id = str(getattr(member, "user_id", "") or "")
    if not user_id:
        raise HTTPException(401, "Authenticated member identity unavailable")
    if body.commercial_use and not body.rights_attested:
        raise HTTPException(
            400,
            "Commercial-use export requires an explicit confirmation that you own or are licensed to use all supplied material.",
        )

    renderer = _renderer(project_name)
    try:
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
        if advanced["advanced"] and not member.plan.has(AUTOMATION):
            raise PermissionError(
                "This sequence contains Pro masks, effects or keyframes. The project is preserved, but Pro is required to render that advanced state."
            )

        if sequence["kind"] == "video":
            if not member.plan.has(MUSIC_VIDEO_DOWNLOAD):
                raise PermissionError("Video export requires a membership tier with video downloads")
            # The production Video Studio renderer preserves grouped/scoped ordering: supported item
            # effects feed the alpha-safe authored-mask derivative, crop is applied afterward, then
            # rendered item colour controls execute before the established transform/blend/track
            # stages. Existing track automation remains executable; unsupported colour paths and
            # automatic tracking continue to fail closed.
            video_renderer = UniversalVisualVideoCompositor(_project(project_name))
            result = _execute_video_render(
                member,
                request,
                lambda: video_renderer.render_video_advanced(sequence_id),
            )
        else:
            # The universal Image Designer compositor consumes shared namespaced image contracts
            # while inheriting the established non-destructive Pillow layer/mask/blend/keyframe
            # renderer. Image exports stay local and are not one of the paid Tier 2 provider calls.
            image_renderer = UniversalImageCompositor(_project(project_name))
            result = image_renderer.render_image_advanced(
                sequence_id,
                format=expected,
                quality=body.quality,
                frame_time=body.frame_time,
            )

        output_path = renderer.resolve_export(result.filename)
        provenance = export_provenance_store.record_export(
            user_id=user_id,
            project_name=project_name,
            sequence_id=sequence_id,
            filename=result.filename,
            media_kind=sequence["kind"],
            format=expected,
            path=output_path,
            commercial_use_requested=body.commercial_use,
            rights_attested=body.rights_attested,
        )
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except EditorRenderUnsupported as exc:
        raise HTTPException(422, str(exc)) from exc
    except (EditorRenderError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "export": result.model_dump(mode="json"),
        "download_url": f"/creative/projects/{project_name}/editor/exports/{result.filename}",
        "provenance": provenance,
        "commercial_release_status": "review_required" if body.commercial_use else "not_requested",
        "automatic_legal_clearance": False,
        "non_destructive": True,
        "source_media_mutated": False,
        "frame_time": body.frame_time if sequence["kind"] == "image" else None,
    }


@router.get("/projects/{project_name}/editor/exports/{filename}")
def download_editor_export(project_name: str, filename: str, request: Request):
    member = _member(request)
    user_id = str(getattr(member, "user_id", "") or "")
    if not user_id:
        raise HTTPException(401, "Authenticated member identity unavailable")
    renderer = _renderer(project_name)
    try:
        path = renderer.resolve_export(filename)
    except (FileNotFoundError, EditorRenderError) as exc:
        raise HTTPException(404, "Editor export not found") from exc

    provenance = export_provenance_store.latest_for_file(user_id, project_name, filename)
    if provenance and provenance["commercial_use_requested"] and not provenance["commercial_platform_export_allowed"]:
        raise HTTPException(
            403,
            "This commercial-use export is awaiting IP/similarity review. A platform review does not replace legal advice or guarantee copyrightability/non-infringement.",
        )

    suffix = path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".webp": "image/webp",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
    }
    media_type = media_types.get(suffix)
    if media_type is None:
        raise HTTPException(404, "Unsupported editor export type")
    if suffix == ".mp4" and not member.plan.has(MUSIC_VIDEO_DOWNLOAD):
        raise HTTPException(403, "Video export requires a membership tier with video downloads")

    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Aura-Editor-Export": "non-destructive",
            "X-Aura-Automatic-Legal-Clearance": "false",
        },
    )


__all__ = [
    "router",
    "EditorRenderRequest",
    "_execute_video_render",
    "_sequence_has_non_normal_item_blend",
]
