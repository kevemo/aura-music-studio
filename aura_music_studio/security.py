from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_RATE_PATHS = {"/auth/login", "/auth/signup", "/owner/login"}


class _SlidingWindowLimiter:
    def __init__(self):
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: tuple[str, str], *, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry
            bucket.append(now)
            return True, 0


_LIMITER = _SlidingWindowLimiter()


def _client_key(request: Request) -> str:
    # request.client is preferred over user-controlled X-Forwarded-For. In production,
    # configure uvicorn/reverse-proxy trusted forwarding so request.client is normalized.
    return request.client.host if request.client else "unknown"


def _same_origin(request: Request) -> bool:
    """Protect cookie-authenticated writes against cross-site requests.

    SameSite cookies already provide a useful browser barrier; Origin/Sec-Fetch-Site adds a
    second server-side check without requiring JavaScript-visible CSRF secrets.
    """
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site == "cross-site":
        return False

    origin = request.headers.get("origin")
    if not origin:
        # Traditional same-origin HTML form submissions may send Referer rather than Origin.
        origin = request.headers.get("referer")
    if not origin:
        # Non-browser clients using explicit Bearer auth are handled outside this function.
        return True

    parsed = urlparse(origin)
    request_host = (request.headers.get("host") or "").lower()
    origin_host = (parsed.netloc or "").lower()
    return bool(request_host and origin_host and request_host == origin_host)


class StudioSecurityMiddleware(BaseHTTPMiddleware):
    """Security envelope around the public membership/studio application."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in AUTH_RATE_PATHS and request.method == "POST":
            limit = int(os.getenv("LSS_AUTH_RATE_LIMIT", "12"))
            window = int(os.getenv("LSS_AUTH_RATE_WINDOW_SECONDS", "900"))
            allowed, retry = _LIMITER.allow((_client_key(request), path), limit=limit, window_seconds=window)
            if not allowed:
                return JSONResponse(
                    {"detail": "Too many authentication attempts. Try again later."},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )

        if request.method in UNSAFE_METHODS:
            has_cookie_auth = bool(
                request.cookies.get("lss_session")
                or request.cookies.get("lss_admin_session")
            )
            bearer = (request.headers.get("authorization") or "").lower().startswith("bearer ")
            if has_cookie_auth and not bearer and not _same_origin(request):
                return JSONResponse({"detail": "Cross-site write request blocked"}, status_code=403)

        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), payment=(), usb=(); microphone=(self)")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        )
        response.headers.setdefault("Cache-Control", "no-store" if path.startswith(("/auth", "/owner", "/dashboard")) else "private")

        public_url = (os.getenv("LSS_PUBLIC_BASE_URL") or "").lower()
        if public_url.startswith("https://"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
