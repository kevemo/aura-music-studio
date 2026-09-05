from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import aura_music_studio.owner_chat9_dashboard_ui as ui


PENDING_MARKER = (
    "<section class='card'><div class='row'><div><div class='eyebrow'>Waiting for Mary / Kev</div>"
)


def _summary() -> dict:
    return {
        "generated_at": "2026-09-05T21:45:00+00:00",
        "read_only": True,
        "support": {"open_cases": 3, "high_or_urgent_open": 2, "waiting_member": 1},
        "recruitment": {"active_leads": 8, "follow_up": 4, "activated": 5, "do_not_contact": 6},
        "training": {"published_courses": 7, "certificates": 9, "failed_attempts": 10},
        "announcements": {
            "active_published": 11,
            "scheduled": 12,
            "ack_required_active": 13,
            "acknowledgements_recorded": 14,
        },
        "evidence": {"draft_batches": 15, "metrics_needing_review": 16},
    }


def _dashboard_app() -> FastAPI:
    app = FastAPI()

    @app.get("/owner/dashboard", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(
            "<html><body><main><section class='card'>Existing Owner summary</section>"
            + PENDING_MARKER
            + "<h2>Pending ESP access</h2></div></div></section></main></body></html>"
        )

    app.add_middleware(ui.OwnerChat9DashboardUIMiddleware)
    return app


def test_authenticated_owner_dashboard_renders_aggregate_chat9_panel(monkeypatch):
    monkeypatch.setattr(ui, "owner_session_authorized", lambda request: True)
    monkeypatch.setattr(ui.operations, "summary", _summary)

    response = TestClient(_dashboard_app()).get("/owner/dashboard")

    assert response.status_code == 200
    body = response.text
    assert "id='chat9-owner-operations'" in body
    assert "Read-only operational pulse" in body
    assert "Creator, Agent &amp; support workflows" in body
    for label in (
        "Open support",
        "High / urgent",
        "Waiting member",
        "Active leads",
        "Lead follow-ups",
        "Activated leads",
        "Do not contact",
        "Published training",
        "Certificates",
        "Failed attempts",
        "Live announcements",
        "Scheduled",
        "Acknowledgement required",
        "Draft evidence",
        "Evidence review",
    ):
        assert label in body
    assert body.index("id='chat9-owner-operations'") < body.index("Waiting for Mary / Kev")

    panel = body.split("id='chat9-owner-operations'", 1)[1].split("Waiting for Mary / Kev", 1)[0]
    assert "href=" not in panel.lower()
    assert "<form" not in panel.lower()
    assert "<button" not in panel.lower()
    assert "lead notes" in panel.lower()
    assert "evidence contents" in panel.lower()


def test_owner_dashboard_panel_is_not_rendered_without_owner_session(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ui, "owner_session_authorized", lambda request: False)
    monkeypatch.setattr(ui.operations, "summary", lambda: calls.append("summary") or _summary())

    response = TestClient(_dashboard_app()).get("/owner/dashboard")

    assert response.status_code == 200
    assert "chat9-owner-operations" not in response.text
    assert calls == []


def test_owner_dashboard_survives_operational_snapshot_database_error(monkeypatch):
    monkeypatch.setattr(ui, "owner_session_authorized", lambda request: True)

    def broken_summary():
        raise sqlite3.OperationalError("temporary read failure")

    monkeypatch.setattr(ui.operations, "summary", broken_summary)
    response = TestClient(_dashboard_app()).get("/owner/dashboard")

    assert response.status_code == 200
    assert "Existing Owner summary" in response.text
    assert "chat9-owner-operations" not in response.text


def test_panel_renderer_contains_only_aggregate_values_and_read_only_copy():
    panel = ui.render_chat9_operations_panel(_summary())

    assert "Read only" in panel
    assert "Aggregate workflow counts" in panel
    assert "<b>3</b>" in panel
    assert "<b>16</b>" in panel
    assert "href=" not in panel.lower()
    assert "<form" not in panel.lower()
    assert "<button" not in panel.lower()
