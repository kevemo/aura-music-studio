from __future__ import annotations

from starlette.requests import Request

import aura_music_studio.shared_sky_live_events_ui as events_ui
from aura_music_studio.shared_sky_live_bootstrap import install_shared_sky_live_community


def _request(path: str = "/live-events") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"user-agent", b"pytest-shared-sky-events-ui")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


class FakeEvents:
    def list_events(self, user_id, **kwargs):
        assert user_id is None
        assert kwargs["owner"] is False
        return [
            {
                "schedule_id": "safe-schedule-1",
                "creator_user_id": "creator-1",
                "creator_display_name": "Creator <One>",
                "title": "Songwriting & Stories",
                "description": "Public event copy only",
                "category": "music",
                "tags": ["original", "live"],
                "language": "en",
                "visibility": "public",
                "suitability": "general",
                "thumbnail_url": "",
                "start_at": "2026-09-05T18:00:00+00:00",
                "mode": "live",
                "version": 1,
                "following": False,
                "reminder": None,
            }
        ]

    def event(self, schedule_id, user_id, **kwargs):
        assert schedule_id == "safe-schedule-1"
        assert user_id is None
        return self.list_events(None, owner=False)[0]


def test_upcoming_live_page_renders_published_safe_fields_and_escapes_creator(monkeypatch):
    monkeypatch.setattr(events_ui, "events_store", FakeEvents())
    monkeypatch.setattr(events_ui, "_member_id", lambda request: None)
    monkeypatch.setattr(events_ui, "owner_session_authorized", lambda request: False)

    response = events_ui.upcoming_events_page(_request())
    body = response.body.decode()

    assert response.status_code == 200
    assert "Songwriting &amp; Stories" in body
    assert "Creator &lt;One&gt;" in body
    assert "/live-events/safe-schedule-1" in body
    assert "project_id" not in body
    assert "destination_ids" not in body
    assert "credential" not in body


def test_event_detail_explains_in_app_reminder_truthfully(monkeypatch):
    monkeypatch.setattr(events_ui, "events_store", FakeEvents())
    monkeypatch.setattr(events_ui, "_member_id", lambda request: None)
    monkeypatch.setattr(events_ui, "owner_session_authorized", lambda request: False)

    response = events_ui.upcoming_event_page("safe-schedule-1", _request("/live-events/safe-schedule-1"))
    body = response.body.decode()

    assert response.status_code == 200
    assert "Sign in to set a reminder" in body
    assert "Email/push delivery is not assumed" in body
    assert "/shared-sky/live/api/events/" in body
    assert "project-private" not in body


def test_live_bootstrap_mounts_upcoming_event_pages_and_public_envelope():
    from fastapi import FastAPI
    from aura_music_studio import access_control

    app = FastAPI()
    install_shared_sky_live_community(app)
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/live-events" in paths
    assert "/live-events/{schedule_id}" in paths
    assert "/live-events" in access_control.PUBLIC_EXACT
    assert "/live-events/" in access_control.PUBLIC_PREFIXES
