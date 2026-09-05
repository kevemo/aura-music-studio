from __future__ import annotations

from copy import copy
from threading import RLock
from typing import Any

from .cosmic_economy import LiveGiftContext
from .cosmic_economy_integrations import (
    UnavailableLiveSessionDirectory,
    configure_economy_integrations,
    runtime_integrations,
)
from .route_integrity import register_route_composition_hook


_INTEGRATION_STATUS: dict[str, Any] = {
    "state": "pending",
    "reason": "shared_sky_live_adapter_not_available",
}
_BIND_LOCK = RLock()

# The production billing hardening layer has already published these operation-ID prefixes as the
# stable public schema identity for the two historical Creation Coin read paths. Chat 5 owns their
# runtime semantics now, but preserving those hardened IDs avoids breaking generated clients and
# proves that final composition did not silently fall back to the deprecated credit-wallet routes.
_PRODUCTION_COMPAT_UNIQUE_IDS: dict[tuple[str, tuple[str, ...]], str] = {
    ("/billing/creation-coins", ("GET",)): (
        "hardened_creation_coin_storefront_billing_creation_coins_get"
    ),
    ("/billing/creation-coins/catalog", ("GET",)): (
        "hardened_creation_coin_catalog_billing_creation_coins_catalog_get"
    ),
}


def _registered_status(adapter: object, *, source: str | None = None) -> dict[str, Any]:
    adapter_name = type(adapter).__name__
    return {
        "state": "registered",
        "source": source or adapter_name,
        "adapter": adapter_name,
        "runtime_adapter": adapter_name,
    }


def configure_chat5_shared_sky() -> dict[str, Any]:
    """Bind Chat 5 to the authoritative Shared Sky live-session adapter when merged.

    The operation is intentionally idempotent and safe to retry after the full application import
    graph has completed. This avoids permanently degrading the economy if an early module import
    reaches the integration boundary before Shared Sky has finished initialising.

    The adapter owns only broadcast/live-recipient truth. Age/region eligibility, Coin pricing,
    spending, risk, payout and Battle scoring remain separate controls. Missing or broken Shared
    Sky modules leave the existing fail-closed live-session directory in place.
    """

    global _INTEGRATION_STATUS
    with _BIND_LOCK:
        current = runtime_integrations.live_sessions
        if not isinstance(current, UnavailableLiveSessionDirectory):
            _INTEGRATION_STATUS = _registered_status(current)
            return dict(_INTEGRATION_STATUS)

        try:
            from . import shared_sky_live_community as live
            from .shared_sky_live_integrations import SharedSkyGiftLiveSessionDirectory

            adapter = SharedSkyGiftLiveSessionDirectory(LiveGiftContext, live.community)
            configure_economy_integrations(live_sessions=adapter)
            _INTEGRATION_STATUS = _registered_status(
                adapter,
                source="aura_music_studio.shared_sky_live_community.community",
            )
        except (ImportError, ModuleNotFoundError):
            current = runtime_integrations.live_sessions
            if not isinstance(current, UnavailableLiveSessionDirectory):
                _INTEGRATION_STATUS = _registered_status(current)
            else:
                _INTEGRATION_STATUS = {
                    "state": "pending",
                    "reason": "shared_sky_live_adapter_not_available",
                    "runtime_adapter": type(current).__name__,
                }
        except Exception as exc:
            current = runtime_integrations.live_sessions
            if not isinstance(current, UnavailableLiveSessionDirectory):
                _INTEGRATION_STATUS = _registered_status(current)
            else:
                _INTEGRATION_STATUS = {
                    "state": "degraded",
                    "reason": str(getattr(exc, "code", type(exc).__name__))[:120],
                    "runtime_adapter": type(current).__name__,
                }
        return dict(_INTEGRATION_STATUS)


def chat5_shared_sky_status() -> dict[str, Any]:
    """Return live integration truth, retrying only while the canonical seam is unavailable."""

    current = runtime_integrations.live_sessions
    if isinstance(current, UnavailableLiveSessionDirectory):
        return configure_chat5_shared_sky()
    if _INTEGRATION_STATUS.get("state") != "registered":
        return configure_chat5_shared_sky()
    status = dict(_INTEGRATION_STATUS)
    status["runtime_adapter"] = type(current).__name__
    return status


def _http_signature(route: Any) -> tuple[str, tuple[str, ...]] | None:
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    return path, tuple(sorted(str(method).upper() for method in methods))


def _production_compat_route(route: Any) -> Any:
    """Clone a legacy bridge route and retain the hardened public schema identity when required."""

    signature = _http_signature(route)
    unique_id = _PRODUCTION_COMPAT_UNIQUE_IDS.get(signature) if signature is not None else None
    if unique_id is None:
        return route

    cloned = copy(route)
    # Leave operation_id unset so FastAPI uses this stable per-route unique_id. Runtime endpoint,
    # dependencies, response model and compiled ASGI handler remain the canonical Chat 5 bridge.
    cloned.operation_id = None
    cloned.unique_id = unique_id
    return cloned


def _restore_chat5_economy_routes(app: Any) -> None:
    """Reassert Chat 5 route ownership after the production overlay graph is composed.

    The package API mounts these routers normally. Some full-site overlay installers rebuild route
    collections during production composition, so this final hook removes any conflicting exact
    path+method copies and appends the canonical Chat 5 APIRoutes once. This deliberately includes
    the legacy Creation Coin compatibility URLs so they cannot fall back to the older credit-wallet
    checkout implementation.

    The two historical read-only Creation Coin paths retain their previously hardened OpenAPI
    identities, but their endpoint modules and behavior remain Chat 5's canonical compatibility
    bridge. The legacy direct credit checkout remains disabled.
    """

    from .cosmic_economy_api import router as economy_router
    from .cosmic_economy_legacy_bridge import router as legacy_router
    from .cosmic_economy_owner_api import router as owner_router

    source_routers = (economy_router, legacy_router, owner_router)
    claimed = {
        signature
        for source_router in source_routers
        for source_route in source_router.routes
        if (signature := _http_signature(source_route)) is not None
    }
    if claimed:
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if _http_signature(route) not in claimed
        ]

    # Economy and Owner routes have no legacy public schema identity to preserve.
    app.router.routes.extend(economy_router.routes)
    # Compatibility routes keep Chat 5 runtime behavior; only the two established read-path schema
    # IDs are preserved for compatibility with the production billing hardening contract.
    app.router.routes.extend(_production_compat_route(route) for route in legacy_router.routes)
    app.router.routes.extend(owner_router.routes)
    app.openapi_schema = None


register_route_composition_hook("chat5_cosmic_economy", _restore_chat5_economy_routes)
