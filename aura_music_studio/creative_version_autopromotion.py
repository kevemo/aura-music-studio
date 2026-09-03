from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .commercial_entitlement_routes import router as commercial_entitlement_router
from .compliance_applicability import owner_router as owner_compliance_applicability_router, router as compliance_applicability_router
from .creative_project import CreativeDirective, CreativeProjectStore
from .creative_project_api import (
    QueueRendererRequest,
    queue_creative_render as base_queue_creative_render,
    sync_creative_outputs as base_sync_creative_outputs,
)
from .creative_render_resource_governance import store as creative_render_resource_store
from .creative_safety_overlay import router as creative_safety_overlay_router
from .daw_mixer_ui import daw_mixer_ui as base_daw_mixer_ui
from .deep_daw_automation_api import router as deep_daw_automation_router
from .deep_daw_automation_ui import enhance_daw_mixer_javascript
from .esp_live_compliance import member_router as esp_live_compliance_router, owner_router as owner_esp_live_compliance_router
from .export_provenance import member_router as export_provenance_router, owner_router as owner_export_provenance_router
from .global_compliance import router as global_compliance_router
from .ip_rights import member_router as member_ip_rights_router, owner_router as owner_ip_rights_router
from .music_video_storyboard import router as music_video_storyboard_router
from .owner_finance import router as owner_finance_router
from .privacy_case_management import router as privacy_case_management_router
from .privacy_consent import router as privacy_consent_router
from .privacy_fulfilment import owner_router as owner_privacy_fulfilment_router
from .privacy_rights import router as privacy_rights_router
from .professional_editor_api import router as professional_editor_router
from .professional_editor_inspector_overlay import router as professional_editor_workspace_router
from .professional_editor_lifecycle_api import router as professional_editor_lifecycle_router
from .professional_editor_render_api import router as professional_editor_render_router
from .professional_editor_visual_entitlements import router as professional_editor_visual_entitlement_router
from .safeguarding_runtime import owner_router as owner_safeguarding_runtime_router
from .safety_appeal_review import router as owner_safety_appeal_router
from .safety_reports import member_router as member_safety_router, owner_router as owner_safety_router
from .stripe_billing import router as stripe_billing_router
from .stripe_billing_hardening import router as stripe_billing_hardening_router
from .stripe_commerce_receipts import router as stripe_commerce_receipts_router
from .stripe_membership_checkout import router as stripe_membership_checkout_router
from .tenant_storage import project_path
from .video_audio_analysis_ingestion import router as video_audio_analysis_router
from .video_image_to_video import router as video_image_to_video_router
from .video_lyric_sync_ingestion import router as video_lyric_sync_ingestion_router
from .video_music_sync import router as video_music_sync_router
from .video_scene_image_to_video import router as video_scene_image_to_video_router
from .video_scene_local_revision import router as video_scene_local_revision_router
from .video_scene_render import router as video_scene_render_router
from .video_scene_timeline import router as video_scene_timeline_router
from .video_sync_entitlement_routes import router as video_sync_entitlement_router
from .video_visual_continuity import router as video_visual_continuity_router

router = APIRouter(tags=["Creative Version Promotion"])
# Safety must be the first duplicate-path layer so prohibited intent is rejected before
# billing/Creation Coin charging, capacity reservation, project mutation or renderer submission.
router.include_router(creative_safety_overlay_router)
router.include_router(commercial_entitlement_router)
router.include_router(global_compliance_router)
router.include_router(compliance_applicability_router)
router.include_router(owner_compliance_applicability_router)
router.include_router(owner_safeguarding_runtime_router)
router.include_router(esp_live_compliance_router)
router.include_router(owner_esp_live_compliance_router)
router.include_router(privacy_rights_router)
router.include_router(privacy_case_management_router)
router.include_router(owner_privacy_fulfilment_router)
router.include_router(privacy_consent_router)
router.include_router(member_safety_router)
router.include_router(owner_safety_router)
router.include_router(owner_safety_appeal_router)
router.include_router(member_ip_rights_router)
router.include_router(owner_ip_rights_router)
router.include_router(export_provenance_router)
router.include_router(owner_export_provenance_router)
router.include_router(stripe_commerce_receipts_router)
# Membership checkout/status must resolve before the generic Stripe layers so the exact
# owner-approved plan/period contract is enforced before any provider resource is created.
router.include_router(stripe_membership_checkout_router)
router.include_router(stripe_billing_hardening_router)
router.include_router(stripe_billing_router)
router.include_router(owner_finance_router)
router.include_router(deep_daw_automation_router)
router.include_router(video_scene_timeline_router)
router.include_router(video_visual_continuity_router)
router.include_router(video_scene_render_router)
router.include_router(video_scene_image_to_video_router)
router.include_router(video_scene_local_revision_router)
router.include_router(video_image_to_video_router)
# These duplicate-path wrappers must precede the base video/music sync routers. Starlette resolves
# the first matching route, so Free/Basic direct API calls are denied before project lookup or I/O.
router.include_router(video_sync_entitlement_router)
router.include_router(video_lyric_sync_ingestion_router)
router.include_router(video_music_sync_router)
router.include_router(video_audio_analysis_router)
router.include_router(music_video_storyboard_router)
# Pro visual entitlement routes intentionally precede the generic editor/render routes so direct
# API calls cannot bypass the same blend/group gates shown in the professional browser inspector.
router.include_router(professional_editor_visual_entitlement_router)
router.include_router(professional_editor_router)
router.include_router(professional_editor_lifecycle_router)
router.include_router(professional_editor_render_router)
router.include_router(professional_editor_workspace_router)
_SAFE_AUTO_PROMOTE_OPERATIONS = {"revise", "replace", "transform", "style"}


@router.get("/daw/mixer-ui.js", include_in_schema=False)
def daw_mixer_ui_with_deep_automation():
    base = base_daw_mixer_ui()
    text = base.body.decode("utf-8")
    enhanced = enhance_daw_mixer_javascript(text)
    headers = {key: value for key, value in base.headers.items() if key.lower() not in {"content-length", "content-type", "cache-control"}}
    headers["Cache-Control"] = "private, no-store"
    return Response(enhanced, media_type="application/javascript", headers=headers)


def auto_promote_single_target_revision(store: CreativeProjectStore, directive: CreativeDirective, imported_elements: list[dict], *, target_was_current: bool) -> dict:
    result = {"promoted": False, "reason": "manual_selection_required", "element_id": None, "version_family": None}
    if directive.operation not in _SAFE_AUTO_PROMOTE_OPERATIONS:
        result["reason"] = "operation_not_revision_like"; return result
    if len(directive.target_element_ids) != 1:
        result["reason"] = "target_is_ambiguous"; return result
    if not target_was_current:
        result["reason"] = "target_was_not_current"; return result
    if len(imported_elements) != 1:
        result["reason"] = "multiple_or_missing_outputs"; return result
    element_id = str(imported_elements[0].get("id") or "")
    if not element_id:
        result["reason"] = "imported_element_missing_id"; return result
    manifest = store.load()
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None:
        result["reason"] = "imported_element_not_found"; return result
    target_id = directive.target_element_ids[0]
    if target_id not in element.parent_ids:
        result["reason"] = "output_lineage_does_not_match_target"; return result
    promoted = store.activate_element_version(element_id)
    family = store.version_family(element_id)
    result.update({"promoted": True, "reason": "single_target_single_output_revision", "element_id": element_id, "version_family": family, "active_element_ids": list(promoted.active_element_ids), "previous_media_retained": True})
    return result


@router.post("/creative/projects/{project_name}/directives/{directive_id}/render")
def queue_render_with_resource_governance(
    project_name: str,
    directive_id: str,
    body: QueueRendererRequest,
    request: Request,
):
    member = getattr(request.state, "member", None)
    user_id = str(getattr(member, "user_id", "") or "")
    plan = getattr(member, "plan", None)
    plan_id = str(getattr(plan, "id", "") or "")
    if not user_id or not plan_id:
        raise HTTPException(401, "Authenticated member identity and plan are required")

    try:
        project = project_path(project_name, must_exist=True)
        manifest = CreativeProjectStore(project).load()
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    if directive is None:
        raise HTTPException(404, "Aura directive not found")
    if directive.target_kind not in {"image", "video"}:
        return base_queue_creative_render(project_name, directive_id, body, request)

    try:
        reservation = creative_render_resource_store.reserve(
            user_id=user_id,
            plan_id=plan_id,
            project_name=project_name,
            directive_id=directive_id,
            media_kind=directive.target_kind,
            width=body.width,
            height=body.height,
            frames=body.frames,
        )
    except PermissionError as exc:
        raise HTTPException(429, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, "Creative renderer resource policy is misconfigured") from exc

    try:
        response = base_queue_creative_render(project_name, directive_id, body, request)
    except Exception:
        creative_render_resource_store.cancel(reservation["reservation_id"], user_id=user_id)
        raise
    if isinstance(response, dict):
        response["resource_governance"] = reservation
    return response


@router.post("/creative/projects/{project_name}/directives/{directive_id}/sync-outputs")
def sync_outputs_with_safe_version_promotion(project_name: str, directive_id: str, request: Request):
    project = project_path(project_name, must_exist=True)
    store = CreativeProjectStore(project)
    before = store.load()
    directive = next((item for item in before.directives if item.id == directive_id), None)
    target_was_current = bool(directive and len(directive.target_element_ids) == 1 and directive.target_element_ids[0] in before.active_element_ids)
    response = base_sync_creative_outputs(project_name, directive_id, request)
    if not isinstance(response, dict) or directive is None:
        return response
    imported = list(response.get("imported_elements") or [])
    promotion = auto_promote_single_target_revision(store, directive, imported, target_was_current=target_was_current)
    response["version_promotion"] = promotion
    response["detail"] = "Single targeted revision promoted to CURRENT; previous media remains available in History." if promotion["promoted"] else "Outputs imported. CURRENT was not changed automatically because manual selection is safer for this result set."
    return response


__all__ = ["router", "auto_promote_single_target_revision", "queue_render_with_resource_governance"]
