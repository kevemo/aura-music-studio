"""Compatibility bridge for the consolidated member account surface.

Password reset, recovery UI and session management are owned by
``aura_music_studio.account_security_api``. Billing history is authored by the legacy
member portal because it shares that portal's authenticated HTML presentation, but the
shared API composition imports this compatibility router before it snapshots the portal
router. Move only the two billing-history route objects into the canonical account router
here so they are deterministically reachable from ``aura_music_studio.api`` without
registering duplicate paths or copying handler logic.

The handler functions remain defined in ``web_portal`` for direct reuse/testing. Only
route ownership moves; authentication, account scoping and read-only billing semantics are
unchanged.
"""

from .account_security_api import router
from .web_portal import router as web_portal_router

_BILLING_HISTORY_PATHS = frozenset({
    "/auth/me/billing-history",
    "/auth/billing-history",
})

for _route in tuple(web_portal_router.routes):
    if getattr(_route, "path", None) in _BILLING_HISTORY_PATHS:
        web_portal_router.routes.remove(_route)
        router.routes.append(_route)

__all__ = ["router"]
