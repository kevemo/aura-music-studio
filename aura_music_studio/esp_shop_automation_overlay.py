from __future__ import annotations

from fastapi import Request

from . import esp_shop_automation as base
from .esp_command_center import esp
from .esp_niche import require_esp_hub_member


def _store(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"creator", "both", "owner"}:
        # Preserve the base module's normal role rejection semantics.
        base._creator_context(request)
    plan_id = str(member.user.get("plan_id") or "free").lower()
    tier = base.SHOP_TIERS.get(plan_id, base.SHOP_TIERS["free"])
    return base.ShopAutomationStore(esp.db_path), member, plan_id, tier


# Route functions resolve `_store` from their module globals at request time, so this adapter
# installs the canonical ESP database boundary without duplicating the API or HTML surface.
base._store = _store

# Connection status is provider-verified state. The base module keeps the store method for a
# future OAuth callback/worker adapter, but the member-facing router must not let a creator
# self-assert that an external provider is connected.
_PROVIDER_STATUS_PATH = "/command-center/api/shop-automation/connections/{connection_id}"
base.router.routes[:] = [
    route for route in base.router.routes
    if not (
        getattr(route, "path", None) == _PROVIDER_STATUS_PATH
        and "PATCH" in (getattr(route, "methods", set()) or set())
    )
]

router = base.router


__all__ = ["router"]
