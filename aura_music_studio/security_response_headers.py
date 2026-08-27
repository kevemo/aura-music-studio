from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_SENSITIVE_PREFIXES = (
    "/owner",
    "/auth",
    "/account",
    "/admin",
    "/api/owner",
    "/api/admin",
)


def _public_base_is_https() -> bool:
    raw = (os.getenv("LSS_PUBLIC_BASE_URL") or "").strip()
    if not raw:
        return False
    try:
        return urlsplit(raw).scheme.lower() == "https"
    except ValueError:
        return False


def _request_is_https(request: Request) -> bool:
    if request.url.scheme.lower() == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return forwarded == "https" or _public_base_is_https()


def _is_sensitive_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in _SENSITIVE_PREFIXES)


class SecurityResponseHeadersMiddleware(BaseHTTPMiddleware):
    """Apply conservative browser security headers across the complete site.

    The policy deliberately avoids a blocking Content-Security-Policy until the mature
    inline-script surfaces have been migrated to nonce/hash based delivery. Shipping an
    unvalidated CSP would risk disabling authenticated production workflows. This layer
    still closes common browser attack surfaces centrally and prevents sensitive owner,
    auth and account responses from being cached by shared or private browser caches.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")

        if _request_is_https(request):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if _is_sensitive_path(request.url.path) or "set-cookie" in response.headers:
            response.headers["Cache-Control"] = "no-store, private"
            response.headers.setdefault("Pragma", "no-cache")

        return response


__all__ = ["SecurityResponseHeadersMiddleware"]
