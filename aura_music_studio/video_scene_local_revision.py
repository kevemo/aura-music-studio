from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from .creative_project_api import QueueRendererRequest
from .video_scene_render import SceneRenderRequest, _member_identity, _scene, render_video_scene
from .video_scene_timeline import _read, _write

router = APIRouter(prefix="/creative", tags=["video-scene-local-revision"])

EditScope = Literal["object", "background", "lighting", "camera", "text_treatment", "color_grade", "style_detail"]


class LocalizedSceneRevisionRequest(QueueRendererRequest):
    edit_scope: EditScope
    target_description: str = Field(min_length=1, max_length=500)
    desired_change: str = Field(min_length=1, max_length=2000)
    preserve_notes: str = Field(default="", max_length=1500)
    preserve_everything_else: bool = True


def localized_revision_prompt(body: LocalizedSceneRevisionRequest) -> str:
    if not body.preserve_everything_else:
        raise HTTPException(
            400,
            "Localized revision requires preserve_everything_else=true. Use normal scene regeneration for broad changes.",
        )
    parts = [
        "LOCALIZED SCENE REVISION.",
        f"Edit scope: {body.edit_scope}.",
        f"Target only: {body.target_description.strip()}.",
        f"Requested change: {body.desired_change.strip()}.",
        "Preserve all other scene content, character identity, composition, timing and established continuity unless the requested scope necessarily changes it.",
    ]
    if body.preserve_notes.strip():
        parts.append(f"Additional preserve constraints: {body.preserve_notes.strip()}")
    return "\n".join(parts)


@router.post("/projects/{project_name}/video-timeline/scenes/{scene_id}/localized-revision")
def render_localized_scene_revision(
    project_name: str,
    scene_id: str,
    body: LocalizedSceneRevisionRequest,
    request: Request,
):
    _member_identity(request)
    _, scene = _scene(project_name, scene_id)
    previous_output = str(scene.get("output_element_id") or "").strip()
    if not previous_output:
        raise HTTPException(409, "Localized revision requires an existing rendered scene output")

    prompt = localized_revision_prompt(body)
    render_body = SceneRenderRequest(
        negative_prompt=body.negative_prompt,
        seed=body.seed,
        width=body.width,
        height=body.height,
        frames=body.frames,
        fps=body.fps,
        variables=body.variables,
        prompt_override=prompt,
    )
    response = render_video_scene(project_name, scene_id, render_body, request)

    data = _read(project_name)
    current = next((item for item in data["scenes"] if item["id"] == scene_id), None)
    if current is None:
        raise HTTPException(409, "Scene disappeared while recording localized revision evidence")
    render = current.get("render")
    if not isinstance(render, dict):
        raise HTTPException(500, "Scene renderer did not record render evidence")
    render["localized_revision"] = {
        "edit_scope": body.edit_scope,
        "target_description": body.target_description.strip(),
        "desired_change": body.desired_change.strip(),
        "preserve_notes": body.preserve_notes.strip(),
        "preserve_everything_else": True,
        "source_output_element_id": previous_output,
        "localization_method": "instruction_scoped_scene_regeneration",
        "pixel_mask_enforced": False,
        "unchanged_regions_guaranteed": False,
    }
    _write(project_name, data)

    if isinstance(response, dict):
        response["localized_revision"] = render["localized_revision"]
        response["automatic_pixel_localization_guaranteed"] = False
        response["grants_esp_role_or_permission"] = False
        response["alters_billing_or_membership"] = False
    return response


__all__ = ["router", "LocalizedSceneRevisionRequest", "localized_revision_prompt", "render_localized_scene_revision"]
