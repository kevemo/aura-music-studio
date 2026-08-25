from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aura_music_studio.esp_social_public_review import (
    CreateReviewLink,
    PublicFeedback,
    _token_hash,
    create_review_link,
    public_review_snapshot,
    revoke_review_link,
    submit_public_feedback,
)
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import PlatformVariant, SocialContent, SocialHouseStore


def _setup(tmp_path: Path, monkeypatch, user_id: str = "owner-a"):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    monkeypatch.setenv("AURA_SOCIAL_REVIEW_DB", str(tmp_path / "review.sqlite3"))
    token = set_current_user_id(user_id)
    try:
        store = SocialHouseStore()
        house = store.create_space("Client Brand")
        content = SocialContent(
            title="Launch Reel",
            status="pending_approval",
            approval_required=True,
            variants=[PlatformVariant(platform="instagram", content_type="reel", caption="Launch")],
        )
        store.add_content(house.id, content)
        return house.id, content.id
    finally:
        reset_current_user_id(token)


def test_public_review_token_is_hashed_at_rest_and_snapshot_is_scoped(tmp_path: Path, monkeypatch):
    space_id, content_id = _setup(tmp_path, monkeypatch)
    link = create_review_link("owner-a", CreateReviewLink(space_id=space_id, content_id=content_id, scopes=["view", "comment"], expires_hours=24))
    assert link["token"] not in link["id"]

    conn = sqlite3.connect(tmp_path / "review.sqlite3")
    row = conn.execute("SELECT token_hash,owner_user_id,space_id,content_id FROM social_review_links").fetchone()
    conn.close()
    assert row[0] == _token_hash(link["token"])
    assert link["token"] != row[0]
    assert row[1:] == ("owner-a", space_id, content_id)

    snapshot = public_review_snapshot(link["token"])
    assert snapshot["space"] == {"name": "Client Brand"}
    assert snapshot["content"]["id"] == content_id
    assert snapshot["external_publish_triggered"] is False


def test_public_comment_and_approval_never_request_external_publish(tmp_path: Path, monkeypatch):
    space_id, content_id = _setup(tmp_path, monkeypatch)
    link = create_review_link("owner-a", CreateReviewLink(space_id=space_id, content_id=content_id, scopes=["view", "comment", "approve"], expires_hours=24))

    comment = submit_public_feedback(link["token"], PublicFeedback(action="comment", reviewer_name="Client", note="Use a stronger hook"))
    assert comment["status"] == "pending_approval"
    assert comment["external_publish_triggered"] is False

    approved = submit_public_feedback(link["token"], PublicFeedback(action="approve", reviewer_name="Client", note="Approved"))
    assert approved["status"] == "approved"
    assert approved["external_publish_triggered"] is False

    token = set_current_user_id("owner-a")
    try:
        row = next(item for item in SocialHouseStore().load(space_id).content if item.id == content_id)
        assert row.status == "approved"
        assert row.variants[0].auto_publish is False
        assert row.variants[0].publish_state == "not_requested"
        assert any(value.startswith("Public reviewer: Client") for value in row.approved_by)
        assert "Public feedback by Client" in row.notes
    finally:
        reset_current_user_id(token)


def test_revoked_link_fails_and_other_owner_cannot_revoke(tmp_path: Path, monkeypatch):
    space_id, content_id = _setup(tmp_path, monkeypatch)
    link = create_review_link("owner-a", CreateReviewLink(space_id=space_id, content_id=content_id, scopes=["view"], expires_hours=24))
    assert revoke_review_link("owner-b", link["id"]) is False
    assert revoke_review_link("owner-a", link["id"]) is True
    with pytest.raises(PermissionError, match="revoked"):
        public_review_snapshot(link["token"])


def test_expired_review_link_fails_closed(tmp_path: Path, monkeypatch):
    space_id, content_id = _setup(tmp_path, monkeypatch)
    link = create_review_link("owner-a", CreateReviewLink(space_id=space_id, content_id=content_id, scopes=["view"], expires_hours=24))
    conn = sqlite3.connect(tmp_path / "review.sqlite3")
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    conn.execute("UPDATE social_review_links SET expires_at=? WHERE id=?", (expired, link["id"]))
    conn.commit()
    conn.close()
    with pytest.raises(PermissionError, match="expired"):
        public_review_snapshot(link["token"])


def test_link_without_approve_scope_cannot_approve(tmp_path: Path, monkeypatch):
    space_id, content_id = _setup(tmp_path, monkeypatch)
    link = create_review_link("owner-a", CreateReviewLink(space_id=space_id, content_id=content_id, scopes=["view", "comment"], expires_hours=24))
    with pytest.raises(PermissionError, match="approve"):
        submit_public_feedback(link["token"], PublicFeedback(action="approve", reviewer_name="Client"))
