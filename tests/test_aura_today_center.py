from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_artifacts_ui import AuraArtifactsUIMiddleware, router as workspace_ui_router
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_notifications import NotificationStore
from aura_music_studio.aura_tasks import AuraTaskStore, TaskCreateRequest
from aura_music_studio.aura_today_center import TODAY_SCRIPT, build_today_snapshot, router as today_router


def _user(accounts: AccountStore, email: str) -> str:
    return accounts.signup(email, "Today User", "very-long-test-password", "free").user_id


def test_today_snapshot_combines_private_workspace_tasks_notifications_and_pinned_project(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "today@example.com")
    chat = AuraChatStore(accounts)
    tasks = AuraTaskStore(chat)
    inbox = NotificationStore(chat)
    thread = chat.create_thread(user_id, "Studio day")
    chat.set_context(user_id, thread["id"], project_name="sparkles-and-glistens")
    tasks.create(user_id, thread["id"], TaskCreateRequest(title="Mix check", kind="reminder", prompt="Review mix", delay_minutes=60))
    inbox.create(user_id, kind="task", title="Research ready", body="Your result is ready", thread_id=thread["id"])

    seen = {}

    def fake_briefing(uid, **kwargs):
        seen["uid"] = uid
        seen.update(kwargs)
        return {
            "calendar": {"available": True, "events": [{"id": "event-1", "summary": "Session"}]},
            "gmail": {"available": True, "messages": [{"id": "m1", "subject": "Studio"}]},
            "drive": {"available": True, "searched": True, "files": [{"name": "notes.txt"}]},
            "read_only": True,
            "tokens_exposed": False,
            "email_bodies_opened": False,
            "drive_files_downloaded": False,
        }

    result = build_today_snapshot(
        user_id,
        thread_id=thread["id"],
        chat_store=chat,
        tasks=tasks,
        notifications=inbox,
        briefing_builder=fake_briefing,
    )
    assert result["pinned_project"] == "sparkles-and-glistens"
    assert seen["uid"] == user_id
    assert seen["drive_query"] == "sparkles-and-glistens"
    assert result["workspace"]["connected"] is True
    assert result["tasks"][0]["title"] == "Mix check"
    assert result["notifications"][0]["title"] == "Research ready"
    assert result["unread_notification_count"] == 1
    assert result["privacy"] == {
        "read_only_connected_services": True,
        "email_bodies_opened": False,
        "drive_bulk_scan": False,
        "drive_files_downloaded": False,
        "tokens_exposed": False,
        "project_writes": False,
    }


def test_today_snapshot_rejects_another_members_thread(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "today-a@example.com")
    user_b = _user(accounts, "today-b@example.com")
    chat = AuraChatStore(accounts)
    tasks = AuraTaskStore(chat)
    inbox = NotificationStore(chat)
    thread_b = chat.create_thread(user_b)

    with pytest.raises(KeyError):
        build_today_snapshot(
            user_a,
            thread_id=thread_b["id"],
            chat_store=chat,
            tasks=tasks,
            notifications=inbox,
            briefing_builder=lambda *_a, **_k: {},
        )


def test_today_still_works_without_google_connection(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "today-local@example.com")
    chat = AuraChatStore(accounts)
    tasks = AuraTaskStore(chat)
    inbox = NotificationStore(chat)
    thread = chat.create_thread(user_id)
    tasks.create(user_id, thread["id"], TaskCreateRequest(title="Local task", kind="reminder", prompt="Remember", delay_minutes=60))

    def disconnected(*_args, **_kwargs):
        raise PermissionError("Connect at least one Google service")

    result = build_today_snapshot(
        user_id,
        thread_id=thread["id"],
        chat_store=chat,
        tasks=tasks,
        notifications=inbox,
        briefing_builder=disconnected,
    )
    assert result["workspace"]["connected"] is False
    assert result["workspace"]["email_bodies_opened"] is False
    assert result["workspace"]["drive_files_downloaded"] is False
    assert result["tasks"][0]["title"] == "Local task"


def test_today_ui_is_mounted_and_keeps_read_only_disclosure():
    paths = {getattr(route, "path", None) for route in today_router.routes}
    assert "/aura-intelligence/api/today" in paths
    assert "/aura-intelligence/today-ui.js" in paths
    assert "Rhian Today" in TODAY_SCRIPT
    assert "Aura Today" not in TODAY_SCRIPT
    assert "does not open email bodies automatically" in TODAY_SCRIPT
    assert "bulk-scan Drive" in TODAY_SCRIPT
    assert "Do not send or modify anything" in TODAY_SCRIPT
    assert "data-aura-event-detail" in TODAY_SCRIPT
    assert "data-aura-event-prepare" in TODAY_SCRIPT
    assert "Do not modify the calendar" in TODAY_SCRIPT

    app = FastAPI()
    app.include_router(workspace_ui_router)

    @app.get("/aura-intelligence", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse('<html><body><div class="sideFoot"></div></body></html>')

    app.add_middleware(AuraArtifactsUIMiddleware)
    response = TestClient(app).get("/aura-intelligence")
    assert response.status_code == 200
    assert "/aura-intelligence/today-ui.js" in response.text
