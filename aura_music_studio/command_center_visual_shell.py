from __future__ import annotations

from html import escape

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .branding import ENDORSEMENT, PLATFORM_DESCRIPTOR, PRODUCT_FULL_NAME, TAGLINE


_HEAD_MARKER = "data-esp-command-center-shell='1'"


def shell_head(request: Request) -> str:
    origin = str(request.base_url).rstrip("/")
    art = f"{origin}/brand/command-center-art.webp"
    title = escape(PRODUCT_FULL_NAME, quote=True)
    description = escape(f"{ENDORSEMENT}. {TAGLINE}. {PLATFORM_DESCRIPTOR}.", quote=True)
    return (
        f"<meta {_HEAD_MARKER}>"
        "<link rel='stylesheet' href='/brand/theme.css'>"
        "<link rel='icon' type='image/webp' href='/favicon.webp'>"
        "<meta name='theme-color' content='#08040f'>"
        f"<meta name='application-name' content='{title}'>"
        f"<meta property='og:site_name' content='{title}'>"
        f"<meta property='og:title' content='{title}'>"
        f"<meta property='og:description' content='{description}'>"
        f"<meta property='og:image' content='{escape(art, quote=True)}'>"
        "<meta property='og:type' content='website'>"
        "<meta name='twitter:card' content='summary_large_image'>"
    )


def apply_visual_shell(html: str, request: Request) -> str:
    if _HEAD_MARKER not in html:
        head = shell_head(request)
        if "</head>" in html:
            html = html.replace("</head>", head + "</head>", 1)
        elif "<body" in html:
            html = head + html
    if "<body" in html and "esp-command-center-shell" not in html:
        html = html.replace("<body>", "<body class='esp-command-center-shell'>", 1)
        if "<body " in html and "esp-command-center-shell" not in html:
            html = html.replace("<body ", "<body class='esp-command-center-shell' ", 1)
    return html


class CommandCenterVisualShellMiddleware(BaseHTTPMiddleware):
    """Apply shared Command Center visuals/metadata to HTML without touching APIs or media."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )

        branded = apply_visual_shell(html, request).encode("utf-8")
        migrated = Response(
            content=branded,
            status_code=response.status_code,
            background=response.background,
        )
        raw_headers = [
            (key, value)
            for key, value in response.raw_headers
            if key.lower() != b"content-length"
        ]
        raw_headers.append((b"content-length", str(len(branded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["CommandCenterVisualShellMiddleware", "apply_visual_shell", "shell_head"]
