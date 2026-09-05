from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from .creation_coin_metering import (
    CreationCoinCharge,
    charge_free_video_render,
    free_video_render_quote,
    refund_free_video_render,
)
from .creative_project import CreativeDirective, CreativeProjectStore
from .creative_project_api import (
    QueueRendererRequest,
    creative_render_status,
    queue_creative_render,
    sync_creative_outputs,
)
from .creative_render_resource_governance import store as creative_render_resource_store
from .video_scene_timeline import _project_dir, _read, _write
from .video_visual_continuity import continuity_prompt, resolve_profiles

router = APIRouter(prefix="/creative", tags=["video-scene-render"])


class SceneRenderRequest(QueueRendererRequest):
    prompt_override: str | None = Field(default=None, min_length=1, max_length=6000)


def _member_identity(request: Request) -> tuple[str, str]:
    member = getattr(request.state, "member", None)
    user_id = str(getattr(member, "user_id", "") or "")
    plan = getattr(member, "plan", None)
    plan_id = str(getattr(plan, "id", "") or "")
    if not user_id or not plan_id:
        raise HTTPException(401, "Authenticated member identity and plan are required")
    return user_id, plan_id


def _scene(project_name: str, scene_id: str) -> tuple[dict, dict]:
    data = _read(project_name)
    scene = next((item for item in data["scenes"] if item["id"] == scene_id), None)
    if scene is None:
        raise HTTPException(404, "Video scene not found")
    return data, scene


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _scene_prompt(scene: dict, override: str | None, profiles: list[dict]) -> str:
    if override:
        base = override.strip()
    else:
        parts = [str(scene.get("description") or "").strip()]
        if scene.get("shot_type"):
            parts.append(f"Shot type: {scene['shot_type']}")
        if scene.get("camera_direction"):
            parts.append(f"Camera direction: {scene['camera_direction']}")
        if scene.get("continuity_notes"):
            parts.append(f"Continuity: {scene['continuity_notes']}")
        base = "\n".join(part for part in parts if part).strip()
    locks = continuity_prompt(profiles).strip()
    prompt = "\n\n".join(part for part in (base, locks) if part).strip()
    if not prompt:
        raise HTTPException(400, "Scene requires creative direction or a continuity profile before rendering")
    return prompt


def _continuity_inputs(scene: dict, profiles: list[dict]) -> tuple[list[str], list[str]]:
    references = list(scene.get("reference_ids") or [])
    preserved = list(scene.get("preserve_element_ids") or [])
    for profile in profiles:
        references.extend(profile.get("reference_ids") or [])
        preserved.extend(profile.get("preserve_element_ids") or [])
    references = _dedupe(references)
    preserved = _dedupe(preserved)
    if len(references) > 100 or len(preserved) > 100:
        raise HTTPException(400, "Combined scene continuity references exceed the renderer safety limit")
    return references, preserved


def _free_scene_video_charge(member, *, project_name: str, directive_id: str) -> CreationCoinCharge | None:
    """Apply the same server-authoritative Free video price to scene renders.

    Scene rendering has its own convenience route and therefore must not bypass the generic
    creative render commercial gate. Basic and Pro retain their subscription behavior.
    """

    if member.plan.id != "free":
        return None
    try:
        quote = free_video_render_quote(member.user_id)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not quote["enabled"]:
        raise HTTPException(
            403,
            {
                "message": "Video generation is not included in the Free tier and no Creation Coin purchase price is configured",
                "creation_coin_purchase": quote,
            },
        )
    if not quote["affordable"]:
        raise HTTPException(
            402,
            {
                "message": "More Creation Coins are required for this Free-tier video render",
                "creation_coin_purchase": quote,
            },
        )
    try:
        return charge_free_video_render(
            member.user_id,
            project_id=project_name,
            directive_id=directive_id,
        )
    except ValueError as exc:
        if "insufficient" in str(exc).lower():
            raise HTTPException(
                402,
                {
                    "message": "More Creation Coins are required for this Free-tier video render",
                    "creation_coin_purchase": free_video_render_quote(member.user_id),
                },
            ) from exc
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _scene_coin_state(member, charge: CreationCoinCharge | None) -> dict:
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


@router.post("/projects/{project_name}/video-timeline/scenes/{scene_id}/render")
def render_video_scene(project_name: str, scene_id: str, body: SceneRenderRequest, request: Request):
    user_id, plan_id = _member_identity(request)
    member = request.state.member
    data, scene = _scene(project_name, scene_id)
    if scene.get("status") == "rendering":
        raise HTTPException(409, "This scene already has a render in progress")

    project = _project_dir(project_name)
    store = CreativeProjectStore(project)
    manifest = store.load()
    previous_output = str(scene.get("output_element_id") or "").strip()
    if previous_output and not any(item.id == previous_output for item in manifest.elements):
        raise HTTPException(409, "Scene output linkage no longer exists in the creative manifest")

    profile_ids = list(scene.get("continuity_profile_ids") or [])
    profiles = resolve_profiles(project_name, profile_ids)
    reference_ids, preserve_element_ids = _continuity_inputs(scene, profiles)

    directive = CreativeDirective(
        instruction=_scene_prompt(scene, body.prompt_override, profiles),
        input_mode="text",
        operation="replace" if previous_output else "create",
        target_kind="video",
        target_element_ids=[previous_output] if previous_output else [],
        reference_ids=reference_ids,
        preserve_element_ids=preserve_element_ids,
        metadata={
            "video_scene": {
                "scene_id": scene_id,
                "start_seconds": scene.get("start_seconds"),
                "end_seconds": scene.get("end_seconds"),
                "previous_output_element_id": previous_output or None,
                "continuity_profile_ids": profile_ids,
            }
        },
    )
    try:
        store.add_directive(directive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

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

    charge: CreationCoinCharge | None = None
    try:
        charge = _free_scene_video_charge(
            member,
            project_name=project_name,
            directive_id=directive.id,
        )
    except Exception:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        raise

    try:
        response = queue_creative_render(project_name, directive.id, body, request)
    except Exception:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        if charge is not None:
            try:
                refund_free_video_render(
                    user_id,
                    charge,
                    reason="Creation Coin refund — scene video renderer did not accept the job",
                )
            except Exception:
                # Preserve the original renderer failure. Append-only charge evidence remains
                # available for owner reconciliation if the refund write itself fails.
                pass
        raise

    scene["status"] = "rendering"
    scene["render"] = {
        "directive_id": directive.id,
        "prompt_id": response["submission"]["prompt_id"],
        "provider": response["submission"]["provider"],
        "workflow_name": response["submission"]["workflow_name"],
        "continuity_profile_ids": profile_ids,
    }
    _write(project_name, data)
    return {
        "scene": scene,
        "directive": response["directive"],
        "submission": response["submission"],
        "resource_governance": reservation,
        "commercial_entitlements": {
            "video_generation": {
                "free_tier_creation_coin_purchase": _scene_coin_state(member, charge),
            }
        },
        "continuity_profiles_applied": profile_ids,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.get("/projects/{project_name}/video-timeline/scenes/{scene_id}/render-status")
def video_scene_render_status(project_name: str, scene_id: str, request: Request):
    _member_identity(request)
    data, scene = _scene(project_name, scene_id)
    render = scene.get("render")
    if not isinstance(render, dict) or not render.get("directive_id"):
        raise HTTPException(409, "This scene has not been submitted to the renderer")
    response = creative_render_status(project_name, str(render["directive_id"]), request)
    if response.get("renderer_status") == "failed":
        scene["status"] = "ready"
        _write(project_name, data)
    return {
        "scene_id": scene_id,
        "scene_status": scene.get("status"),
        "renderer_status": response.get("renderer_status"),
        "outputs": response.get("outputs", []),
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.post("/projects/{project_name}/video-timeline/scenes/{scene_id}/sync-output")
def sync_video_scene_output(project_name: str, scene_id: str, request: Request):
    _member_identity(request)
    data, scene = _scene(project_name, scene_id)
    render = scene.get("render")
    if not isinstance(render, dict) or not render.get("directive_id"):
        raise HTTPException(409, "This scene has not been submitted to the renderer")

    response = sync_creative_outputs(project_name, str(render["directive_id"]), request)
    imported = list(response.get("imported_elements") or [])
    video_outputs = [item for item in imported if item.get("kind") == "video"]
    if len(video_outputs) != 1:
        raise HTTPException(409, "Scene regeneration requires exactly one imported video output")

    scene["output_element_id"] = str(video_outputs[0]["id"])
    scene["status"] = "rendered"
    render["synced"] = True
    render["output_element_id"] = scene["output_element_id"]
    _write(project_name, data)
    return {
        "scene": scene,
        "imported_element": video_outputs[0],
        "previous_output_retained": True,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = [
    "_free_scene_video_charge",
    "router",
    "render_video_scene",
    "video_scene_render_status",
    "sync_video_scene_output",
]
