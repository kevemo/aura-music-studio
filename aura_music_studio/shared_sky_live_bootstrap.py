from __future__ import annotations

from typing import Any

from . import access_control
from .shared_sky_live_battle_bridge import (
    install_chat6_battle_viewer_bridge,
    router as live_battle_bridge_router,
)
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
from .shared_sky_live_moderator_permissions import (
    install_shared_sky_moderator_permissions,
    router as live_moderator_permissions_router,
)
from .shared_sky_live_watch_bridge_guard import router as live_watch_bridge_guard_router
from .shared_sky_live_watch_ui_v2 import router as live_watch_v2_router
from .shared_sky_transport_browser_bridge import install_chat2_browser_playback_bridge


PUBLIC_LIVE_PREFIXES = ("/watch/", "/live-events/", "/shared-sky/live/api/")

# The production application has a late compatibility-composition layer. Keep immutable snapshots
# of the Chat 4 APIRoutes before any application consumes/includes the source routers. This makes
# repeated installation and isolated-app tests deterministic even if a framework/composition step
# later mutates or replaces a router's live ``routes`` collection.
_LIVE_WATCH_BRIDGE_GUARD_ROUTES = tuple(live_watch_bridge_guard_router.routes)
_LIVE_WATCH_V2_ROUTES = tuple(live_watch_v2_router.routes)
_LIVE_COMMUNITY_ROUTES = tuple(live_community_router.routes)
_LIVE_CONTROL_ROUTES = tuple(live_controls_router.routes)
_LIVE_EVENT_ROUTES = tuple(live_events_router.routes)
_LIVE_EVENT_UI_ROUTES = tuple(live_events_ui_router.routes)
_LIVE_INTEGRATION_ROUTES = tuple(live_integrations_router.routes)
_LIVE_BATTLE_BRIDGE_ROUTES = tuple(live_battle_bridge_router.routes)
_LIVE_MODERATOR_PERMISSION_ROUTES = tuple(live_moderator_permissions_router.routes)


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

    Chat 2 owns the signed first-party HLS bootstrap, bearer minting, HttpOnly cookie and redirect.
    Chat 4's Wave 4 Watch guard is mounted before the Wave 2 page and delegates all viewer UI back to
    Wave 2. It intervenes only when the canonical descriptor declares
    ``cookie_bootstrap_redirect``: the bootstrap URL is not eagerly assigned to native video, and
    it is treated as HLS for browser-capability gating so unsupported browsers do not falsely claim
    playback readiness. No second playback/token authority is created.

    Neighbour contracts are registered at application composition time. Chat 2 playback is first
    hardened for a generic native-video runtime, then the Chat 2-owned browser bridge is installed
    when its signed cookie-bootstrap contract is available. Chat 6 viewer Battle state remains
    fail-closed until Chat 6 publishes an explicit ``viewer_live_battle(live_session_id)`` lookup;
    Chat 4 never discovers Battles by reading Chat 6 private tables.

    LIVE moderation is an independent permission dimension. Owner and the LIVE creator retain their
    own authority; any other moderator must have both a current Owner-enabled global Moderator grant
    and an explicit assignment to that LIVE. Agent status by itself grants no moderation action.

    Chat 4 Wave 3 installs additive durability hardening before requests are served. LIVE follower
    notifications are retry-safe and poll votes have one serialized receipt per poll/viewer.

    Upcoming-event routes expose only creator-published schedule sidecars. The underlying private
    `shared_sky_schedules` table is never made public by membership-middleware configuration alone.
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
    install_chat2_browser_playback_bridge()
    install_chat6_battle_viewer_bridge()
    install_shared_sky_moderator_permissions()
    install_live_community_hardening()

    existing = {_route_signature(route) for route in app.router.routes}
    for route in (
        *_LIVE_WATCH_BRIDGE_GUARD_ROUTES,
        *_LIVE_WATCH_V2_ROUTES,
        *_LIVE_COMMUNITY_ROUTES,
        *_LIVE_CONTROL_ROUTES,
        *_LIVE_EVENT_ROUTES,
        *_LIVE_EVENT_UI_ROUTES,
        *_LIVE_INTEGRATION_ROUTES,
        *_LIVE_BATTLE_BRIDGE_ROUTES,
        *_LIVE_MODERATOR_PERMISSION_ROUTES,
    ):
        signature = _route_signature(route)
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = ["install_shared_sky_live_community", "PUBLIC_LIVE_PREFIXES"]
