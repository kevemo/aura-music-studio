from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .creative_catalogue import get_catalogue_item, search_catalogue
from .studio_catalogue_menus import EFFECT_BANDS, STUDIO_MENUS, public_studio_catalogue

router = APIRouter(
    prefix="/command-center/api/universal-library",
    tags=["Universal Creative Library"],
)

_ALLOWED_BANDS = {band.id: band.coin_price for band in EFFECT_BANDS}
if _ALLOWED_BANDS != {"core": 0, "silver": 200, "gold": 500}:  # pragma: no cover
    raise RuntimeError("Universal effect-band prices drifted from the canonical catalogue contract")


class RuntimePreviewRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)


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
    # _require_member validates this shape before any response exposes plan context.
    return member.plan.id


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


def _runtime_row(item) -> dict:
    row = item.public()
    expected_price = _ALLOWED_BANDS[item.entitlement]
    if item.ccc_price != expected_price:  # pragma: no cover - import/runtime integrity boundary
        raise RuntimeError(f"Effect Coin price drift for {item.id}")
    row.update(
        {
            "backend_executable": item.status in {
                "BACKEND_FUNCTIONAL",
                "UI_FUNCTIONAL",
                "WORKFLOW_FUNCTIONAL",
                "INTEGRATED",
                "TESTED",
                "RELEASE_CANDIDATE",
                "PRODUCTION_VERIFIED",
            },
            "preview_compile_available": item.runtime == "ffmpeg_audio",
            "entitlement_price_authoritative": True,
        }
    )
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
    return payload


@router.get("/runtime-effects")
def universal_runtime_effects(
    request: Request,
    q: str = "",
    studio: str | None = None,
    entitlement: str | None = None,
):
    member = _require_member(request)
    selected_studio = _validate_studio(studio)
    selected_band = _validate_band(entitlement)
    rows = [
        _runtime_row(item)
        for item in search_catalogue(q.strip(), studio=selected_studio, entitlement=selected_band)
    ]
    return {
        "items": rows,
        "count": len(rows),
        "backend_executable_count": sum(1 for row in rows if row["backend_executable"]),
        "plan": _member_plan_id(member),
        "query": q.strip(),
        "studio": selected_studio,
        "entitlement": selected_band,
        "effect_bands": [band.public() for band in EFFECT_BANDS],
        "purchase_entitlement_separate_from_subscription": True,
        "owned_state_included": False,
        "owned_state_reason": "Account purchase entitlements are resolved by the server-authoritative Coin ledger in a separate commercial slice.",
    }


@router.get("/runtime-effects/{item_id:path}")
def universal_runtime_effect_item(item_id: str, request: Request):
    member = _require_member(request)
    try:
        item = get_catalogue_item(item_id)
    except KeyError as exc:
        raise HTTPException(404, "Creative catalogue item not found") from exc
    return {"item": _runtime_row(item), "plan": _member_plan_id(member)}


@router.post("/runtime-effects/{item_id:path}/preview-plan")
def universal_runtime_effect_preview_plan(item_id: str, body: RuntimePreviewRequest, request: Request):
    """Compile bounded parameters into the real renderer plan without mutating project media.

    This endpoint proves that a catalogue item maps to an executable renderer primitive. The
    actual project apply/render workflow remains separately authorised by project and entitlement
    boundaries and is not bypassed here.
    """
    _require_member(request)
    try:
        item = get_catalogue_item(item_id)
        effect = item.build_effect(body.parameters, enabled=body.enabled, mix=body.mix)
        chain = item.preview_filter_chain(body.parameters) if body.enabled else ""
    except KeyError as exc:
        raise HTTPException(404, "Creative catalogue item not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "item_id": item.id,
        "runtime": item.runtime,
        "renderer_effect": {
            "type": effect.type,
            "enabled": effect.enabled,
            "mix": effect.mix,
            "parameters": effect.parameters,
        },
        "filter_chain": chain,
        "project_media_mutated": False,
        "backend_executable": True,
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
    """Bind Universal Library handlers directly to the shared production app.

    The Command Center composes many late overlay routers. A late ``include_router`` can be
    ineffective after those compatibility installers have copied/wrapped the original router.
    Register these handlers on the shared FastAPI instance itself so runtime reachability is
    deterministic. The handlers retain their own membership validation and the surrounding app
    middleware remains authoritative. The legacy catch-all is deliberately registered last.
    """
    from .universal_creative_library import universal_library, universal_library_item

    prefix = "/command-center/api/universal-library"
    registrations = (
        (prefix, universal_library, "GET"),
        (f"{prefix}/menus", universal_studio_menus, "GET"),
        (f"{prefix}/runtime-effects", universal_runtime_effects, "GET"),
        (f"{prefix}/runtime-effects/{{item_id:path}}", universal_runtime_effect_item, "GET"),
        (f"{prefix}/runtime-effects/{{item_id:path}}/preview-plan", universal_runtime_effect_preview_plan, "POST"),
        (f"{prefix}/{{item_id:path}}", universal_library_item, "GET"),
    )
    for path, endpoint, method in registrations:
        if _route_signature_exists(app, path, method):
            continue
        app.add_api_route(path, endpoint, methods=[method], tags=["Universal Creative Library"])


# The root production entrypoint imports ``aura_music_studio.api.app`` before importing this
# module. Bind to that same shared FastAPI object here so the routes exist before any later
# compatibility-router copy can make ``include_router`` ineffective. The installer is idempotent.
from .api import app as _canonical_app

install_universal_creative_routes(_canonical_app)


__all__ = [
    "RuntimePreviewRequest",
    "install_universal_creative_routes",
    "router",
    "universal_runtime_effects",
    "universal_runtime_effect_item",
    "universal_runtime_effect_preview_plan",
    "universal_studio_menus",
]
