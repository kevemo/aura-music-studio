from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .video_image_to_video import ImageToVideoRenderRequest, render_project_image_to_video
from .video_scene_render import _member_identity, _scene_prompt
from .video_scene_timeline import _read, _write
from .video_visual_continuity import resolve_profiles

router = APIRouter(prefix="/creative", tags=["video-scene-image-to-video"])


class SceneImageToVideoRenderRequest(BaseModel):
    rights_confirmed: bool = False
    prompt_override: str | None = Field(default=None, min_length=1, max_length=6000)
    negative_prompt: str = Field(default="", max_length=4000)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    width: int = Field(default=1024, ge=64, le=4096)
    height: int = Field(default=1024, ge=64, le=4096)
    frames: int | None = Field(default=None, ge=1, le=10000)
    fps: float = Field(default=24.0, ge=1.0, le=120.0)


def _scene_frames(scene: dict, requested: int | None, fps: float) -> int:
    if requested is not None:
        return requested
    duration = float(scene["end_seconds"]) - float(scene["start_seconds"])
    frames = max(1, int(round(duration * fps)) + 1)
    if frames > 10000:
        raise HTTPException(
            400,
            "Scene duration and frame rate exceed the image-to-video renderer frame limit",
        )
    return frames


@router.post("/projects/{project_name}/video-timeline/scenes/{scene_id}/render-image-to-video")
def render_scene_image_to_video(
    project_name: str,
    scene_id: str,
    body: SceneImageToVideoRenderRequest,
    request: Request,
):
    _member_identity(request)
    if not body.rights_confirmed:
        raise HTTPException(
            400,
            "Confirm that you own or are authorized to animate the selected scene image and any depicted likenesses",
        )

    data = _read(project_name)
    scene = next((item for item in data["scenes"] if item["id"] == scene_id), None)
    if scene is None:
        raise HTTPException(404, "Video scene not found")
    if scene.get("status") == "rendering":
        raise HTTPException(409, "This scene already has a render in progress")

    source_image_element_id = str(scene.get("source_image_element_id") or "").strip()
    if not source_image_element_id:
        raise HTTPException(409, "Select a project image for this scene before image-to-video rendering")

    profile_ids = list(scene.get("continuity_profile_ids") or [])
    profiles = resolve_profiles(project_name, profile_ids)
    instruction = _scene_prompt(scene, body.prompt_override, profiles)
    frames = _scene_frames(scene, body.frames, body.fps)

    render_body = ImageToVideoRenderRequest(
        source_element_id=source_image_element_id,
        instruction=instruction,
        negative_prompt=body.negative_prompt,
        rights_confirmed=True,
        seed=body.seed,
        width=body.width,
        height=body.height,
        frames=frames,
        fps=body.fps,
    )
    response = render_project_image_to_video(project_name, render_body, request)

    submission = dict(response.get("submission") or {})
    directive = dict(response.get("directive") or {})
    directive_id = str(directive.get("id") or "").strip()
    prompt_id = str(submission.get("prompt_id") or "").strip()
    if not directive_id or not prompt_id:
        raise HTTPException(502, "Image-to-video renderer returned incomplete scene linkage")

    scene["status"] = "rendering"
    scene["render"] = {
        "mode": "image_to_video",
        "directive_id": directive_id,
        "prompt_id": prompt_id,
        "provider": submission.get("provider"),
        "workflow_name": submission.get("workflow_name"),
        "source_image_element_id": source_image_element_id,
        "continuity_profile_ids": profile_ids,
    }
    _write(project_name, data)

    return {
        "scene": scene,
        "directive": directive,
        "submission": submission,
        "resource_governance": response.get("resource_governance"),
        "commercial_entitlements": response.get("commercial_entitlements"),
        "source": {
            "element_id": source_image_element_id,
            "scene_bound": True,
            "project_owned": True,
            "rights_confirmed": True,
            "raw_filesystem_path_exposed": False,
        },
        "scene_sync_output_path": (
            f"/creative/projects/{project_name}/video-timeline/scenes/{scene_id}/sync-output"
        ),
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = [
    "SceneImageToVideoRenderRequest",
    "_scene_frames",
    "render_scene_image_to_video",
    "router",
]
