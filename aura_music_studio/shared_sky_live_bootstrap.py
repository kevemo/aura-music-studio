from __future__ import annotations

from typing import Any

from . import access_control
from .shared_sky_live_browser_playback import harden_browser_playback_integration
from .shared_sky_live_community import router as live_community_router
from .shared_sky_live_controls import router as live_controls_router
from .shared_sky_live_events import router as live_events_router
from .shared_sky_live_events_ui import router as live_events_ui_router
from .shared_sky_live_integrations import (
    configure_neighbor_live_integrations,
    router as live_integrations_router,
)
from .shared_sky_live_watch_ui_v2 import router as live_watch_v2_router


PUBLIC_LIVE_PREFIXES = ("/watch/", "/live-events/", "/shared-sky/live/api/")

# The production application has a late compatibility-composition layer. Keep immutable snapshots
# of the Chat 4 APIRoutes before any application consumes/includes the source routers. This makes
# repeated installation and isolated-app tests deterministic even if a framework/composition step
# later mutates or replaces a router's live ``routes`` collection.
_LIVE_WATCH_V2_ROUTES = tuple(live_watch_v2_router.routes)
_LIVE_COMMUNITY_ROUTES = tuple(live_community_router.routes)
_LIVE_CONTROL_ROUTES = tuple(live_controls_router.routes)
_LIVE_EVENT_ROUTES = tuple(live_events_router.routes)
_LIVE_EVENT_UI_ROUTES = tuple(live_events_ui_router.routes)
_LIVE_INTEGRATION_ROUTES = tuple(live_integrations_router.routes)


def _route_signature(route: Any) -> tuple[str, tuple[str, ...]]:
    return (
        str(getattr(route, "path", "")),
        tuple(sorted(getattr(route, "methods", set()) or set())),
    )


def install_shared_sky_live_community(app: Any) -> None:
    """Mount Chat 4 viewer routes with optional-auth public-watch semantics.

    MembershipAccessMiddleware reads its module-level public route registry at request time, so
    registering these paths here allows anonymous discovery/watch while every state-changing
    community handler still performs its own canonical optional/required member resolution.
    StudioSecurityMiddleware and CrossSiteRequestGuardMiddleware remain in force globally.

    Routes are appended from import-time immutable APIRoute snapshots rather than relying on a
    late ``include_router`` flattening pass. The repository already uses direct canonical-app route
    binding for late Shared Sky modules; this keeps Chat 4 registration idempotent and deterministic
    for both the production application and isolated FastAPI test applications.

    The Wave 2 Watch routes are installed before the legacy Chat 4 Watch route. Route signatures
    are deduplicated, so the responsive/resilient Wave 2 player becomes the canonical
    ``GET /watch/{broadcast_id}`` surface while the legacy endpoint remains as compatibility code.

    Neighbour contracts are registered at application composition time. If Chat 2/5 modules are not
    merged yet, registration remains fail-closed and the original unavailable adapters stay active.
    Chat 2 playback is then hardened for the actual browser runtime: a descriptor that requires a
    custom Bearer header is not advertised as native-video playable until a browser-safe credential
    mode or a deliberately packaged header-capable HLS runtime exists.

    Upcoming-event routes expose only creator-published schedule sidecars. The underlying private
    `shared_sky_schedules` table is never made public by membership-middleware configuration alone;
    publication/access checks remain server authoritative inside the event handlers. The public
    `/live-events` viewer surface uses the same access decisions and never reads private schedules
    directly.
    """

    access_control.PUBLIC_EXACT.add("/live-now")
    access_control.PUBLIC_EXACT.add("/live-events")
    prefixes = tuple(access_control.PUBLIC_PREFIXES)
    for prefix in PUBLIC_LIVE_PREFIXES:
        if prefix not in prefixes:
            prefixes += (prefix,)
    access_control.PUBLIC_PREFIXES = prefixes

    configure_neighbor_live_integrations()
    harden_browser_playback_integration()

    existing = {_route_signature(route) for route in app.router.routes}
    for route in (
        *_LIVE_WATCH_V2_ROUTES,
        *_LIVE_COMMUNITY_ROUTES,
        *_LIVE_CONTROL_ROUTES,
        *_LIVE_EVENT_ROUTES,
        *_LIVE_EVENT_UI_ROUTES,
        *_LIVE_INTEGRATION_ROUTES,
    ):
        signature = _route_signature(route)
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = ["install_shared_sky_live_community", "PUBLIC_LIVE_PREFIXES"]
