from __future__ import annotations

import pytest

from aura_music_studio.esp_social_media_library import (
    AddMediaRequest,
    ApproveMediaRequest,
    AttachMediaRequest,
    SocialMediaLibraryStore,
    _safe_artifact_ref,
    router,
)
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import PlatformVariant, SocialContent, SocialHouseStore


def _social_root(monkeypatch, tmp_path, user_id="member-a"):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    token = set_current_user_id(user_id)
    store = SocialHouseStore()
    house = store.create_space("Test Brand")
    return token, store, house


def test_external_media_is_quarantined_until_separate_human_approval(monkeypatch, tmp_path):
    token, social, house = _social_root(monkeypatch, tmp_path)
    try:
        library = SocialMediaLibraryStore(social)
        asset = library.add_asset(
            house.id,
            AddMediaRequest(
                name="Client poster",
                kind="image",
                source_type="external_url",
                source_ref="https://example.com/client-poster.jpg",
                rights_confirmed=True,
                rights_note="Client says they own the supplied artwork.",
            ),
            actor="member-a",
        )
        assert asset["approval_state"] == "quarantined"
        assert asset["rights_confirmed"] is True
        with pytest.raises(ValueError, match="Rights confirmation is required"):
            library.approve_asset(
                house.id,
                asset["id"],
                ApproveMediaRequest(rights_confirmed=False),
                actor="member-a",
            )
        approved = library.approve_asset(
            house.id,
            asset["id"],
            ApproveMediaRequest(rights_confirmed=True, rights_note="Rights checked by Social House owner."),
            actor="member-a",
        )
        assert approved["approval_state"] == "approved"
    finally:
        reset_current_user_id(token)


def test_only_approved_media_can_attach_and_attachment_invalidates_prior_content_approval(monkeypatch, tmp_path):
    token, social, house = _social_root(monkeypatch, tmp_path)
    try:
        content = SocialContent(
            title="Approved draft",
            status="approved",
            approval_required=True,
            approved_by=["Reviewer"],
            approval_at="2026-08-26T00:00:00+00:00",
            variants=[PlatformVariant(platform="tiktok", content_type="video", caption="Launch", auto_publish=False)],
        )
        social.add_content(house.id, content)
        library = SocialMediaLibraryStore(social)
        asset = library.add_asset(
            house.id,
            AddMediaRequest(
                name="Launch image",
                kind="image",
                source_type="external_url",
                source_ref="https://example.com/launch.jpg",
                rights_confirmed=True,
            ),
            actor="member-a",
        )
        with pytest.raises(PermissionError, match="Only approved"):
            library.attach(
                house.id,
                asset["id"],
                AttachMediaRequest(content_id=content.id, platform="tiktok"),
                actor="member-a",
            )
        library.approve_asset(
            house.id,
            asset["id"],
            ApproveMediaRequest(rights_confirmed=True),
            actor="member-a",
        )
        result = library.attach(
            house.id,
            asset["id"],
            AttachMediaRequest(content_id=content.id, platform="tiktok"),
            actor="member-a",
        )
        assert result["external_publish_triggered"] is False
        assert result["approval_status"] == "pending_approval"
        reloaded = social.load(house.id)
        post = next(item for item in reloaded.content if item.id == content.id)
        assert post.approved_by == []
        assert post.approval_at is None
        assert f"library:{asset['id']}" in post.variants[0].media_refs
    finally:
        reset_current_user_id(token)


def test_media_library_remains_tenant_isolated(monkeypatch, tmp_path):
    token, social, house = _social_root(monkeypatch, tmp_path, "member-a")
    try:
        SocialMediaLibraryStore(social).create_folder(house.id, "Campaign")
    finally:
        reset_current_user_id(token)

    other_token = set_current_user_id("member-b")
    try:
        other = SocialMediaLibraryStore(SocialHouseStore())
        with pytest.raises(FileNotFoundError):
            other.state(house.id)
    finally:
        reset_current_user_id(other_token)


def test_application_refs_cannot_be_filesystem_paths():
    assert _safe_artifact_ref("artifact_abc123") == "artifact_abc123"
    with pytest.raises(ValueError, match="filesystem path"):
        _safe_artifact_ref("../../private/file.png")


def test_media_library_routes_cover_quarantine_approval_and_attachment():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/social/media-library" in paths
    assert "/command-center/api/social/media-library" in paths
    assert "/command-center/api/social/media-library/{space_id}" in paths
    assert "/command-center/api/social/media-library/{space_id}/assets" in paths
    assert "/command-center/api/social/media-library/{space_id}/assets/{asset_id}/approve" in paths
    assert "/command-center/api/social/media-library/{space_id}/assets/{asset_id}/attach" in paths
    assert "/command-center/api/social/media-library/{space_id}/assets/{asset_id}/detach" in paths
