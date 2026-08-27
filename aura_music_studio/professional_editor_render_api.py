from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .plans import AUTOMATION, BASIC_TIMELINE, MUSIC_VIDEO_DOWNLOAD
from .professional_editor import ProfessionalEditorStore
from .professional_editor_renderer import (
    EditorRenderError,
    EditorRenderUnsupported,
    ProfessionalEditorRenderer,
)
from .professional_image_compositor import AdvancedImageCompositor
from .professional_video_compositor import AdvancedVideoCompositor
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Professional Creative Editor Rendering"])


class EditorRenderRequest(BaseModel):
    format: Literal["png", "webp", "jpeg", "mp4"]
    quality: int = Field(default=92, ge=1, le=100)
    # Image sequences may contain transform/effect keyframes even though the exported file is
    # a still. frame_time lets the member choose which authored frame to flatten.
    frame_time: float = Field(default=0.0, ge=0.0, le=86400.0)


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


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/render")
def render_editor_sequence(
    project_name: str,
    sequence_id: str,
    body: EditorRenderRequest,
    request: Request,
):
    member = _member(request)
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
            video_renderer = AdvancedVideoCompositor(_project(project_name))
            result = video_renderer.render_video_advanced(sequence_id)
        else:
            image_renderer = AdvancedImageCompositor(_project(project_name))
            result = image_renderer.render_image_advanced(
                sequence_id,
                format=expected,
                quality=body.quality,
                frame_time=body.frame_time,
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
        "non_destructive": True,
        "source_media_mutated": False,
        "frame_time": body.frame_time if sequence["kind"] == "image" else None,
    }


@router.get("/projects/{project_name}/editor/exports/{filename}")
def download_editor_export(project_name: str, filename: str, request: Request):
    member = _member(request)
    renderer = _renderer(project_name)
    try:
        path = renderer.resolve_export(filename)
    except (FileNotFoundError, EditorRenderError) as exc:
        raise HTTPException(404, "Editor export not found") from exc

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
        },
    )


__all__ = ["router", "EditorRenderRequest"]
