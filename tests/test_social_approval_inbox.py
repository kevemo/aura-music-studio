from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.esp_social_approval_inbox import approval_queue, review_content
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import PlatformVariant, SocialContent, SocialHouseStore


def _with_user(tmp_path: Path, monkeypatch, user_id: str):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    return set_current_user_id(user_id)


def test_approval_queue_only_returns_pending_items(tmp_path: Path, monkeypatch):
    token = _with_user(tmp_path, monkeypatch, "reviewer")
    try:
        store = SocialHouseStore()
        house = store.create_space("Campaign")
        waiting = SocialContent(
            title="Waiting",
            status="pending_approval",
            approval_required=True,
            variants=[PlatformVariant(platform="tiktok", content_type="video")],
        )
        approved = SocialContent(
            title="Already approved",
            status="approved",
            approval_required=True,
            variants=[PlatformVariant(platform="instagram", content_type="reel")],
        )
        no_gate = SocialContent(
            title="No approval gate",
            status="pending_approval",
            approval_required=False,
            variants=[PlatformVariant(platform="youtube", content_type="short")],
        )
        store.add_content(house.id, waiting)
        store.add_content(house.id, approved)
        store.add_content(house.id, no_gate)
        rows = approval_queue(store)
        assert [row["content"]["id"] for row in rows] == [waiting.id]
    finally:
        reset_current_user_id(token)


def test_approval_decisions_update_existing_social_workflow_without_publishing(tmp_path: Path, monkeypatch):
    token = _with_user(tmp_path, monkeypatch, "reviewer")
    try:
        store = SocialHouseStore()
        house = store.create_space("Campaign")
        content = SocialContent(
            title="Launch",
            status="pending_approval",
            approval_required=True,
            variants=[
                PlatformVariant(
                    platform="tiktok",
                    content_type="video",
                    scheduled_at="2026-09-01T18:00:00+01:00",
                    auto_publish=False,
                )
            ],
        )
        store.add_content(house.id, content)

        changed = review_content("owner-1", house.id, content.id, "request_changes", "Tighten the hook", store=store)
        row = next(item for item in changed.content if item.id == content.id)
        assert row.status == "in_production"
        assert row.approved_by == []
        assert "Changes requested by owner-1" in row.notes
        assert row.variants[0].auto_publish is False

        changed = review_content("owner-1", house.id, content.id, "approve", "Ready", store=store)
        row = next(item for item in changed.content if item.id == content.id)
        assert row.status == "approved"
        assert row.approved_by == ["owner-1"]
        assert row.approval_at
        assert row.variants[0].publish_state == "not_requested"
        assert any(event.action == "content_approved" and event.actor == "owner-1" for event in changed.activity)
    finally:
        reset_current_user_id(token)


def test_non_approval_content_cannot_be_reviewed_from_inbox(tmp_path: Path, monkeypatch):
    token = _with_user(tmp_path, monkeypatch, "reviewer")
    try:
        store = SocialHouseStore()
        house = store.create_space("Campaign")
        content = SocialContent(
            title="Draft",
            status="draft",
            approval_required=False,
            variants=[PlatformVariant(platform="threads", content_type="post")],
        )
        store.add_content(house.id, content)
        with pytest.raises(ValueError, match="does not require approval"):
            review_content("owner-1", house.id, content.id, "approve", store=store)
    finally:
        reset_current_user_id(token)
