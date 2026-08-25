from __future__ import annotations

import pytest

from aura_music_studio import aura_workspace_briefing as briefing


def test_workspace_briefing_uses_only_connected_services_and_does_not_bulk_scan_drive(monkeypatch):
    monkeypatch.setattr(briefing.vault, "status", lambda user_id: [{"provider": "google", "services": ["calendar", "gmail", "drive"]}])
    calls = {"calendar": 0, "gmail": 0, "drive": 0}

    def calendar(user_id, **kwargs):
        calls["calendar"] += 1
        return {"time_min": kwargs["time_min"], "time_max": kwargs["time_max"], "events": [{"id": "e1", "summary": "Meeting"}]}

    def gmail(user_id, query, limit):
        calls["gmail"] += 1
        return {"messages": [{"id": "m1", "subject": "Important", "snippet": "Please review"}]}

    def drive(user_id, query, limit):
        calls["drive"] += 1
        return {"files": [{"id": "f1", "name": "Plan.docx"}]}

    monkeypatch.setattr(briefing.google, "calendar_events", calendar)
    monkeypatch.setattr(briefing.google, "gmail_search", gmail)
    monkeypatch.setattr(briefing.google, "drive_search", drive)

    result = briefing.build_workspace_briefing("user-1", hours=24)
    assert calls == {"calendar": 1, "gmail": 1, "drive": 0}
    assert result["calendar"]["events"][0]["summary"] == "Meeting"
    assert result["gmail"]["messages"][0]["subject"] == "Important"
    assert result["drive"]["searched"] is False
    assert "bulk-scan" in result["drive"]["reason_not_searched"]
    assert result["email_bodies_opened"] is False
    assert result["drive_files_downloaded"] is False
    assert result["tokens_exposed"] is False


def test_workspace_briefing_drive_search_requires_explicit_topic(monkeypatch):
    monkeypatch.setattr(briefing.vault, "status", lambda user_id: [{"provider": "google", "services": ["drive"]}])
    seen = {}

    def drive(user_id, query, limit):
        seen.update(user_id=user_id, query=query, limit=limit)
        return {"files": [{"id": "f1", "name": "Pulsar plan"}]}

    monkeypatch.setattr(briefing.google, "drive_search", drive)
    result = briefing.build_workspace_briefing("user-2", drive_query="Pulsar Frequency House", limit=4)
    assert seen == {"user_id": "user-2", "query": "Pulsar Frequency House", "limit": 4}
    assert result["drive"]["searched"] is True
    assert result["drive"]["files"][0]["id"] == "f1"


def test_workspace_briefing_missing_connector_fails_closed(monkeypatch):
    monkeypatch.setattr(briefing.vault, "status", lambda user_id: [])
    with pytest.raises(PermissionError):
        briefing.build_workspace_briefing("user-3")


def test_workspace_briefing_is_deterministically_detected():
    assert briefing._wants_briefing("Aura, give me my daily briefing") is True
    assert briefing._wants_briefing("What needs my attention today?") is True
    assert briefing._wants_briefing("Search my email for Ana") is False
