from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.shared_sky_live_community import LiveCommunityStore
from aura_music_studio.shared_sky_live_events import (
    EventPublicationRequest,
    LiveEventsStore,
    ReminderPreferenceRequest,
)


class FakeNotifications:
    def __init__(self, *, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def create(self, user_id: str, **kwargs):
        if self.fail:
            raise RuntimeError("notification backend unavailable")
        self.calls.append((user_id, kwargs))
        return {"id": f"notice-{len(self.calls)}"}


def _insert_user(con: sqlite3.Connection, user_id: str, display_name: str) -> None:
    con.execute(
        """INSERT INTO users
           (id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at)
           VALUES(?,?,?,?,?,'active','free','free','not_required',?)""",
        (
            user_id,
            f"{user_id}@example.invalid",
            display_name,
            "00",
            "00",
            datetime.now(timezone.utc).isoformat(),
        ),
    )


@pytest.fixture()
def event_env(tmp_path: Path):
    db = tmp_path / "shared-sky-events.sqlite3"
    AccountStore(db)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(db) as con:
        _insert_user(con, "creator-1", "Creator One")
        _insert_user(con, "viewer-1", "Viewer One")
        _insert_user(con, "viewer-2", "Viewer Two")
        con.executescript(
            """
            CREATE TABLE shared_sky_schedules (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                destination_ids_json TEXT NOT NULL DEFAULT '[]',
                mode TEXT NOT NULL DEFAULT 'live',
                state TEXT NOT NULL DEFAULT 'scheduled',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        start = (now + timedelta(minutes=10)).isoformat()
        con.execute(
            """INSERT INTO shared_sky_schedules
               (id,user_id,project_id,title,start_at,mode,state,created_at,updated_at)
               VALUES('schedule-1','creator-1','project-private','Songwriting LIVE',?,'live','scheduled',?,?)""",
            (start, now.isoformat(), now.isoformat()),
        )
    community = LiveCommunityStore(db)
    notifier = FakeNotifications()
    store = LiveEventsStore(community, notifier)
    return store, community, notifier, now


def _publish(store: LiveEventsStore, **overrides):
    values = {
        "description": "Writing an original song live.",
        "category": "music",
        "tags": ["original", "songwriting"],
        "language": "en",
        "visibility": "public",
        "suitability": "general",
        "thumbnail_url": "https://images.example.invalid/songwriting.jpg",
    }
    values.update(overrides)
    return store.publish("schedule-1", "creator-1", EventPublicationRequest(**values))


def test_private_studio_schedule_is_not_exposed_until_creator_publishes(event_env):
    store, _community, _notifier, _now_value = event_env
    assert store.list_events(None) == []
    with pytest.raises(PermissionError):
        store.event("schedule-1", None, direct=True)

    event = _publish(store)
    assert event["schedule_id"] == "schedule-1"
    assert event["creator_display_name"] == "Creator One"
    assert event["category"] == "music"
    assert "project_id" not in event
    assert "destination_ids_json" not in event
    assert [item["schedule_id"] for item in store.list_events(None)] == ["schedule-1"]


def test_publication_is_creator_authoritative_versioned_and_timezone_safe(event_env):
    store, _community, _notifier, _now_value = event_env
    with pytest.raises(PermissionError):
        store.publish("schedule-1", "viewer-1", EventPublicationRequest())

    first = _publish(store)
    assert first["version"] == 1
    second = _publish(store, description="Updated event details", expected_version=1)
    assert second["version"] == 2
    with pytest.raises(RuntimeError):
        _publish(store, description="Stale write", expected_version=1)

    with store._connect() as con:
        con.execute("UPDATE shared_sky_schedules SET start_at='2026-09-05T12:00:00' WHERE id='schedule-1'")
    with pytest.raises(ValueError):
        _publish(store, expected_version=2)


def test_unlisted_and_followers_event_visibility_is_enforced_server_side(event_env):
    store, community, _notifier, _now_value = event_env
    _publish(store, visibility="unlisted")
    assert store.list_events(None) == []
    assert store.event("schedule-1", None, direct=True)["visibility"] == "unlisted"

    _publish(store, visibility="followers", expected_version=1)
    assert store.list_events("viewer-1") == []
    community.follow("viewer-1", "creator-1", True, False)
    assert [item["schedule_id"] for item in store.list_events("viewer-1")] == ["schedule-1"]

    with community._connect() as con:
        con.execute(
            "INSERT INTO shared_sky_blocks(blocker_user_id,blocked_user_id,created_at,reason) VALUES(?,?,?,?)",
            ("creator-1", "viewer-1", datetime.now(timezone.utc).isoformat(), "creator block"),
        )
    assert store.list_events("viewer-1") == []
    with pytest.raises(PermissionError):
        store.event("schedule-1", "viewer-1", direct=True)


def test_explicit_reminder_emits_once_when_due_and_uses_in_app_notification(event_env):
    store, _community, notifier, base = event_env
    _publish(store)
    reminder = store.set_reminder(
        "schedule-1",
        "viewer-1",
        ReminderPreferenceRequest(enabled=True, lead_minutes=15),
    )
    assert reminder["enabled"] is True and reminder["lead_minutes"] == 15

    result = store.emit_due_reminders(now=base + timedelta(minutes=1))
    assert result == {"checked": 1, "sent": 1, "skipped": 0, "failed": 0}
    assert len(notifier.calls) == 1
    user_id, payload = notifier.calls[0]
    assert user_id == "viewer-1"
    assert payload["kind"] == "shared_sky_schedule_reminder"
    assert payload["resource_kind"] == "shared_sky_schedule"
    assert payload["resource_id"] == "schedule-1"

    duplicate = store.emit_due_reminders(now=base + timedelta(minutes=2))
    assert duplicate["sent"] == 0 and duplicate["skipped"] == 1
    assert len(notifier.calls) == 1


def test_disabled_or_unpublished_reminder_does_not_emit(event_env):
    store, _community, notifier, base = event_env
    _publish(store)
    store.set_reminder(
        "schedule-1", "viewer-1", ReminderPreferenceRequest(enabled=False, lead_minutes=15)
    )
    assert store.emit_due_reminders(now=base + timedelta(minutes=1))["checked"] == 0
    assert notifier.calls == []

    store.set_reminder(
        "schedule-1", "viewer-1", ReminderPreferenceRequest(enabled=True, lead_minutes=15)
    )
    assert store.unpublish("schedule-1", "creator-1") is True
    assert store.list_events("viewer-1") == []
    assert store.reminder("schedule-1", "viewer-1")["enabled"] is False
    assert store.emit_due_reminders(now=base + timedelta(minutes=2))["checked"] == 0


def test_failed_notification_is_retryable_after_claim_staleness(event_env):
    store, _community, _notifier, base = event_env
    _publish(store)
    store.set_reminder(
        "schedule-1", "viewer-1", ReminderPreferenceRequest(enabled=True, lead_minutes=15)
    )
    failing = FakeNotifications(fail=True)
    store.notifier = failing
    first = store.emit_due_reminders(now=base + timedelta(minutes=1))
    assert first["failed"] == 1

    succeeding = FakeNotifications()
    store.notifier = succeeding
    second = store.emit_due_reminders(now=base + timedelta(minutes=2))
    assert second["sent"] == 1
    assert len(succeeding.calls) == 1


def test_member_only_publication_is_not_discoverable_to_anonymous_viewer(event_env):
    store, _community, _notifier, _now_value = event_env
    _publish(store, visibility="members")
    assert store.list_events(None) == []
    assert len(store.list_events("viewer-2")) == 1


def test_restricted_publication_fails_closed_without_suitability_assertion(event_env):
    store, _community, _notifier, _now_value = event_env
    _publish(store, visibility="restricted")
    assert store.list_events(None) == []
    assert store.list_events("viewer-1") == []
    with pytest.raises(PermissionError):
        store.event("schedule-1", "viewer-1", direct=True)
