from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from .game_forge_project_navigation import (
    bound_project_name_for_game,
    game_id_from_subworkspace_path,
    project_navigation_script,
)


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
