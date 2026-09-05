from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PRODUCTION_ENV = "production"
_COOKIE_NAMES = ("lss_session", "lss_admin_session")


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
    """Reject cross-site browser state changes before application routing.

    Bearer-token API clients are excluded because they do not rely on ambient browser cookies.
    Production cookie-authenticated writes require browser origin/fetch evidence and compare it
    only with explicitly configured trusted origins. This avoids using attacker-controlled Host
    input as its own authorization source while keeping local development ergonomics intact.
    """

    async def dispatch(self, request: Request, call_next):
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
