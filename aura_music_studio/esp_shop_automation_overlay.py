from __future__ import annotations

from fastapi import APIRouter, Request

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
# provider callback/worker adapter, but the member-facing router must not let a creator
# self-assert that an external provider is connected.
_PROVIDER_STATUS_PATH = "/command-center/api/shop-automation/connections/{connection_id}"
base.router.routes[:] = [
    route for route in base.router.routes
    if not (
        getattr(route, "path", None) == _PROVIDER_STATUS_PATH
        and "PATCH" in (getattr(route, "methods", set()) or set())
    )
]

# Import only after the canonical ESP store and member-facing route boundary are installed.
# The provider runtime reuses the exact same Shop state, approval queue and safety policy.
from . import esp_shop_provider_runtime as runtime
from .esp_shop_async_execution import EXECUTE_PATH, router as async_execution_router
from .esp_shop_provider_callback_security import CALLBACK_PATH, router as callback_security_router

runtime.configure_runtime_db(esp.db_path)

# Replace two generic runtime routes at the exposed composition boundary:
# 1. OAuth callback -> signed provider callback gate.
# 2. provider execute -> async-aware execution/reconciliation state machine.
runtime_routes = [
    route for route in runtime.router.routes
    if not (
        (
            getattr(route, "path", None) == CALLBACK_PATH
            and "GET" in (getattr(route, "methods", set()) or set())
        )
        or (
            getattr(route, "path", None) == EXECUTE_PATH
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    )
]

# Compose the already-constructed route objects directly. This avoids depending on FastAPI's
# include_router copy semantics during module collection while preserving each route's own
# methods, dependencies, response class, tags and endpoint function.
router = APIRouter()
router.routes.extend(list(base.router.routes))
router.routes.extend(runtime_routes)
router.routes.extend(list(callback_security_router.routes))
router.routes.extend(list(async_execution_router.routes))


__all__ = ["router"]
