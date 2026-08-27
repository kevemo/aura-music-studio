from __future__ import annotations

import json
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .accounts import AccountStore
from . import credit_wallet_registration as _credit_wallet_registration  # noqa: F401

_PROJECT_RE = re.compile(r"^/(?:creative/)?projects/([^/]+)")


class CreativeUsageMiddleware(BaseHTTPMiddleware):
    """Record successful creation activity across the unified studio.

    Older music routes already emit some usage records. This layer covers the newer
    cross-media/Aura workflow so Mary/Kev can see creation categories and activity from
    one owner user view without inspecting a member's private creative content.
    """

    def __init__(self, app):
        super().__init__(app)
        self.accounts = AccountStore()

    @staticmethod
    def _event(request: Request) -> tuple[str | None, str | None, dict]:
        method = request.method.upper()
        path = request.url.path
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None, None, {}

        project_id = None
        match = _PROJECT_RE.match(path)
        if match:
            project_id = match.group(1)

        if path == "/songs" and method == "POST":
            return "music_project_created", None, {"category": "music"}
        if path.startswith("/creative/projects/"):
            if path.endswith("/initialize") and method == "POST":
                return "creative_project_initialized", project_id, {"category": "creative_project"}
            if path.endswith("/elements") and method == "POST":
                return "creative_element_created", project_id, {"category": "multimedia"}
            if path.endswith("/references") and method == "POST":
                return "creative_reference_added", project_id, {"category": "reference"}
            if path.endswith("/directives") and method == "POST":
                return "aura_creative_directive", project_id, {"category": "aura"}
            if path.endswith("/render") and method == "POST":
                return "creative_render_queued", project_id, {"category": "image_or_video"}
            if path.endswith("/sync-outputs") and method == "POST":
                return "creative_outputs_imported", project_id, {"category": "image_or_video"}
            if "/elements/" in path and method == "PATCH":
                return "creative_element_edited", project_id, {"category": "multimedia_edit"}
        if path.startswith("/projects/") and path.endswith("/produce") and method == "POST":
            return "music_production_render", project_id, {"category": "music"}
        if path.startswith("/projects/") and path.endswith("/producer") and method == "POST":
            return "aura_producer_request", project_id, {"category": "aura"}
        if path.startswith("/projects/") and "/session" in path and method in {"POST", "PUT", "PATCH"}:
            return "studio_session_edit", project_id, {"category": "music_editing"}
        return None, project_id, {}

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            return response
        event_type, project_id, metadata = self._event(request)
        if not event_type:
            return response
        member = getattr(request.state, "member", None)
        user_id = getattr(member, "user_id", None)
        if not user_id:
            return response
        try:
            self.accounts.record_usage(
                user_id,
                event_type,
                project_id=project_id,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )
        except Exception:
            # Tracking must never break a successful creative action.
            pass
        return response
