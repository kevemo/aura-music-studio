from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


_PORTAL_PREFIX = "/aura-sec/approval/"
_API_PREFIX = "/api/aura-sec/member/actions/"
_API_WRITE_SUFFIXES = ("/approval-challenge", "/approve")


def _normalized_host(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        try:
            return (urlsplit(text).netloc or "").lower()
        except ValueError:
            return ""
    return text


def _is_approval_api_write(path: str) -> bool:
    return path.startswith(_API_PREFIX) and path.endswith(_API_WRITE_SUFFIXES)


def _same_origin_browser_write(request: Request) -> bool:
    host = _normalized_host(request.headers.get("host"))
    if not host:
        return False

    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site != "same-origin":
        return False

    origin = request.headers.get("origin")
    if origin:
        return _normalized_host(origin) == host

    referer = request.headers.get("referer")
    if referer:
        return _normalized_host(referer) == host

    # Sensitive cookie-authenticated writes must provide browser origin evidence. Modern
    # browsers send Origin on POST; Referer is accepted as a compatibility fallback.
    return False


class AuraSecApprovalWriteGuardMiddleware(BaseHTTPMiddleware):
    """Protect Aura Sec approval writes from ambient-cookie cross-site requests.

    Browser form approval uses the HttpOnly member cookie and therefore must prove an
    exact same-origin navigation. JSON/API approval writes require an explicit Bearer
    credential, which browsers do not attach ambiently to cross-site forms. This guard is
    additive to the one-time session/action-bound approval challenge and fixed action-risk
    policy; it never approves or executes an action itself.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() != "POST":
            return await call_next(request)

        path = request.url.path
        if _is_approval_api_write(path):
            auth = (request.headers.get("authorization") or "").strip()
            if not auth.lower().startswith("bearer ") or not auth[7:].strip():
                return JSONResponse(
                    {
                        "detail": "Aura Sec approval API writes require explicit Bearer authentication.",
                        "command_issued": False,
                    },
                    status_code=403,
                )
            return await call_next(request)

        if path.startswith(_PORTAL_PREFIX):
            if not _same_origin_browser_write(request):
                return JSONResponse(
                    {
                        "detail": "Aura Sec approval browser write rejected by same-origin policy.",
                        "command_issued": False,
                    },
                    status_code=403,
                )

        return await call_next(request)


__all__ = ["AuraSecApprovalWriteGuardMiddleware"]
