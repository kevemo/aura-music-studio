from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .account_security_api import router as account_security_router
from .csrf_tokens import CSRF_HEADER, router as csrf_router, service as csrf_service
from .email_verification import router as email_verification_router
from .email_verification_integration import install_email_verification
from .membership_api import router as membership_router
from .tenant_storage import project_path

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_RATE_PATHS = {
    "/auth/login",
    "/auth/signup",
    "/owner/login",
    "/auth/password-reset/request",
    "/auth/password-reset/confirm",
    "/auth/email-verification/request",
    "/auth/email-verification/confirm",
}
PUBLIC_PWA_PATHS = {
    "/", "/pricing", "/signin", "/signup", "/ai-music-studio", "/ai-song-generator",
    "/backing-track-maker", "/stem-splitter", "/ai-mastering", "/ai-vocal-studio",
}
SENSITIVE_ACCOUNT_PAGES = {
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/verify-email",
}

# Base API composition imports membership_api before this module, then mounts membership_router.
# Attach account-security routes once here so every production entrypoint receives the same routes
# without modifying Creative/ESP feature router tables.
if not any(
    getattr(route, "path", None) == "/auth/password-reset/request"
    for route in membership_router.routes
):
    membership_router.include_router(account_security_router)

if not any(
    getattr(route, "path", None) == "/auth/email-verification/request"
    for route in membership_router.routes
):
    membership_router.include_router(email_verification_router)

if not any(
    getattr(route, "path", None) == "/auth/csrf-token"
    for route in membership_router.routes
):
    membership_router.include_router(csrf_router)

# Extend the existing signup/approval callables only after all routes are present. The installer
# preserves FastAPI's dependency model and does not replace membership/billing/ESP role logic.
install_email_verification(membership_router)


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
    return request.client.host if request.client else "unknown"


def _same_origin(request: Request) -> bool:
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site == "cross-site":
        return False
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    parsed = urlparse(origin)
    request_host = (request.headers.get("host") or "").lower()
    origin_host = (parsed.netloc or "").lower()
    return bool(request_host and origin_host and request_host == origin_host)


def _requires_session_csrf(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    if method == "DELETE" and path == "/privacy/account":
        return True
    if method == "POST" and path == "/auth/sessions/revoke-others":
        return True
    if method == "DELETE" and path.startswith("/auth/sessions/"):
        session_id = path.removeprefix("/auth/sessions/").strip()
        return bool(session_id and "/" not in session_id)
    return False


def _known_public_url() -> str:
    configured = (os.getenv("LSS_PUBLIC_BASE_URL") or "").strip()
    if configured and configured.lower() not in {"auto", "automatic"}:
        return configured
    try:
        from .public_address import PublicAddressManager
        return (PublicAddressManager().read_status().get("recommended_url") or "").strip()
    except Exception:
        return ""


def _project_name_from_path(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) < 2 or parts[0] != "projects":
        return None
    name = parts[1].strip()
    return name or None


def _safe_public_project_value(value, project_root: Path):
    if isinstance(value, dict):
        return {key: _safe_public_project_value(item, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_public_project_value(item, project_root) for item in value]
    if isinstance(value, tuple):
        return [_safe_public_project_value(item, project_root) for item in value]
    if not isinstance(value, str) or not value:
        return value

    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    try:
        return candidate.resolve().relative_to(project_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return "[redacted-host-path]"


async def _scrub_project_json_response(response, path: str):
    """Remove absolute host paths from member project JSON responses.

    Older synchronous endpoints pre-date tenant-safe public references and can return absolute
    paths. This response boundary converts paths inside the authenticated member project to
    project-relative refs and redacts any absolute path outside that project. It does not alter
    routing, membership, billing, Creation Coin, or ESP role decisions.
    """
    project_name = _project_name_from_path(path)
    content_type = (response.headers.get("content-type") or "").lower()
    if not project_name or "application/json" not in content_type or not hasattr(response, "body_iterator"):
        return response

    try:
        project_root = project_path(project_name, must_exist=True).resolve()
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return response

    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    raw = b"".join(chunks)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
        return Response(content=raw, status_code=response.status_code, headers=headers, media_type=content_type)

    scrubbed = _safe_public_project_value(payload, project_root)
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    return JSONResponse(content=scrubbed, status_code=response.status_code, headers=headers)


async def _inject_esp_brand(response, path: str):
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/html" not in content_type or not hasattr(response, "body_iterator"):
        return response

    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Response(content=raw, status_code=response.status_code, headers=dict(response.headers), media_type=content_type)

    if "/brand/theme.css" not in text:
        head = (
            "<link rel='stylesheet' href='/brand/theme.css'>"
            "<link rel='icon' type='image/webp' href='/brand/esp-logo.webp'>"
            "<link rel='apple-touch-icon' href='/brand/esp-logo.webp'>"
            "<link rel='manifest' href='/manifest.webmanifest'>"
            "<meta name='theme-color' content='#120818'>"
        )
        if "</head>" in text:
            text = text.replace("</head>", head + "</head>", 1)
        else:
            text = head + text

    if path == "/signin" and "href='/auth/forgot-password'" not in text and "</form>" in text:
        text = text.replace(
            "</form>",
            "</form><p class='help'><a href='/auth/forgot-password'>Forgot password?</a></p>",
            1,
        )

    extras = ""
    if path == "/auth/reset-password":
        extras += "<script>try{history.replaceState({},'', '/auth/reset-password')}catch(e){}</script>"
    if path in {"/studio", "/production-suite"} and "esp-history-fab" not in text:
        extras += "<a class='esp-history-fab' href='/history' title='Project history and undo'>↶ Project History</a>"
    if path == "/production-suite" and "href='/take-manager'" not in text:
        extras += "<a class='esp-history-fab' style='bottom:66px' href='/take-manager' title='Audition and select generated performances'>🎚 Take Manager</a>"
    if path in {"/studio", "/production-suite"} and "href='/recording-studio'" not in text:
        recording_bottom = "66px" if path == "/studio" else "114px"
        extras += f"<a class='esp-history-fab' style='bottom:{recording_bottom}' href='/recording-studio' title='Record vocals and instruments directly'>🎙 Recording Studio</a>"
    if path in {"/studio", "/production-suite"} and "href='/daw'" not in text:
        daw_bottom = "114px" if path == "/studio" else "162px"
        extras += f"<a class='esp-history-fab' style='bottom:{daw_bottom}' href='/daw' title='Open the visual waveform timeline'>🎚 Visual DAW</a>"
    if path == "/daw" and "/daw/recording-ui.js" not in text:
        extras += "<script src='/daw/recording-ui.js'></script>"
    if path == "/daw" and "/daw/routing-ui.js" not in text:
        extras += "<script src='/daw/routing-ui.js'></script>"
    if path == "/daw" and "/daw/mixer-ui.js" not in text:
        extras += "<script src='/daw/mixer-ui.js'></script>"
    if path == "/owner/dashboard" and "href='/owner/compute-nodes'" not in text:
        extras += "<a class='esp-history-fab' href='/owner/compute-nodes' title='Manage ESP compute machines'>🖥 Compute Nodes</a>"
    if path in PUBLIC_PWA_PATHS and "serviceWorker.register" not in text:
        extras += "<script>if('serviceWorker' in navigator){window.addEventListener('load',()=>navigator.serviceWorker.register('/service-worker.js').catch(()=>{}));}</script>"
    if extras:
        text = text.replace("</body>", extras + "</body>", 1) if "</body>" in text else text + extras

    headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
    return Response(content=text, status_code=response.status_code, headers=headers, media_type="text/html")


class StudioSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in AUTH_RATE_PATHS and request.method == "POST":
            limit = int(os.getenv("LSS_AUTH_RATE_LIMIT", "12"))
            window = int(os.getenv("LSS_AUTH_RATE_WINDOW_SECONDS", "900"))
            allowed, retry = _LIMITER.allow((_client_key(request), path), limit=limit, window_seconds=window)
            if not allowed:
                return JSONResponse({"detail": "Too many authentication attempts. Try again later."}, status_code=429, headers={"Retry-After": str(retry)})

        if request.method in UNSAFE_METHODS:
            member_cookie = request.cookies.get("lss_session") or ""
            has_cookie_auth = bool(member_cookie or request.cookies.get("lss_admin_session"))
            bearer = (request.headers.get("authorization") or "").lower().startswith("bearer ")
            if has_cookie_auth and not bearer and not _same_origin(request):
                return JSONResponse({"detail": "Cross-site write request blocked"}, status_code=403)

            if member_cookie and not bearer and _requires_session_csrf(request):
                supplied = request.headers.get(CSRF_HEADER) or ""
                if not csrf_service.verify(member_cookie, supplied):
                    return JSONResponse(
                        {
                            "detail": "A valid session-bound CSRF token is required for this destructive action",
                            "security_gate": "session_csrf",
                            "csrf_token_endpoint": "/auth/csrf-token",
                            "csrf_header": CSRF_HEADER,
                        },
                        status_code=403,
                    )

        response = await call_next(request)
        response = await _scrub_project_json_response(response, path)
        response = await _inject_esp_brand(response, path)
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
        if path.startswith(("/auth", "/owner", "/dashboard", "/membership", "/node-coordinator")) or path in SENSITIVE_ACCOUNT_PAGES:
            response.headers.setdefault("Cache-Control", "no-store")
        elif path in PUBLIC_PWA_PATHS or path in {"/robots.txt", "/sitemap.xml", "/manifest.webmanifest", "/service-worker.js"}:
            response.headers.setdefault("Cache-Control", "public, max-age=300")
        else:
            response.headers.setdefault("Cache-Control", "private")

        public_url = _known_public_url().lower()
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").lower()
        if public_url.startswith("https://") or forwarded_proto == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
