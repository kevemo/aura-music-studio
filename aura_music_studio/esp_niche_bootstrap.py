from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from .accounts import AccountStore
from .esp_niche import niches

MEMBER_COOKIE = "lss_session"
store = AccountStore()


class EspNicheDashboardMiddleware(BaseHTTPMiddleware):
    """Adds the ESP-only niche onboarding/workspace to the ordinary Studio dashboard.

    The underlying dashboard remains a normal customer surface. The injected fragment is
    empty for non-ESP accounts, and every write/read API in esp_niche.py independently
    enforces active ESP membership server-side.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.url.path != "/dashboard" or response.status_code != 200:
            return response
        if "text/html" not in (response.headers.get("content-type") or ""):
            return response

        user = store.resolve_session(request.cookies.get(MEMBER_COOKIE))
        if not user:
            return response
        fragment = niches.dashboard_fragment(user["id"])
        if not fragment:
            return response

        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
        text = b"".join(chunks).decode("utf-8", errors="replace")
        marker = "</main></div>"
        if marker in text:
            text = text.replace(marker, fragment + marker, 1)
        elif "</body>" in text:
            text = text.replace("</body>", fragment + "</body>", 1)
        else:
            text += fragment

        headers = dict(response.headers)
        headers.pop("content-length", None)
        return HTMLResponse(text, status_code=response.status_code, headers=headers)
