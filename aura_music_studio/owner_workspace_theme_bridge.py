from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .owner_identity import actor_from_request, admin_authorized
from .workspace_theme import theme_css, themes

_THEME_STYLE = re.compile(r"<style id=['\"]workspace-theme-tokens['\"]>.*?</style>", re.IGNORECASE | re.DOTALL)


class OwnerWorkspaceThemeBridgeMiddleware(BaseHTTPMiddleware):
    """Overlay the selected Kev/Mary theme on all shared-admin creative HTML.

    Owner authorization still comes exclusively from the existing owner session. The actor cookie
    selects attribution/personalization only. This bridge makes the chosen owner's saved theme
    follow them into Aura and creative Studio pages instead of applying only on `/owner/*` routes.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if response.status_code >= 400 or "text/html" not in (response.headers.get("content-type") or "").lower():
            return response
        if not admin_authorized(request):
            return response
        actor = actor_from_request(request)
        if not actor or actor.get("id") not in {"kev", "mary"}:
            return response

        current = themes.current(f"owner:{actor['id']}")
        style = f"<style id='workspace-theme-tokens'>{theme_css(current['theme'])}</style>"
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
        raw = b"".join(chunks)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            return Response(raw, status_code=response.status_code, headers=headers, media_type=response.headers.get("content-type"))

        if _THEME_STYLE.search(text):
            text = _THEME_STYLE.sub(style, text, count=1)
        elif "</head>" in text:
            text = text.replace("</head>", style + "</head>", 1)
        elif "</body>" in text:
            text = text.replace("</body>", style + "</body>", 1)
        else:
            text = style + text

        # This is presentation context only and intentionally contains no credential or permission state.
        badge = (
            f"<meta name='esp-owner-workspace-actor' content='{actor['id']}'>"
            f"<meta name='esp-owner-workspace-name' content='{actor['display_name']}'>"
        )
        if "esp-owner-workspace-actor" not in text and "</head>" in text:
            text = text.replace("</head>", badge + "</head>", 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        return HTMLResponse(text, status_code=response.status_code, headers=headers)
