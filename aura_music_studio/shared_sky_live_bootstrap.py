from __future__ import annotations

from typing import Any

from . import access_control
from .shared_sky_live_browser_playback import harden_browser_playback_integration
from .shared_sky_live_community import router as live_community_router
from .shared_sky_live_controls import router as live_controls_router
from .shared_sky_live_events import router as live_events_router
from .shared_sky_live_events_ui import router as live_events_ui_router
from .shared_sky_live_hardening import install_live_community_hardening
from .shared_sky_live_integrations import (
    configure_neighbor_live_integrations,
    router as live_integrations_router,
)
from .shared_sky_live_neighbor_wave4 import (
    configure_wave4_neighbor_adapters,
    router as live_neighbor_wave4_router,
)
from .shared_sky_live_watch_ui_v2 import router as live_watch_v2_router
from .shared_sky_live_watch_ui_v4 import router as live_watch_v4_router


PUBLIC_LIVE_PREFIXES = ("/watch/", "/live-events/", "/shared-sky/live/api/")

# The production application has a late compatibility-composition layer. Keep immutable snapshots
# of the Chat 4 APIRoutes before any application consumes/includes the source routers. This makes
# repeated installation and isolated-app tests deterministic even if a framework/composition step
# later mutates or replaces a router's live ``routes`` collection.
_LIVE_WATCH_V4_ROUTES = tuple(live_watch_v4_router.routes)
_LIVE_WATCH_V2_ROUTES = tuple(live_watch_v2_router.routes)
_LIVE_COMMUNITY_ROUTES = tuple(live_community_router.routes)
_LIVE_CONTROL_ROUTES = tuple(live_controls_router.routes)
_LIVE_EVENT_ROUTES = tuple(live_events_router.routes)
_LIVE_EVENT_UI_ROUTES = tuple(live_events_ui_router.routes)
_LIVE_NEIGHBOR_WAVE4_ROUTES = tuple(live_neighbor_wave4_router.routes)
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

    Wave 4 Watch routes are installed first. Route-signature deduplication therefore makes the
    Wave 4 wrapper the canonical ``GET /watch/{broadcast_id}`` surface while retaining the validated
    Wave 2 page as the underlying UI/state implementation and the legacy route as compatibility
    code. Wave 4 only adds neighbour integration; it does not fork the player state machine.

    Neighbour contracts are registered at application composition time. The merged Chat 2 transport
    is consumed through its canonical playback API, but browser playback stays fail-closed unless
    the runtime descriptor explicitly advertises the secure POST cookie-exchange contract. The
    short-lived media bearer is exchanged server-side; Chat 4 never places it in HTML, JSON returned
    to the player, URLs or browser storage. Same-origin media is required for the scoped cookie path.

    Chat 6 Battle display registration is equally fail-closed. Chat 4 registers a read-only Battle
    adapter only when Chat 6 exposes an explicit ``viewer_live_battle(live_session_id)`` helper.
    Chat 4 never discovers Battle IDs by querying Chat 6 private tables and never calculates scores.

    Chat 4 Wave 3 installs additive durability hardening before requests are served. LIVE follower
    notifications are retry-safe after delivery failures, and poll votes use one serialized receipt
    per poll/viewer so competing requests cannot create multiple final choices. Historical
    emission/vote rows are migrated without inventing new events.

    Upcoming-event routes expose only creator-published schedule sidecars. The underlying private
    ``shared_sky_schedules`` table is never made public by membership-middleware configuration alone;
    publication/access checks remain server authoritative inside the event handlers. The public
    ``/live-events`` viewer surface uses the same access decisions and never reads private schedules
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
    install_live_community_hardening()
    configure_wave4_neighbor_adapters()

    existing = {_route_signature(route) for route in app.router.routes}
    for route in (
        *_LIVE_WATCH_V4_ROUTES,
        *_LIVE_WATCH_V2_ROUTES,
        *_LIVE_COMMUNITY_ROUTES,
        *_LIVE_CONTROL_ROUTES,
        *_LIVE_EVENT_ROUTES,
        *_LIVE_EVENT_UI_ROUTES,
        *_LIVE_NEIGHBOR_WAVE4_ROUTES,
        *_LIVE_INTEGRATION_ROUTES,
    ):
        signature = _route_signature(route)
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = ["install_shared_sky_live_community", "PUBLIC_LIVE_PREFIXES"]
