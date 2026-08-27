from __future__ import annotations

from html import escape
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .branding import ENDORSEMENT, PLATFORM_DESCRIPTOR, PRODUCT_FULL_NAME, TAGLINE


_HEAD_MARKER = "data-esp-command-center-shell='1'"
_BODY_CLASS = "esp-command-center-shell"
_BODY_TAG_RE = re.compile(r"<body(?P<attrs>[^>]*)>", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r"\bclass=(?P<quote>['\"])(?P<classes>.*?)(?P=quote)", re.IGNORECASE)


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


def _inject_body_class(html: str) -> str:
    match = _BODY_TAG_RE.search(html)
    if not match:
        return html

    attrs = match.group("attrs") or ""
    class_match = _CLASS_ATTR_RE.search(attrs)
    if class_match:
        classes = class_match.group("classes").split()
        if _BODY_CLASS in classes:
            return html
        quote = class_match.group("quote")
        updated_classes = " ".join([*classes, _BODY_CLASS])
        updated_attrs = (
            attrs[: class_match.start()]
            + f"class={quote}{updated_classes}{quote}"
            + attrs[class_match.end() :]
        )
    else:
        updated_attrs = f" class='{_BODY_CLASS}'{attrs}"

    replacement = f"<body{updated_attrs}>"
    return html[: match.start()] + replacement + html[match.end() :]


def apply_visual_shell(html: str, request: Request) -> str:
    # Head metadata and body styling are intentionally independent. A prior head
    # injection must never prevent the body class from being added.
    if _HEAD_MARKER not in html:
        head = shell_head(request)
        if "</head>" in html:
            html = html.replace("</head>", head + "</head>", 1)
        elif "<body" in html.lower():
            html = head + html

    html = _inject_body_class(html)
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
