from __future__ import annotations

from fastapi import Request
from fastapi.routing import APIRoute
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from .game_forge_live_integration import router as game_forge_live_router
from .game_forge_live_transport_guard import router as game_forge_live_transport_guard_router
from .game_forge_model_assets import router as game_forge_model_assets_router
from .game_forge_model_generation import router as game_forge_model_generation_router
from .game_forge_project_binding import router as game_forge_project_binding_router
from .game_forge_project_navigation import (
    bound_project_name_for_game,
    game_id_from_subworkspace_path,
    project_navigation_script,
)
from .game_forge_shared_sky_transport import router as game_forge_shared_sky_transport_router
from .game_forge_visual_logic import router as game_forge_visual_logic_router
from .game_forge_visual_logic_portal import router as game_forge_visual_logic_portal_router
from .route_integrity import register_route_composition_hook


# These routers are part of the bounded Game Forge project surface but are assembled after the
# foundation Game Forge router. FastAPI snapshots nested routers when they are included, so a
# deterministic final-composition hook is required for canonical production reachability.
#
# The transport guard intentionally precedes the compatibility LIVE router. They share a handful
# of path/method signatures and the guard is the authoritative dispatch boundary because it keeps
# canonical Shared Sky programme-source readiness in sync. The installer then skips later exact
# duplicates rather than creating a second dispatch authority.
_GAME_FORGE_AUTHORITATIVE_ROUTERS = (
    game_forge_live_transport_guard_router,
    game_forge_live_router,
    game_forge_shared_sky_transport_router,
    game_forge_project_binding_router,
    game_forge_model_assets_router,
    game_forge_model_generation_router,
    game_forge_visual_logic_router,
    game_forge_visual_logic_portal_router,
)


def _http_signature(route: APIRoute) -> tuple[str, tuple[str, ...]]:
    return route.path, tuple(sorted(str(method).upper() for method in (route.methods or set())))


def _install_game_forge_project_routes(app) -> None:
    """Install bounded Game Forge project routes exactly once into the canonical app.

    Existing APIRoute objects are reused directly so request models, dependencies, methods,
    response metadata and the original Creator/member authorization handlers remain authoritative.
    No transport, payment, Owner/Admin or provider authority is recreated here.
    """

    existing = {
        _http_signature(route)
        for route in app.router.routes
        if isinstance(route, APIRoute)
    }
    for source_router in _GAME_FORGE_AUTHORITATIVE_ROUTERS:
        for route in source_router.routes:
            if not isinstance(route, APIRoute):
                continue
            signature = _http_signature(route)
            if signature in existing:
                continue
            app.router.routes.append(route)
            existing.add(signature)


register_route_composition_hook("game_forge_project_routes", _install_game_forge_project_routes)


class GameForgeProjectNavigationMiddleware(BaseHTTPMiddleware):
    """Carry authoritative Creative-project identity across Game Forge HTML sub-workspaces.

    Route handlers remain the authority for authentication and plan access. This middleware runs
    after the route response exists, reads only the already-persisted Game DNA binding, and rewrites
    navigation links in successful HTML pages. It never calls creator APIs and never changes API,
    download, public-gallery or runtime-frame responses.
    """

    async def dispatch(self, request: Request, call_next):
        game_id = game_id_from_subworkspace_path(request.url.path)
        response = await call_next(request)
        if request.method.upper() != "GET" or not game_id:
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response

        project_name = bound_project_name_for_game(game_id)
        if not project_name:
            # Legacy/unbound Game Forge behavior stays exactly as it was.
            return response
        script = project_navigation_script(game_id, project_name)
        if not script:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )

        marker = "data-game-project-continuity='1'"
        if marker not in text and "</body>" in text:
            text = text.replace("</body>", script + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["GameForgeProjectNavigationMiddleware"]
