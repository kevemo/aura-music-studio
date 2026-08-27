from __future__ import annotations

from fastapi import APIRouter, Request

from .commercial_entitlement_routes import router as commercial_entitlement_router
from .creative_project import CreativeDirective, CreativeProjectStore
from .creative_project_api import sync_creative_outputs as base_sync_creative_outputs
from .deep_daw_automation_api import router as deep_daw_automation_router
from .global_compliance import router as global_compliance_router
from .owner_finance import router as owner_finance_router
from .professional_editor_api import router as professional_editor_router
from .professional_editor_inspector_overlay import router as professional_editor_workspace_router
from .professional_editor_lifecycle_api import router as professional_editor_lifecycle_router
from .professional_editor_render_api import router as professional_editor_render_router
from .stripe_billing import router as stripe_billing_router
from .stripe_billing_hardening import router as stripe_billing_hardening_router
from .stripe_commerce_receipts import router as stripe_commerce_receipts_router
from .tenant_storage import project_path

router = APIRouter(tags=["Creative Version Promotion"])
# Commercial entitlements, compliance, Stripe billing, protected owner finance reporting and the
# professional non-destructive editor are nested here deliberately because this overlay router is
# already mounted by app.py before the underlying Creative handlers. Each subsystem keeps its own
# membership/owner/role/security checks without another high-conflict application-entrypoint edit.
router.include_router(commercial_entitlement_router)
router.include_router(global_compliance_router)
# Commerce receipt persistence owns the public webhook path and delegates first to the hardened
# renewal preflight, which in turn delegates to the base idempotent Stripe processor. Starlette
# dispatches the first matching route, so this ordering keeps access/credit mutation authoritative
# while making verified top-up revenue auditable.
router.include_router(stripe_commerce_receipts_router)
router.include_router(stripe_billing_hardening_router)
router.include_router(stripe_billing_router)
router.include_router(owner_finance_router)
router.include_router(deep_daw_automation_router)
router.include_router(professional_editor_router)
router.include_router(professional_editor_lifecycle_router)
router.include_router(professional_editor_render_router)
# The inspector overlay delegates to the existing authenticated workspace function, then injects
# the Pro mask/effect/keyframe/export controls. The original workspace router is intentionally not
# mounted separately so there is only one /creative/editor-workspace route.
router.include_router(professional_editor_workspace_router)
_SAFE_AUTO_PROMOTE_OPERATIONS = {"revise", "replace", "transform", "style"}


def auto_promote_single_target_revision(
    store: CreativeProjectStore,
    directive: CreativeDirective,
    imported_elements: list[dict],
    *,
    target_was_current: bool,
) -> dict:
    """Promote only an unambiguous one-target/one-output revision.

    Multiple alternatives deliberately remain candidates. No media is deleted and the
    previous element stays in the version family/history.
    """
    result = {
        "promoted": False,
        "reason": "manual_selection_required",
        "element_id": None,
        "version_family": None,
    }
    if directive.operation not in _SAFE_AUTO_PROMOTE_OPERATIONS:
        result["reason"] = "operation_not_revision_like"
        return result
    if len(directive.target_element_ids) != 1:
        result["reason"] = "target_is_ambiguous"
        return result
    if not target_was_current:
        result["reason"] = "target_was_not_current"
        return result
    if len(imported_elements) != 1:
        result["reason"] = "multiple_or_missing_outputs"
        return result
    element_id = str(imported_elements[0].get("id") or "")
    if not element_id:
        result["reason"] = "imported_element_missing_id"
        return result
    manifest = store.load()
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None:
        result["reason"] = "imported_element_not_found"
        return result
    target_id = directive.target_element_ids[0]
    if target_id not in element.parent_ids:
        result["reason"] = "output_lineage_does_not_match_target"
        return result
    promoted = store.activate_element_version(element_id)
    family = store.version_family(element_id)
    result.update({
        "promoted": True,
        "reason": "single_target_single_output_revision",
        "element_id": element_id,
        "version_family": family,
        "active_element_ids": list(promoted.active_element_ids),
        "previous_media_retained": True,
    })
    return result


@router.post("/creative/projects/{project_name}/directives/{directive_id}/sync-outputs")
def sync_outputs_with_safe_version_promotion(project_name: str, directive_id: str, request: Request):
    project = project_path(project_name, must_exist=True)
    store = CreativeProjectStore(project)
    before = store.load()
    directive = next((item for item in before.directives if item.id == directive_id), None)
    target_was_current = bool(
        directive
        and len(directive.target_element_ids) == 1
        and directive.target_element_ids[0] in before.active_element_ids
    )

    response = base_sync_creative_outputs(project_name, directive_id, request)
    if not isinstance(response, dict) or directive is None:
        return response
    imported = list(response.get("imported_elements") or [])
    promotion = auto_promote_single_target_revision(
        store,
        directive,
        imported,
        target_was_current=target_was_current,
    )
    response["version_promotion"] = promotion
    response["detail"] = (
        "Single targeted revision promoted to CURRENT; previous media remains available in History."
        if promotion["promoted"]
        else "Outputs imported. CURRENT was not changed automatically because manual selection is safer for this result set."
    )
    return response


__all__ = ["router", "auto_promote_single_target_revision"]
