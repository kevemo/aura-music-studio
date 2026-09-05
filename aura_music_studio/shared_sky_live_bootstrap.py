from __future__ import annotations

from typing import Any

from . import access_control
from .shared_sky_live_community import router as live_community_router
from .shared_sky_live_controls import router as live_controls_router


PUBLIC_LIVE_PREFIXES = ("/watch/", "/shared-sky/live/api/")


def install_shared_sky_live_community(app: Any) -> None:
    """Mount Chat 4 viewer routes and preserve optional-auth public-watch semantics.

    MembershipAccessMiddleware reads its module-level public route registry at request time, so
    registering these paths here allows anonymous discovery/watch while every state-changing
    community handler still performs its own canonical optional/required member resolution.
    StudioSecurityMiddleware and CrossSiteRequestGuardMiddleware remain in force globally.
    """

    access_control.PUBLIC_EXACT.add("/live-now")
    prefixes = tuple(access_control.PUBLIC_PREFIXES)
    for prefix in PUBLIC_LIVE_PREFIXES:
        if prefix not in prefixes:
            prefixes += (prefix,)
    access_control.PUBLIC_PREFIXES = prefixes

    existing = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
    }
    for candidate in (live_community_router, live_controls_router):
        candidate_routes = list(candidate.routes)
        if not all(
            (
                getattr(route, "path", ""),
                tuple(sorted(getattr(route, "methods", set()) or set())),
            ) in existing
            for route in candidate_routes
        ):
            app.include_router(candidate)
            existing.update(
                (
                    getattr(route, "path", ""),
                    tuple(sorted(getattr(route, "methods", set()) or set())),
                )
                for route in candidate_routes
            )


__all__ = ["install_shared_sky_live_community", "PUBLIC_LIVE_PREFIXES"]
