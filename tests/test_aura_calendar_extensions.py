from __future__ import annotations

import pytest

from aura_music_studio.aura_calendar_extensions import (
    CALENDAR_EVENT_SPEC,
    _clean_event_id,
    read_calendar_event,
    router,
)


class FakeConnector:
    def __init__(self):
        self.calls = []

    def _get(self, user_id, service, url, *, params=None):
        self.calls.append((user_id, service, url, params or {}))
        return {
            "id": "abc/def",
            "summary": "Creator strategy meeting",
            "description": "x" * 13000,
            "location": "Studio A",
            "start": {"dateTime": "2026-08-25T18:00:00+01:00"},
            "end": {"dateTime": "2026-08-25T19:00:00+01:00"},
            "status": "confirmed",
            "htmlLink": "https://calendar.google.com/example",
            "hangoutLink": "https://meet.google.com/example",
            "organizer": {"email": "owner@example.com", "displayName": "Owner", "self": True},
            "creator": {"email": "owner@example.com", "displayName": "Owner", "self": True},
            "attendees": [
                {"email": f"user-{i}@example.com", "displayName": f"User {i}", "responseStatus": "accepted"}
                for i in range(120)
            ],
            "conferenceData": {
                "conferenceSolution": {"name": "Google Meet"},
                "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/example", "label": "Meet"}],
            },
            "attachments": [
                {"title": f"File {i}", "mimeType": "text/plain", "fileId": f"file-{i}", "fileUrl": "https://drive.google.com/example"}
                for i in range(25)
            ],
            "extendedProperties": {"private": {"secret": "must-not-leak"}},
            "eventType": "default",
            "visibility": "default",
        }


def test_calendar_event_detail_uses_stable_id_and_is_bounded_read_only():
    connector = FakeConnector()
    result = read_calendar_event("user-a", "abc/def", connector=connector)
    user_id, service, url, params = connector.calls[0]
    assert user_id == "user-a"
    assert service == "calendar"
    assert url.endswith("/abc%2Fdef")
    assert params["maxAttendees"] == 100
    assert "extendedProperties" not in params["fields"]
    assert len(result["description"]) == 12000
    assert len(result["attendees"]) == 100
    assert len(result["attachments"]) == 20
    assert result["conference"]["solution_name"] == "Google Meet"
    assert result["read_only"] is True
    assert result["event_modified"] is False
    assert result["attachments_downloaded"] is False
    assert result["private_extended_properties_included"] is False
    assert result["tokens_exposed"] is False
    assert "secret" not in str(result)


def test_calendar_event_id_validation_and_routes():
    with pytest.raises(ValueError):
        _clean_event_id("")
    with pytest.raises(ValueError):
        _clean_event_id("x\ninvalid")
    assert CALENDAR_EVENT_SPEC.write is False
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/aura-intelligence/api/connectors/google/calendar/events/{event_id:path}" in paths
