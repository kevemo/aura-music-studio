from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .creative_catalogue import get_catalogue_item, search_catalogue
from .creative_effect_entitlements import PUBLIC_COIN_UNIT, store as effect_entitlement_store
from .membership_api import require_admin
from .studio_catalogue_menus import EFFECT_BANDS, STUDIO_MENUS, public_studio_catalogue

router = APIRouter(
    prefix="/command-center/api/universal-library",
    tags=["Universal Creative Library"],
)

_ALLOWED_BANDS = {band.id: band.coin_price for band in EFFECT_BANDS}
if _ALLOWED_BANDS != {"core": 0, "silver": 200, "gold": 500}:  # pragma: no cover
    raise RuntimeError("Universal effect-band prices drifted from the canonical catalogue contract")

_EXECUTABLE_RUNTIME_KINDS = frozenset({"ffmpeg_audio"})
_EXECUTABLE_STATUSES = frozenset(
    {
        "BACKEND_FUNCTIONAL",
        "UI_FUNCTIONAL",
        "WORKFLOW_FUNCTIONAL",
        "INTEGRATED",
        "TESTED",
        "RELEASE_CANDIDATE",
        "PRODUCTION_VERIFIED",
    }
)


class RuntimePreviewRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)


class EffectPurchaseRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=120)


class EffectRefundRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    reference: str = Field(min_length=1, max_length=180)
    reason: str = Field(default="Effect purchase refund", min_length=2, max_length=240)


def _require_member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    plan = getattr(member, "plan", None)
    plan_id = getattr(plan, "id", None)
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise HTTPException(401, "Membership plan context unavailable")
    return member


def _member_plan_id(member) -> str:
    return member.plan.id


def _member_user_id(member) -> str:
    user_id = str(getattr(member, "user_id", "") or "").strip()
    if not user_id:
        raise HTTPException(401, "Active member account required")
    return user_id


def _validate_studio(studio: str | None) -> str | None:
    selected = (studio or "").strip().casefold()
    if selected and selected not in STUDIO_MENUS:
        raise HTTPException(400, "Unknown creative studio")
    return selected or None


def _validate_band(entitlement: str | None) -> Literal["core", "silver", "gold"] | None:
    selected = (entitlement or "").strip().casefold()
    if not selected:
        return None
    if selected not in _ALLOWED_BANDS:
        raise HTTPException(400, "entitlement must be core, silver or gold")
    return selected  # type: ignore[return-value]


def _item_backend_executable(item) -> bool:
    """Require both a deliberately allowlisted runtime and functional lifecycle evidence."""
    return bool(
        str(getattr(item, "runtime", "")) in _EXECUTABLE_RUNTIME_KINDS
        and str(getattr(item, "status", "")) in _EXECUTABLE_STATUSES
    )


def _runtime_row(item, *, owned: bool | None = None) -> dict:
    row = item.public()
    expected_price = _ALLOWED_BANDS[item.entitlement]
    if item.ccc_price != expected_price:  # pragma: no cover
        raise RuntimeError(f"Effect Coin price drift for {item.id}")
    executable = _item_backend_executable(item)
    row.update(
        {
            "backend_executable": executable,
            "preview_compile_available": executable,
            "execution_truth_contract": "allowlisted_runtime_and_lifecycle_status_v1",
            "entitlement_price_authoritative": True,
            "coin_unit": PUBLIC_COIN_UNIT,
        }
    )
    if owned is not None:
        row["owned"] = bool(owned)
    return row


@router.get("/menus")
def universal_studio_menus(request: Request, domain: str | None = None):
    member = _require_member(request)
    try:
        payload = public_studio_catalogue(domain=domain)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload["plan"] = _member_plan_id(member)
    payload["catalogue_contract"] = "original_first_party"
    payload["coin_unit"] = PUBLIC_COIN_UNIT
    return payload


@router.get("/runtime-effects")
def universal_runtime_effects(request: Request, q: str = "", studio: str | None = None, entitlement: str | None = None):
    member = _require_member(request)
    user_id = _member_user_id(member)
    selected_studio = _validate_studio(studio)
    selected_band = _validate_band(entitlement)
    purchased_ids = {row["effect_id"] for row in effect_entitlement_store.list_owned(user_id)}
    items = search_catalogue(q.strip(), studio=selected_studio, entitlement=selected_band)
    rows = [_runtime_row(item, owned=item.entitlement == "core" or item.id in purchased_ids) for item in items]
    return {
        "items": rows,
        "count": len(rows),
        "backend_executable_count": sum(1 for row in rows if row["backend_executable"]),
        "owned_count": sum(1 for row in rows if row["owned"]),
        "plan": _member_plan_id(member),
        "query": q.strip(),
        "studio": selected_studio,
        "entitlement": selected_band,
        "effect_bands": [band.public() for band in EFFECT_BANDS],
        "coin_unit": PUBLIC_COIN_UNIT,
        "purchase_entitlement_separate_from_subscription": True,
        "owned_state_included": True,
        "individual_purchase_scope": "permanent_account_unlock",
        "execution_truth_contract": "allowlisted_runtime_and_lifecycle_status_v1",
    }


@router.get("/runtime-effects/owned")
def universal_owned_runtime_effects(request: Request):
    member = _require_member(request)
    user_id = _member_user_id(member)
    purchased_ids = {row["effect_id"] for row in effect_entitlement_store.list_owned(user_id)}
    premium_items = [item for item in search_catalogue("") if item.id in purchased_ids]
    return {
        "items": [_runtime_row(item, owned=True) for item in premium_items],
        "count": len(premium_items),
        "coin_unit": PUBLIC_COIN_UNIT,
        "core_effects_implicitly_included": True,
        "individual_purchase_scope": "permanent_account_unlock",
        "execution_truth_contract": "allowlisted_runtime_and_lifecycle_status_v1",
    }


@router.get("/runtime-effects/{item_id:path}/entitlement")
def universal_runtime_effect_entitlement(item_id: str, request: Request):
    member = _require_member(request)
    user_id = _member_user_id(member)
    try:
        entitlement = effect_entitlement_store.has_entitlement(user_id, item_id)
    except ValueError as exc:
        if "not found" in str(exc).casefold():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc
    return {"entitlement": entitlement, "coin_unit": PUBLIC_COIN_UNIT, "individual_purchase_scope": "permanent_account_unlock"}


@router.post("/runtime-effects/{item_id:path}/purchase")
def universal_runtime_effect_purchase(item_id: str, body: EffectPurchaseRequest, request: Request):
    member = _require_member(request)
    user_id = _member_user_id(member)
    try:
        result = effect_entitlement_store.purchase(user_id, item_id, idempotency_key=body.idempotency_key, actor="member")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.casefold():
            raise HTTPException(404, message) from exc
        if "insufficient" in message.casefold():
            raise HTTPException(402, message) from exc
        raise HTTPException(400, message) from exc
    return {**result, "coin_unit": PUBLIC_COIN_UNIT, "individual_purchase_scope": "permanent_account_unlock", "subscription_changed": False}


@router.post("/admin/runtime-effects/{item_id:path}/refund")
def universal_runtime_effect_refund(item_id: str, body: EffectRefundRequest, x_lss_admin_key: str | None = Header(default=None)):
    require_admin(x_lss_admin_key)
    try:
        result = effect_entitlement_store.refund_and_revoke(body.user_id, item_id, reference=body.reference, reason=body.reason, actor="admin_key")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.casefold():
            raise HTTPException(404, message) from exc
        raise HTTPException(400, message) from exc
    return {**result, "coin_unit": PUBLIC_COIN_UNIT, "subscription_changed": False}


@router.get("/runtime-effects/{item_id:path}")
def universal_runtime_effect_item(item_id: str, request: Request):
    member = _require_member(request)
    user_id = _member_user_id(member)
    try:
        item = get_catalogue_item(item_id)
        entitlement = effect_entitlement_store.has_entitlement(user_id, item_id)
    except KeyError as exc:
        raise HTTPException(404, "Creative catalogue item not found") from exc
    except ValueError as exc:
        if "not found" in str(exc).casefold():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc
    return {"item": _runtime_row(item, owned=bool(entitlement["owned"])), "entitlement": entitlement, "plan": _member_plan_id(member), "coin_unit": PUBLIC_COIN_UNIT}


@router.post("/runtime-effects/{item_id:path}/preview-plan")
def universal_runtime_effect_preview_plan(item_id: str, body: RuntimePreviewRequest, request: Request):
    """Compile a bounded preview plan without granting premium apply authority."""
    member = _require_member(request)
    user_id = _member_user_id(member)
    try:
        item = get_catalogue_item(item_id)
        if not _item_backend_executable(item):
            raise HTTPException(409, "Creative catalogue item has no allowlisted executable runtime")
        effect = item.build_effect(body.parameters, enabled=body.enabled, mix=body.mix)
        chain = item.preview_filter_chain(body.parameters) if body.enabled else ""
        entitlement = effect_entitlement_store.has_entitlement(user_id, item_id)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(404, "Creative catalogue item not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "item_id": item.id,
        "runtime": item.runtime,
        "renderer_effect": {"type": effect.type, "enabled": effect.enabled, "mix": effect.mix, "parameters": effect.parameters},
        "filter_chain": chain,
        "project_media_mutated": False,
        "backend_executable": True,
        "execution_truth_contract": "allowlisted_runtime_and_lifecycle_status_v1",
        "owned": bool(entitlement["owned"]),
        "coin_unit": PUBLIC_COIN_UNIT,
        "preview_does_not_grant_apply_access": True,
    }


def _route_signature_exists(app, path: str, method: str) -> bool:
    expected_method = method.upper()
    for existing in app.router.routes:
        if getattr(existing, "path", None) != path:
            continue
        methods = getattr(existing, "methods", None) or set()
        if expected_method in methods:
            return True
    return False


def install_universal_creative_routes(app) -> None:
    """Bind specific Universal Library handlers before the legacy catch-all."""
    from .aura_effect_system_api import effect_system_route_registrations
    from .aura_effect_system_extended_api import effect_system_extended_route_registrations
    from .universal_creative_library import universal_library, universal_library_item

    prefix = "/command-center/api/universal-library"
    registrations = (
        (prefix, universal_library, "GET"),
        (f"{prefix}/menus", universal_studio_menus, "GET"),
        (f"{prefix}/runtime-effects", universal_runtime_effects, "GET"),
        (f"{prefix}/runtime-effects/owned", universal_owned_runtime_effects, "GET"),
        (f"{prefix}/runtime-effects/{{item_id:path}}/entitlement", universal_runtime_effect_entitlement, "GET"),
        (f"{prefix}/runtime-effects/{{item_id:path}}/purchase", universal_runtime_effect_purchase, "POST"),
        (f"{prefix}/admin/runtime-effects/{{item_id:path}}/refund", universal_runtime_effect_refund, "POST"),
        (f"{prefix}/runtime-effects/{{item_id:path}}/preview-plan", universal_runtime_effect_preview_plan, "POST"),
        (f"{prefix}/runtime-effects/{{item_id:path}}", universal_runtime_effect_item, "GET"),
        *effect_system_route_registrations(prefix),
        *effect_system_extended_route_registrations(prefix),
        (f"{prefix}/{{item_id:path}}", universal_library_item, "GET"),
    )
    for path, endpoint, method in registrations:
        if _route_signature_exists(app, path, method):
            continue
        app.add_api_route(path, endpoint, methods=[method], tags=["Universal Creative Library"])


from .api import app as _canonical_app

install_universal_creative_routes(_canonical_app)


__all__ = [
    "EffectPurchaseRequest",
    "EffectRefundRequest",
    "RuntimePreviewRequest",
    "install_universal_creative_routes",
    "router",
    "universal_owned_runtime_effects",
    "universal_runtime_effect_entitlement",
    "universal_runtime_effect_item",
    "universal_runtime_effect_purchase",
    "universal_runtime_effect_preview_plan",
    "universal_runtime_effect_refund",
    "universal_runtime_effects",
    "universal_studio_menus",
]
