from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


_BROWSER_WRITE_PREFIXES = ("/aura-sec/approval/", "/aura-sec/passkeys/")
_API_PREFIX = "/api/aura-sec/member/actions/"
_API_WRITE_SUFFIXES = ("/approval-challenge", "/approve")


def _origin_tuple(value: str | None) -> tuple[str, str, int] | None:
    text = (value or "").strip()
    if not text or "://" not in text:
        return None
    try:
        parsed = urlsplit(text)
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()
        if scheme not in {"http", "https"} or not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, int(port)


def _request_origin(request: Request) -> tuple[str, str, int] | None:
    host = (request.headers.get("host") or "").strip()
    scheme = (request.url.scheme or "").strip().lower()
    if not host or scheme not in {"http", "https"}:
        return None
    return _origin_tuple(f"{scheme}://{host}")


def _is_approval_api_write(path: str) -> bool:
    return path.startswith(_API_PREFIX) and path.endswith(_API_WRITE_SUFFIXES)


def _same_origin_browser_write(request: Request) -> bool:
    expected = _request_origin(request)
    if expected is None:
        return False

    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site != "same-origin":
        return False

    origin = request.headers.get("origin")
    if origin:
        return _origin_tuple(origin) == expected

    referer = request.headers.get("referer")
    if referer:
        return _origin_tuple(referer) == expected

    # Sensitive cookie-authenticated writes must provide browser origin evidence. Modern
    # browsers send Origin on POST; Referer is accepted as a compatibility fallback.
    return False


class AuraSecApprovalWriteGuardMiddleware(BaseHTTPMiddleware):
    """Protect Aura Sec approval and passkey writes from ambient-cookie cross-site requests.

    Browser approval/passkey ceremonies use the HttpOnly member cookie and therefore must
    prove an exact same-origin navigation, including scheme, host and effective port.
    JSON approval API writes require an explicit Bearer credential, which browsers do not
    attach ambiently to cross-site forms. This guard is additive to WebAuthn verification,
    one-time session/action-bound approval challenges and fixed action-risk policy; it never
    approves or executes an action itself.
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

        if path.startswith(_BROWSER_WRITE_PREFIXES):
            if not _same_origin_browser_write(request):
                return JSONResponse(
                    {
                        "detail": "Aura Sec security browser write rejected by same-origin policy.",
                        "command_issued": False,
                    },
                    status_code=403,
                )

        return await call_next(request)


__all__ = ["AuraSecApprovalWriteGuardMiddleware"]
