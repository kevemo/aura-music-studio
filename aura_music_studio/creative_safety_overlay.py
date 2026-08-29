from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .commercial_entitlement_routes import render_with_commercial_entitlements as base_commercial_render
from .content_safety import enforce_creation_policy
from .creative_project import CreativeProjectStore
from .creative_project_api import (
    CreateDirectiveRequest,
    QueueRendererRequest,
    add_directive as base_add_directive,
)
from .tenant_storage import project_path
from .video_image_to_video import (
    ImageToVideoRenderRequest,
    render_project_image_to_video as base_image_to_video_render,
)
from .video_scene_render import (
    SceneRenderRequest,
    _scene,
    _scene_prompt,
    render_video_scene as base_scene_render,
)
from .video_visual_continuity import resolve_profiles

router = APIRouter(tags=["creative-safety-overlay"])


def _require_member(request: Request) -> None:
    if getattr(request.state, "member", None) is None:
        raise HTTPException(401, "Membership context unavailable")


def _enforce(text: str, *, context: str) -> None:
    try:
        enforce_creation_policy(text, context=context)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _stored_directive_instruction(project_name: str, directive_id: str) -> str:
    try:
        project = project_path(project_name, must_exist=True)
        manifest = CreativeProjectStore(project).load()
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    if directive is None:
        raise HTTPException(404, "Aura directive not found")
    return str(directive.instruction or "")


def _stored_scene_instruction(project_name: str, scene_id: str, prompt_override: str | None) -> str:
    _, scene = _scene(project_name, scene_id)
    profile_ids = list(scene.get("continuity_profile_ids") or [])
    profiles = resolve_profiles(project_name, profile_ids)
    return _scene_prompt(scene, prompt_override, profiles)


@router.post("/creative/projects/{project_name}/directives")
def add_directive_with_creation_safety(
    project_name: str,
    body: CreateDirectiveRequest,
    request: Request,
):
    _require_member(request)
    _enforce(body.instruction, context="Aura creative directive")
    return base_add_directive(project_name, body, request)


@router.post("/creative/projects/{project_name}/directives/{directive_id}/render")
def render_with_creation_safety(
    project_name: str,
    directive_id: str,
    body: QueueRendererRequest,
    request: Request,
):
    _require_member(request)
    instruction = _stored_directive_instruction(project_name, directive_id)
    _enforce(instruction, context="Creative renderer submission")
    return base_commercial_render(project_name, directive_id, body, request)


@router.post("/creative/projects/{project_name}/image-to-video/render")
def image_to_video_with_creation_safety(
    project_name: str,
    body: ImageToVideoRenderRequest,
    request: Request,
):
    _require_member(request)
    _enforce(body.instruction, context="Image-to-video render")
    return base_image_to_video_render(project_name, body, request)


@router.post("/creative/projects/{project_name}/video-timeline/scenes/{scene_id}/render")
def scene_render_with_creation_safety(
    project_name: str,
    scene_id: str,
    body: SceneRenderRequest,
    request: Request,
):
    _require_member(request)
    instruction = _stored_scene_instruction(project_name, scene_id, body.prompt_override)
    _enforce(instruction, context="Video scene render")
    return base_scene_render(project_name, scene_id, body, request)


__all__ = [
    "add_directive_with_creation_safety",
    "image_to_video_with_creation_safety",
    "render_with_creation_safety",
    "router",
    "scene_render_with_creation_safety",
]
