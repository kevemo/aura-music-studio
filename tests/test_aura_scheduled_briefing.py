from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio import aura_scheduled_briefing as scheduled
from aura_music_studio.aura_tasks import TaskCreateRequest


def test_scheduled_briefing_extension_adds_read_only_task_kind():
    scheduled.install_aura_scheduled_briefing()
    body = TaskCreateRequest(
        title="Daily workspace brief",
        kind="workspace_briefing",
        prompt="general",
        delay_minutes=60,
        interval_minutes=1440,
    )
    assert body.kind == "workspace_briefing"


def test_scheduled_workspace_briefing_uses_bounded_connector_result(monkeypatch):
    captured = {}

    def fake_brief(user_id, **kwargs):
        captured.update(user_id=user_id, **kwargs)
        return {
            "calendar": {"events": [{"summary": "Creator review"}]},
            "gmail": {"messages": [{"subject": "Update", "snippet": "Ready"}]},
            "drive": {"searched": False, "files": []},
            "email_bodies_opened": False,
            "drive_files_downloaded": False,
            "tokens_exposed": False,
            "read_only": True,
        }

    class FakeModel:
        def complete(self, messages, temperature=0.2):
            joined = " ".join(str(row.get("content") or "") for row in messages)
            assert "email_bodies_opened" in joined
            assert "drive_files_downloaded" in joined
            return SimpleNamespace(text="You have one creator review and one email update.")

    monkeypatch.setattr(scheduled, "build_workspace_briefing", fake_brief)
    monkeypatch.setattr(scheduled, "AuraModelClient", FakeModel)
    result = scheduled.scheduled_workspace_briefing(
        {"user_id": "member-1", "prompt": "general", "kind": "workspace_briefing"}
    )
    assert result.startswith("☀ Aura workspace briefing")
    assert "creator review" in result.lower()
    assert captured["user_id"] == "member-1"
    assert captured["hours"] == 24
    assert captured["drive_query"] is None
    assert captured["limit"] == 8


def test_weekly_briefing_uses_seven_day_horizon(monkeypatch):
    seen = {}

    def fake_brief(user_id, **kwargs):
        seen.update(kwargs)
        return {"read_only": True, "calendar": {}, "gmail": {}, "drive": {}}

    class FakeModel:
        def complete(self, messages, temperature=0.2):
            return SimpleNamespace(text="Weekly summary")

    monkeypatch.setattr(scheduled, "build_workspace_briefing", fake_brief)
    monkeypatch.setattr(scheduled, "AuraModelClient", FakeModel)
    scheduled.scheduled_workspace_briefing(
        {"user_id": "member-2", "prompt": "weekly briefing for campaign-alpha", "kind": "workspace_briefing"}
    )
    assert seen["hours"] == 168
    assert seen["drive_query"] == "weekly briefing for campaign-alpha"


def test_drive_topic_does_not_trigger_bulk_scan_for_generic_briefings():
    assert scheduled._drive_topic("general") is None
    assert scheduled._drive_topic("daily briefing") is None
    assert scheduled._drive_topic("workspace briefing") is None
    assert scheduled._drive_topic("campaign-alpha") == "campaign-alpha"


def test_scheduled_briefing_detection_is_specific():
    assert scheduled._is_briefing_request("Schedule my daily briefing") is True
    assert scheduled._is_briefing_request("Give me a weekly workspace briefing") is True
    assert scheduled._is_briefing_request("Remind me to drink water") is False
