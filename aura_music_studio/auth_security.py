from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not host:
        return None
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def _allowed_origins(request: Request) -> set[str]:
    values: set[str] = set()
    request_origin = _origin(f"{request.url.scheme}://{request.url.netloc}")
    if request_origin:
        values.add(request_origin)

    configured = [
        os.getenv("LSS_PUBLIC_BASE_URL") or "",
        *(os.getenv("AURA_ALLOWED_ORIGINS") or "").split(","),
    ]
    for item in configured:
        resolved = _origin(item)
        if resolved:
            values.add(resolved)
    return values


def _authorization_is_bearer(request: Request) -> bool:
    return (request.headers.get("authorization") or "").lower().startswith("bearer ")


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
    """Reject explicit cross-site browser state changes before application routing.

    This protects the shared cookie-authenticated site without requiring every feature team
    to implement its own Origin/Fetch-Metadata checks. Bearer-token API clients are excluded
    because they do not rely on ambient browser cookies. Requests from older/non-browser
    clients that provide none of these browser headers continue to normal authentication;
    high-impact HTML forms can additionally adopt synchronizer tokens in a later layer.
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
        elif request.headers.get("referer"):
            supplied = _origin(request.headers.get("referer"))
            if not supplied or supplied not in allowed:
                return JSONResponse(
                    {
                        "detail": "Request referrer is not allowed for a state-changing action",
                        "security_gate": "referer",
                    },
                    status_code=403,
                )

        response = await call_next(request)
        _merge_vary(response, "Sec-Fetch-Site", "Origin")
        return response


__all__ = ["CrossSiteRequestGuardMiddleware"]
