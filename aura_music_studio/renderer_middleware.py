from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .renderer_runtime import RendererUnavailable, require_render_capacity


class RendererAdmissionMiddleware(BaseHTTPMiddleware):
    """Fail neural-only production requests before quota or queue mutation when compute is offline."""

    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/")
        if request.method == "POST" and path.startswith("/production/projects/") and path.endswith("/build-around"):
            try:
                capacity = require_render_capacity(task_type="complete")
            except RendererUnavailable as exc:
                return JSONResponse(
                    {
                        "detail": str(exc),
                        "renderer_ready": False,
                        "required_engine": "ace-step-1.5",
                        "required_task": "complete",
                        "quota_consumed": False,
                    },
                    status_code=503,
                )
            request.state.renderer_admission = capacity
        return await call_next(request)
