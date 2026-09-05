from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio import esp_product_workflows_schedule_hardening as _schedule_hardening  # noqa: F401
from aura_music_studio.esp_product_workflows import AnnouncementCreate, Chat9WorkflowStore


def _store_with_owner(tmp_path):
    accounts = AccountStore(tmp_path / "chat9-schedule.sqlite3")
    signup = accounts.signup("owner-schedule@example.com", "Schedule Owner", "a-very-secure-test-password", "free")
    owner = accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    esp = EspStore(accounts)
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,?,?,?,?,datetime('now'),'test-owner',datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,roles=excluded.roles,region=excluded.region,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,updated_at=excluded.updated_at""",
            (owner["id"], "owner", "owner", "", "Global"),
        )
    return owner, Chat9WorkflowStore(esp)


def _scheduled(**overrides) -> AnnouncementCreate:
    values = {
        "title": "Scheduled policy update",
        "body": "This announcement has a deterministic publication time.",
        "audience": "everyone",
        "status": "scheduled",
        "confirm_publish": True,
    }
    values.update(overrides)
    return AnnouncementCreate(**values)


def test_scheduled_announcement_requires_publish_at(tmp_path):
    owner, workflows = _store_with_owner(tmp_path)
    with pytest.raises(ValueError, match="publish_at is required"):
        workflows.create_announcement(_scheduled(), actor_user_id=owner["id"])


def test_scheduled_announcement_requires_timezone_aware_publish_at(tmp_path):
    owner, workflows = _store_with_owner(tmp_path)
    with pytest.raises(ValueError, match="explicit timezone offset"):
        workflows.create_announcement(
            _scheduled(publish_at="2026-09-06T09:00:00"),
            actor_user_id=owner["id"],
        )


def test_scheduled_announcement_rejects_expiry_before_publication(tmp_path):
    owner, workflows = _store_with_owner(tmp_path)
    with pytest.raises(ValueError, match="expires_at must be later"):
        workflows.create_announcement(
            _scheduled(
                publish_at="2026-09-06T09:00:00+01:00",
                expires_at="2026-09-06T08:59:59+01:00",
            ),
            actor_user_id=owner["id"],
        )


def test_scheduled_announcement_preserves_high_impact_confirmation(tmp_path):
    owner, workflows = _store_with_owner(tmp_path)
    with pytest.raises(PermissionError, match="high_impact_confirmation_required"):
        workflows.create_announcement(
            _scheduled(publish_at="2026-09-06T09:00:00+01:00", confirm_publish=False),
            actor_user_id=owner["id"],
        )


def test_valid_scheduled_announcement_is_normalized_and_persisted(tmp_path):
    owner, workflows = _store_with_owner(tmp_path)
    announcement = workflows.create_announcement(
        _scheduled(
            publish_at="2026-09-06T09:00:00+01:00",
            expires_at="2026-09-07T09:00:00+01:00",
        ),
        actor_user_id=owner["id"],
    )
    assert announcement["status"] == "scheduled"
    assert announcement["publish_at"] == "2026-09-06T08:00:00+00:00"
    assert announcement["expires_at"] == "2026-09-07T08:00:00+00:00"
