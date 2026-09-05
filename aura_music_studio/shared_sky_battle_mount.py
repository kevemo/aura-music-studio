from __future__ import annotations

from typing import Any

from .shared_sky_battle_api import router as battle_router

_SENTINEL = ("/shared-sky/api/broadcasts/{live_session_id}/participants/host", ("POST",))
_REQUIRED = {
    _SENTINEL,
    ("/shared-sky/api/broadcasts/{live_session_id}/battles", ("POST",)),
    ("/shared-sky/api/battle-plans", ("POST",)),
    ("/shared-sky/api/battle-challenges", ("POST",)),
    ("/owner/shared-sky/api/battle-rulesets", ("POST",)),
}


def _signature(route: Any) -> tuple[str, tuple[str, ...]] | None:
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    return path, tuple(sorted(str(method).upper() for method in methods))


def install_shared_sky_battle_routes(app: Any) -> None:
    """Install Chat 6 authority directly on the one canonical FastAPI application.

    The Command Center has legacy parent-router composition that snapshots nested routes.  Chat 6
    keeps one Battle router/store authority, but binds that router directly to ``api.app`` so the
    participant and Battle controls cannot disappear when an already-snapshotted parent overlay is
    later included.  Signature checks make repeated imports/idempotent application assembly safe.
    """
    mounted = {
        signature
        for route in app.router.routes
        if (signature := _signature(route)) is not None
    }
    if _SENTINEL not in mounted:
        app.include_router(battle_router)
        mounted = {
            signature
            for route in app.router.routes
            if (signature := _signature(route)) is not None
        }

    missing = sorted(_REQUIRED - mounted)
    if missing:
        raise RuntimeError(f"Shared Sky Battle canonical route mount failed: {missing!r}")

    app.state.shared_sky_battle_routes_installed = True


# ``app.py`` imports ``aura_music_studio.api.app`` before importing the Creator overlay.  Bind the
# authoritative Battle router at that canonical boundary during overlay import, matching the
# proven late-binding pattern used by Shared Sky owner/community operations.
from .api import app as _canonical_app

install_shared_sky_battle_routes(_canonical_app)


__all__ = ["install_shared_sky_battle_routes"]
