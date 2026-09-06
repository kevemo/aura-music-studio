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
from .shared_sky_live_moderator_actions import (
    install_limited_moderator_actions,
    router as live_moderator_actions_router,
)
from .shared_sky_live_moderator_permissions import (
    install_shared_sky_moderator_permissions,
    router as live_moderator_permissions_router,
)
from .shared_sky_live_watch_bridge_guard import router as live_watch_bridge_guard_router
from .shared_sky_live_watch_engagement_wave6 import router as live_watch_engagement_wave6_router
from .shared_sky_live_watch_ui_v2 import router as live_watch_v2_router
from .shared_sky_transport_browser_bridge import install_chat2_browser_playback_bridge


PUBLIC_LIVE_PREFIXES = ("/watch/", "/live-events/", "/shared-sky/live/api/")

# The production application has a late compatibility-composition layer. Keep immutable snapshots
# of the Chat 4 APIRoutes before any application consumes/includes the source routers. This makes
# repeated installation and isolated-app tests deterministic even if a framework/composition step
# later mutates or replaces a router's live ``routes`` collection.
_LIVE_WATCH_ENGAGEMENT_WAVE6_ROUTES = tuple(live_watch_engagement_wave6_router.routes)
_LIVE_WATCH_BRIDGE_GUARD_ROUTES = tuple(live_watch_bridge_guard_router.routes)
_LIVE_WATCH_V2_ROUTES = tuple(live_watch_v2_router.routes)
_LIVE_COMMUNITY_ROUTES = tuple(live_community_router.routes)
_LIVE_CONTROL_ROUTES = tuple(live_controls_router.routes)
_LIVE_EVENT_ROUTES = tuple(live_events_router.routes)
_LIVE_EVENT_UI_ROUTES = tuple(live_events_ui_router.routes)
_LIVE_INTEGRATION_ROUTES = tuple(live_integrations_router.routes)
_LIVE_BATTLE_BRIDGE_ROUTES = tuple(live_battle_bridge_router.routes)
_LIVE_MODERATOR_PERMISSION_ROUTES = tuple(live_moderator_permissions_router.routes)
_LIVE_MODERATOR_ACTION_ROUTES = tuple(live_moderator_actions_router.routes)


def _route_signature(route: Any) -> tuple[str, tuple[str, ...]]:
    return (
        str(getattr(route, "path", "")),
        tuple(sorted(getattr(route, "methods", set()) or set())),
    )


def install_shared_sky_live_community(app: Any) -> None:
    """Mount viewer/community routes and bounded Chat 5 LIVE safety extensions.

    MembershipAccessMiddleware reads its module-level public route registry at request time, so
    registering these paths here allows anonymous discovery/watch while every state-changing
    community handler still performs its own canonical optional/required member resolution.
    StudioSecurityMiddleware and CrossSiteRequestGuardMiddleware remain in force globally.

    Chat 2's merged transport remains the canonical first-party HLS/control-plane implementation.
    The Watch layer consumes that truth and does not manufacture media readiness. Gift sending is
    delegated to the authoritative economy route; Battle viewer state is read-only here.

    LIVE moderation is an independent permission dimension. Owner and the LIVE creator retain their
    own authority; any other moderator must have both a current Owner-enabled global Moderator grant
    and an explicit assignment to that LIVE. Agent status by itself grants no moderation action.

    Chat 5 also mounts a purpose-built emergency Programme source-hide command. It operates only on
    the already-committed Studio Programme snapshot, commits through the canonical transport
    adapter, never mutates a wallet/Battle score, and fails closed if transport rejects the change.

    Rhiannon LIVE Guardian is mounted as a read-only/advisory readiness surface that consumes the
    existing moderation authority and never obtains independent moderation or provider-write power.
    Auto Cue is a creator-private browser-local prompter: script text is never posted to or persisted
    by Shared Skies.

    LIVE voice integration consumes Chat 1 Rhiannon capability/provenance contracts and Chat 2 voice
    authority without creating another clone/profile engine. Current readiness distinguishes batch
    speech processing from genuine real-time voice/caption paths, and emergency Programme muting is
    independent of any failing AI voice processor. Tenant-scoped authorised Voice House profile
    discovery is a read-only candidate list only; no server-authoritative LIVE processor binding or
    real-time processing capability is implied by discovery.
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
    install_limited_moderator_actions()
    install_live_community_hardening()

    existing = {_route_signature(route) for route in app.router.routes}
    for route in (
        *_LIVE_WATCH_ENGAGEMENT_WAVE6_ROUTES,
        *_LIVE_WATCH_BRIDGE_GUARD_ROUTES,
        *_LIVE_WATCH_V2_ROUTES,
        *_LIVE_COMMUNITY_ROUTES,
        *_LIVE_CONTROL_ROUTES,
        *_LIVE_EVENT_ROUTES,
        *_LIVE_EVENT_UI_ROUTES,
        *_LIVE_INTEGRATION_ROUTES,
        *_LIVE_BATTLE_BRIDGE_ROUTES,
        *_LIVE_MODERATOR_PERMISSION_ROUTES,
        *_LIVE_MODERATOR_ACTION_ROUTES,
    ):
        signature = _route_signature(route)
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)

    # Import lazily after the canonical application/control-room composition is available to avoid
    # creating a second Studio authority or an import cycle during module construction.
    from .shared_skies_emergency_programme import install_shared_skies_emergency_programme
    from .shared_skies_live_assist import install_shared_skies_live_assist
    from .shared_skies_live_voice import install_shared_skies_live_voice
    from .shared_skies_live_voice_profiles import install_shared_skies_live_voice_profiles

    install_shared_skies_emergency_programme(app)
    install_shared_skies_live_assist(app)
    install_shared_skies_live_voice(app)
    install_shared_skies_live_voice_profiles(app)


__all__ = ["install_shared_sky_live_community", "PUBLIC_LIVE_PREFIXES"]
