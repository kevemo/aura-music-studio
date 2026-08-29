from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .commercial_entitlement_routes import _free_video_charge
from .creation_coin_metering import CreationCoinCharge, free_video_render_quote, refund_free_video_render
from .creative_project import CreativeDirective, CreativeProjectStore
from .creative_render_resource_governance import store as creative_render_resource_store
from .creative_renderers import renderer_for
from .video_scene_render import _member_identity
from .video_scene_timeline import _project_dir

router = APIRouter(prefix="/creative", tags=["image-to-video"])

_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif"}


class ImageToVideoRenderRequest(BaseModel):
    source_element_id: str = Field(min_length=1, max_length=96)
    instruction: str = Field(min_length=1, max_length=6000)
    negative_prompt: str = Field(default="", max_length=4000)
    rights_confirmed: bool = False
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    width: int = Field(default=1024, ge=64, le=4096)
    height: int = Field(default=1024, ge=64, le=4096)
    frames: int = Field(default=121, ge=1, le=10000)
    fps: float = Field(default=24.0, ge=1.0, le=120.0)


def _resolve_project_image(project_name: str, element_id: str) -> tuple[Path, CreativeProjectStore, object, object, Path]:
    project = _project_dir(project_name).resolve()
    store = CreativeProjectStore(project)
    try:
        manifest = store.load()
    except FileNotFoundError as exc:
        raise HTTPException(404, "Creative manifest not found") from exc
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None:
        raise HTTPException(404, "Creative image element not found")
    if element.kind != "image":
        raise HTTPException(400, "Image-to-video requires a project image element")
    if element.status != "ready":
        raise HTTPException(409, "Source image must be ready before image-to-video rendering")

    source_ref = str(element.source_ref or "").strip()
    if (
        not source_ref
        or "://" in source_ref
        or source_ref.startswith(("/", "\\"))
        or "\\" in source_ref
    ):
        raise HTTPException(400, "Source image does not reference project-owned media")
    relative = Path(source_ref)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "Source image does not reference project-owned media")
    source = (project / relative).resolve()
    if project not in source.parents:
        raise HTTPException(400, "Source image is outside the member project")
    if not source.is_file():
        raise HTTPException(404, "Source image file is unavailable")
    if source.suffix.lower() not in _ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(415, "Source image format is not supported for image-to-video rendering")
    return project, store, manifest, element, source


def _coin_state(member, charge: CreationCoinCharge | None) -> dict:
    if member.plan.id != "free":
        return {
            "required": False,
            "reason": "included_subscription_behavior",
            "charged": False,
            "charged_amount": 0,
            "membership_effect": "none",
            "esp_role_effect": "none",
        }
    quote = free_video_render_quote(member.user_id)
    return {
        **quote,
        "required": True,
        "charged": charge is not None,
        "charged_amount": charge.cost if charge is not None else 0,
        "charge_transaction_id": charge.transaction.get("id") if charge is not None else None,
        "membership_effect": "none",
        "esp_role_effect": "none",
    }


@router.post("/projects/{project_name}/image-to-video/render")
def render_project_image_to_video(
    project_name: str,
    body: ImageToVideoRenderRequest,
    request: Request,
):
    user_id, plan_id = _member_identity(request)
    member = request.state.member
    if not body.rights_confirmed:
        raise HTTPException(
            400,
            "Confirm that you own or are authorized to animate the selected image and any depicted likenesses",
        )

    project, store, manifest, element, source = _resolve_project_image(project_name, body.source_element_id)

    workflow_name = (os.getenv("AURA_COMFYUI_IMAGE_TO_VIDEO_WORKFLOW") or "").strip()
    if not workflow_name:
        raise HTTPException(503, "Image-to-video workflow is not configured on this deployment")
    renderer = renderer_for("video")
    renderer.workflow_name = workflow_name
    if not renderer.configured:
        raise HTTPException(503, "Image-to-video renderer is not configured on this deployment")

    directive = CreativeDirective(
        instruction=body.instruction,
        input_mode="upload",
        operation="create",
        target_kind="video",
        target_element_ids=[element.id],
        metadata={
            "image_to_video": {
                "source_element_id": element.id,
                "rights_confirmed": True,
                "source_kind": "project_image",
            }
        },
    )

    # Reserve scarce render capacity before persisting the directive. A quota or policy denial
    # therefore cannot leave an orphan project directive that was never eligible to render.
    try:
        reservation = creative_render_resource_store.reserve(
            user_id=user_id,
            plan_id=plan_id,
            project_name=project_name,
            directive_id=directive.id,
            media_kind="video",
            width=body.width,
            height=body.height,
            frames=body.frames,
        )
    except PermissionError as exc:
        raise HTTPException(429, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, "Creative renderer resource policy is misconfigured") from exc

    try:
        store.add_directive(directive)
    except ValueError as exc:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        raise HTTPException(400, str(exc)) from exc

    try:
        renderer_input = renderer.upload_image_input(source)
    except Exception as exc:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        store.update_directive(directive.id, status="failed")
        raise HTTPException(502, f"Image-to-video source handoff failed: {type(exc).__name__}: {exc}") from exc

    charge: CreationCoinCharge | None = None
    try:
        charge = _free_video_charge(
            member,
            project_name=project_name,
            directive_id=directive.id,
        )
    except Exception:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        store.update_directive(directive.id, status="failed")
        raise

    seed = body.seed if body.seed is not None else secrets.randbelow(2**31 - 1)
    variables = {
        "prompt": body.instruction,
        "negative_prompt": body.negative_prompt,
        "seed": seed,
        "width": body.width,
        "height": body.height,
        "frames": body.frames,
        "fps": body.fps,
        "project_name": manifest.project_name,
        "project_title": manifest.title,
        "directive_id": directive.id,
        "operation": directive.operation,
        "source_image": renderer_input.workflow_value,
    }

    try:
        submission = renderer.submit(variables)
    except Exception as exc:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        store.update_directive(directive.id, status="failed")
        if charge is not None:
            try:
                refund_free_video_render(
                    user_id,
                    charge,
                    reason="Creation Coin refund — image-to-video renderer did not accept the job",
                )
            except Exception:
                pass
        raise HTTPException(502, f"Image-to-video renderer submission failed: {type(exc).__name__}: {exc}") from exc

    manifest = store.update_directive(
        directive.id,
        status="queued",
        capability_state="connected",
        renderer_route=f"comfyui:{submission.workflow_name}",
        metadata={
            "creative_renderer": {
                "provider": submission.provider,
                "kind": submission.kind,
                "prompt_id": submission.prompt_id,
                "client_id": submission.client_id,
                "workflow_name": submission.workflow_name,
                "seed": seed,
                "width": body.width,
                "height": body.height,
                "frames": body.frames,
                "fps": body.fps,
                "input": {
                    "source_element_id": element.id,
                    "renderer_name": renderer_input.name,
                    "renderer_subfolder": renderer_input.subfolder,
                    "renderer_type": renderer_input.type,
                },
            }
        },
    )
    queued = next(item for item in manifest.directives if item.id == directive.id)
    return {
        "directive": queued.model_dump(mode="json"),
        "submission": submission.model_dump(mode="json"),
        "resource_governance": reservation,
        "commercial_entitlements": {
            "video_generation": {
                "free_tier_creation_coin_purchase": _coin_state(member, charge),
            }
        },
        "source": {
            "element_id": element.id,
            "kind": element.kind,
            "project_owned": True,
            "rights_confirmed": True,
            "raw_filesystem_path_exposed": False,
            "client_renderer_filename_accepted": False,
            "client_workflow_selection_accepted": False,
        },
        "render_status_path": f"/creative/projects/{project_name}/directives/{directive.id}/render-status",
        "sync_outputs_path": f"/creative/projects/{project_name}/directives/{directive.id}/sync-outputs",
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = [
    "ImageToVideoRenderRequest",
    "_resolve_project_image",
    "render_project_image_to_video",
    "router",
]
