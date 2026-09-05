from __future__ import annotations

import os
import sqlite3
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth_rate_limit import AuthRateLimitStore

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PRODUCTION_ENV = "production"
_DURABLE_RATE_ENVIRONMENTS = {"production", "staging"}
_COOKIE_NAMES = ("lss_session", "lss_admin_session")
_AUTH_RATE_PATHS = {
    "/auth/login",
    "/auth/signup",
    "/owner/login",
    "/auth/password-reset/request",
    "/auth/password-reset/confirm",
    "/auth/email-verification/request",
    "/auth/email-verification/confirm",
}


def _origin(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw or raw.lower() == "null":
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not host:
        return None
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def _deployment_environment() -> str:
    return (os.getenv("AURA_DEPLOYMENT_ENV") or "development").strip().lower()


def _configured_origins() -> set[str]:
    values: set[str] = set()
    configured = [
        os.getenv("LSS_PUBLIC_BASE_URL") or "",
        *(os.getenv("AURA_ALLOWED_ORIGINS") or "").split(","),
    ]
    for item in configured:
        resolved = _origin(item)
        if resolved:
            values.add(resolved)
    return values


def _allowed_origins(request: Request) -> set[str]:
    """Return origins trusted for state-changing browser requests.

    Production never reflects the incoming Host header into the allowlist. The deployment must
    explicitly configure its public/trusted origins, so Host-header spoofing cannot manufacture
    an allowed Origin. Development/test retain request-origin fallback for local tooling.
    """
    values = _configured_origins()
    if _deployment_environment() != _PRODUCTION_ENV:
        request_origin = _origin(f"{request.url.scheme}://{request.url.netloc}")
        if request_origin:
            values.add(request_origin)
    return values


def _authorization_is_bearer(request: Request) -> bool:
    return (request.headers.get("authorization") or "").lower().startswith("bearer ")


def _has_cookie_auth(request: Request) -> bool:
    return any(bool(request.cookies.get(name)) for name in _COOKIE_NAMES)


def _client_key(request: Request) -> str:
    # Uvicorn/ASGI proxy-header trust must be configured at deployment if a trusted edge should
    # replace request.client with the originating client address. This middleware deliberately
    # does not trust arbitrary X-Forwarded-For input by itself.
    return request.client.host if request.client else "unknown"


def _durable_auth_rate_admission(request: Request) -> JSONResponse | None:
    """Apply cross-worker auth throttling for production/staging on the shared durable database."""
    if request.method.upper() != "POST" or request.url.path not in _AUTH_RATE_PATHS:
        return None
    if _deployment_environment() not in _DURABLE_RATE_ENVIRONMENTS:
        return None

    try:
        limit = int((os.getenv("LSS_AUTH_RATE_LIMIT") or "12").strip())
        window = int((os.getenv("LSS_AUTH_RATE_WINDOW_SECONDS") or "900").strip())
    except ValueError:
        return JSONResponse(
            {"detail": "Authentication rate-limit security policy is unavailable", "security_gate": "durable_auth_rate_limit"},
            status_code=503,
        )

    db_path = (os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3").strip()
    try:
        allowed, retry = AuthRateLimitStore(db_path).allow(
            _client_key(request),
            request.url.path,
            limit=limit,
            window_seconds=window,
        )
    except (sqlite3.Error, OSError, ValueError):
        return JSONResponse(
            {"detail": "Authentication rate-limit security policy is unavailable", "security_gate": "durable_auth_rate_limit"},
            status_code=503,
        )

    if not allowed:
        return JSONResponse(
            {"detail": "Too many authentication attempts. Try again later.", "security_gate": "durable_auth_rate_limit"},
            status_code=429,
            headers={"Retry-After": str(retry)},
        )
    return None


def _merge_vary(response, *names: str) -> None:
    existing = [part.strip() for part in (response.headers.get("vary") or "").split(",") if part.strip()]
    lowered = {part.lower() for part in existing}
    for name in names:
        if name.lower() not in lowered:
            existing.append(name)
            lowered.add(name.lower())
    if existing:
        response.headers["Vary"] = ", ".join(existing)


class CrossSiteRequestGuardMiddleware(BaseHTTPMiddleware):
    """Enforce shared browser-origin and durable auth-abuse boundaries before application routing.

    Production/staging authentication endpoints use the shared SQLite rate ledger so multiple
    application workers cannot each grant an independent attempt budget. Bearer-token API clients
    remain excluded from ambient-cookie CSRF rules, but authentication attempts are still rate
    admitted before that distinction because bearer headers must not bypass credential abuse gates.
    """

    async def dispatch(self, request: Request, call_next):
        rate_rejection = _durable_auth_rate_admission(request)
        if rate_rejection is not None:
            return rate_rejection

        if request.method.upper() not in _UNSAFE_METHODS:
            return await call_next(request)
        if _authorization_is_bearer(request):
            return await call_next(request)

        fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse(
                {
                    "detail": "Cross-site state-changing requests are not accepted",
                    "security_gate": "fetch_metadata",
                },
                status_code=403,
            )

        allowed = _allowed_origins(request)
        origin_header = request.headers.get("origin")
        referer_header = request.headers.get("referer")
        if origin_header is not None:
            supplied = _origin(origin_header)
            if not supplied or supplied not in allowed:
                return JSONResponse(
                    {
                        "detail": "Request origin is not allowed for a state-changing action",
                        "security_gate": "origin",
                    },
                    status_code=403,
                )
        elif referer_header:
            supplied = _origin(referer_header)
            if not supplied or supplied not in allowed:
                return JSONResponse(
                    {
                        "detail": "Request referrer is not allowed for a state-changing action",
                        "security_gate": "referer",
                    },
                    status_code=403,
                )
        elif _deployment_environment() == _PRODUCTION_ENV and _has_cookie_auth(request):
            # Modern browser state-changing requests provide Origin, Referer and/or Fetch Metadata.
            # A production request carrying ambient auth but none of that evidence is refused;
            # non-browser integrations should use explicit bearer authentication instead.
            return JSONResponse(
                {
                    "detail": "Production cookie-authenticated writes require trusted browser origin evidence",
                    "security_gate": "browser_origin_evidence",
                },
                status_code=403,
            )

        response = await call_next(request)
        _merge_vary(response, "Sec-Fetch-Site", "Origin")
        return response


__all__ = ["CrossSiteRequestGuardMiddleware"]
