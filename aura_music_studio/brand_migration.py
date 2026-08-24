from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Compatibility bridge: historical product copy may still exist inside legacy modules
# and persisted templates. This middleware makes Pulsar-Frequency House authoritative
# at the HTTP boundary without renaming storage keys, cookies or package imports.
_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "Powered by Elevate Souls Productions and Aura AI Systems",
        "Powered by Elevate Souls Productions & Aura AI Systems",
    ),
    (
        "Elevate Souls Productions Presents: The Live Sound Studio",
        "Pulsar-Frequency House",
    ),
    (
        "Elevate Souls Productions Presents: Live Sound Studio",
        "Pulsar-Frequency House",
    ),
    (
        "Elevate Souls Productions Presents",
        "Powered by Elevate Souls Productions & Aura AI Systems",
    ),
    ("4Infinity Creative Studios", "Pulsar-Frequency House"),
    ("Cosmic Creative Studios", "Pulsar-Frequency House"),
    ("Cosmic Creation Studios", "Pulsar-Frequency House"),
    ("The Live Sound Studio", "Pulsar-Frequency House"),
    ("Live Sound Studio", "Pulsar-Frequency House"),
    ("4Infinity", "Pulsar-Frequency"),
    (
        "Music, Video, Image & Creator Intelligence",
        "For Professional Creation Beyond The Cosmos",
    ),
    ("Music Making for Professionals", "For Professional Creation Beyond The Cosmos"),
)

_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/manifest+json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)


def rebrand_text(value: str) -> str:
    for old, new in _REPLACEMENTS:
        value = value.replace(old, new)
    return value


class BrandMigrationMiddleware(BaseHTTPMiddleware):
    """Rewrite legacy public-facing product copy to Pulsar-Frequency House.

    Binary audio/video/image responses are passed through untouched. Response headers,
    including repeated Set-Cookie headers, are preserved while Content-Length is
    recalculated after text replacement.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if not any(content_type.startswith(prefix) for prefix in _TEXTUAL_CONTENT_TYPES):
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

        branded = rebrand_text(text).encode("utf-8")
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
