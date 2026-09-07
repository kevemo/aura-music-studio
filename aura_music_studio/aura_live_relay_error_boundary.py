from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .aura_live_relay_legacy_schema import prepare_legacy_receipt_schema

_RELAY_INGEST_PATH = "/live-overlay/source/relay/events"
_RELAY_STATE_PATHS = {
    _RELAY_INGEST_PATH,
    "/api/live-overlays/connector",
}


class AuraLiveRelayErrorBoundaryMiddleware(BaseHTTPMiddleware):
    """Preflight historical relay state and preserve bounded identity-validation errors."""

    async def dispatch(self, request: Request, call_next):
        try:
            if request.url.path in _RELAY_STATE_PATHS:
                prepare_legacy_receipt_schema()
            return await call_next(request)
        except HTTPException as exc:
            if request.url.path != _RELAY_INGEST_PATH:
                raise
            headers = {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            }
            if exc.headers:
                headers.update(exc.headers)
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=headers,
            )


__all__ = ["AuraLiveRelayErrorBoundaryMiddleware"]
