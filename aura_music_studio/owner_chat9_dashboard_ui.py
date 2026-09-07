from __future__ import annotations

"""Read-only Chat 9 operational panel for the canonical Owner dashboard.

The Owner Control Center remains the only /owner/dashboard route. This middleware augments
its successful HTML response with aggregate counts from ``OwnerChat9Operations``. It creates
no workflow mutations, exposes no row-level data, and independently re-checks the canonical
Owner session before rendering the panel.
"""

import sqlite3

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .owner_chat9_operations import operations
from .owner_identity import owner_session_authorized

_PANEL_ID = "chat9-owner-operations"
_PENDING_SECTION_MARKER = (
    "<section class='card'><div class='row'><div><div class='eyebrow'>Waiting for Mary / Kev</div>"
)


def _metric(label: str, value: int) -> str:
    return f"<div class='metric'><small>{label}</small><b>{int(value)}</b></div>"


def render_chat9_operations_panel(summary: dict) -> str:
    support = summary.get("support") or {}
    recruitment = summary.get("recruitment") or {}
    training = summary.get("training") or {}
    announcements = summary.get("announcements") or {}
    evidence = summary.get("evidence") or {}

    metrics = "".join(
        (
            _metric("Open support", support.get("open_cases", 0)),
            _metric("High / urgent", support.get("high_or_urgent_open", 0)),
            _metric("Waiting member", support.get("waiting_member", 0)),
            _metric("Active leads", recruitment.get("active_leads", 0)),
            _metric("Lead follow-ups", recruitment.get("follow_up", 0)),
            _metric("Activated leads", recruitment.get("activated", 0)),
            _metric("Do not contact", recruitment.get("do_not_contact", 0)),
            _metric("Published training", training.get("published_courses", 0)),
            _metric("Certificates", training.get("certificates", 0)),
            _metric("Failed attempts", training.get("failed_attempts", 0)),
            _metric("Live announcements", announcements.get("active_published", 0)),
            _metric("Scheduled", announcements.get("scheduled", 0)),
            _metric("Acknowledgement required", announcements.get("ack_required_active", 0)),
            _metric("Draft evidence", evidence.get("draft_batches", 0)),
            _metric("Evidence review", evidence.get("metrics_needing_review", 0)),
        )
    )
    return (
        f"<section id='{_PANEL_ID}' class='card' aria-label='Chat 9 operational pulse'>"
        "<div class='row'><div><div class='eyebrow'>Read-only operational pulse</div>"
        "<h2>Creator, Agent &amp; support workflows</h2></div>"
        "<span class='pill good'>Read only</span></div>"
        "<p class='muted'>Aggregate workflow counts for Mary and Kev. No private case text, "
        "lead notes, evidence contents, member identity or mutation controls are exposed here.</p>"
        f"<div class='grid'>{metrics}</div></section>"
    )


def _response_with_body(response: Response, body: bytes) -> Response:
    rebuilt = Response(content=body, status_code=response.status_code, background=response.background)
    raw_headers = [
        (key, value)
        for key, value in response.raw_headers
        if key.lower() != b"content-length"
    ]
    raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
    rebuilt.raw_headers = raw_headers
    return rebuilt


class OwnerChat9DashboardUIMiddleware(BaseHTTPMiddleware):
    """Augment only the authenticated canonical Owner dashboard HTML response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET" or request.url.path != "/owner/dashboard":
            return response
        if response.status_code != 200 or not owner_session_authorized(request):
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return _response_with_body(response, body)
        if f"id='{_PANEL_ID}'" in text:
            return _response_with_body(response, body)

        try:
            summary = operations.summary()
        except sqlite3.Error:
            # The Owner dashboard remains available if the read-only operational snapshot
            # cannot be read; missing optional tables are already handled inside summary().
            return _response_with_body(response, body)

        panel = render_chat9_operations_panel(summary)
        if _PENDING_SECTION_MARKER in text:
            text = text.replace(_PENDING_SECTION_MARKER, panel + _PENDING_SECTION_MARKER, 1)
        elif "</main>" in text:
            text = text.replace("</main>", panel + "</main>", 1)
        else:
            text = text.replace("</body>", panel + "</body>", 1)
        return _response_with_body(response, text.encode("utf-8"))


def install_owner_chat9_dashboard_ui() -> None:
    """Install once on the canonical FastAPI app during production composition."""
    from .api import app

    if any(
        getattr(item, "cls", None) is OwnerChat9DashboardUIMiddleware
        for item in app.user_middleware
    ):
        return
    app.add_middleware(OwnerChat9DashboardUIMiddleware)


__all__ = [
    "OwnerChat9DashboardUIMiddleware",
    "install_owner_chat9_dashboard_ui",
    "render_chat9_operations_panel",
]
