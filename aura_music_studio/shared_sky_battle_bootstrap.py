from __future__ import annotations

from typing import Any

from .shared_sky_battle_api import router as shared_sky_battle_router

# The production compatibility composition can consume/snapshot APIRouters before late
# include_router calls are flattened. Capture the fully declared Chat 6 routes once, then bind
# those exact APIRoute objects to the canonical application deterministically.
_BATTLE_ROUTES = tuple(shared_sky_battle_router.routes)


def _route_signature(route: Any) -> tuple[str, tuple[str, ...]]:
    return (
        str(getattr(route, "path", "")),
        tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
    )


def install_shared_sky_battle_routes(app: Any) -> None:
    """Bind authoritative Chat 6 Battle/multi-host routes to the canonical FastAPI app.

    Directly append the already-built APIRoute objects from an immutable import-time snapshot.
    This preserves the original endpoints, dependencies, response metadata and server-authoritative
    Battle controls while bypassing the repository's late compatibility-router snapshot boundary.
    Exact path+method signatures keep repeated installation idempotent.
    """

    existing = {_route_signature(route) for route in app.router.routes}
    for route in _BATTLE_ROUTES:
        signature = _route_signature(route)
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = ["install_shared_sky_battle_routes"]
