from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_notifications import NotificationStore
from aura_music_studio.aura_notifications_ui import NOTIFICATIONS_SCRIPT, router as aura_notifications_ui_router


def _user(accounts: AccountStore, email: str) -> str:
    return accounts.signup(email, "Notification User", "very-long-test-password", "free").user_id


def test_notifications_are_private_readable_and_deletable(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "notification-a@example.com")
    user_b = _user(accounts, "notification-b@example.com")
    chat = AuraChatStore(accounts)
    inbox = NotificationStore(chat)
    thread_a = chat.create_thread(user_a)

    created = inbox.create(
        user_a,
        kind="task_completed",
        title="Research ready",
        body="Aura finished the scheduled research.",
        thread_id=thread_a["id"],
        resource_kind="task",
        resource_id="task-1",
    )
    assert created["unread"] is True
    assert inbox.unread_count(user_a) == 1
    assert inbox.unread_count(user_b) == 0
    assert [row["id"] for row in inbox.list(user_a)] == [created["id"]]
    assert inbox.list(user_b) == []

    with pytest.raises(KeyError):
        inbox.mark_read(user_b, created["id"])
    marked = inbox.mark_read(user_a, created["id"])
    assert marked["unread"] is False
    assert inbox.unread_count(user_a) == 0

    assert inbox.delete(user_b, created["id"]) is False
    assert inbox.delete(user_a, created["id"]) is True
    assert inbox.list(user_a) == []


def test_notification_thread_must_belong_to_recipient(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "notification-thread-a@example.com")
    user_b = _user(accounts, "notification-thread-b@example.com")
    chat = AuraChatStore(accounts)
    inbox = NotificationStore(chat)
    thread_b = chat.create_thread(user_b)

    with pytest.raises(KeyError):
        inbox.create(user_a, kind="task", title="No", body="No", thread_id=thread_b["id"])


def test_notifications_ui_links_back_to_originating_aura_thread_and_exposes_script_route():
    assert "/aura-intelligence/api" in NOTIFICATIONS_SCRIPT
    assert "auraNotificationsBadge" in NOTIFICATIONS_SCRIPT
    assert "openThread(thread)" in NOTIFICATIONS_SCRIPT
    assert "notifications/read-all" in NOTIFICATIONS_SCRIPT
    assert "data-notification-delete" in NOTIFICATIONS_SCRIPT
    paths = {getattr(route, "path", None) for route in aura_notifications_ui_router.routes}
    assert "/aura-intelligence/notifications-ui.js" in paths
