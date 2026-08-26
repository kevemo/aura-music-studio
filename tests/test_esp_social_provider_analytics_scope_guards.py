from pathlib import Path

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_social_provider_analytics import (
    ProviderAnalyticsError,
    ProviderAnalyticsService,
    _YOUTUBE_SCOPE,
)
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import SocialConnection, SocialHouseStore


def _creator(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db_path))
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    accounts = AccountStore(db_path)
    signup = accounts.signup(
        "scope-guard@example.test",
        "Scope Guard Creator",
        "very-secure-test-password",
        "free",
    )
    return accounts.decide_membership(signup.approval_token, "approve", "Test Owner")


def test_youtube_connection_without_readonly_scope_is_blocked_before_network(tmp_path, monkeypatch):
    user = _creator(tmp_path, monkeypatch)
    token = set_current_user_id(user["id"])
    try:
        store = SocialHouseStore()
        house = store.create_space("Scope Guard House")
        house = store.connect_placeholder(
            house.id,
            SocialConnection(
                platform="youtube",
                account_label="Upload Only Channel",
                account_external_id="channel_upload_only",
                state="connected",
                token_secret_ref="social-token://upload-only",
                metadata={
                    "oauth_scopes": ["https://www.googleapis.com/auth/youtube.upload"]
                },
            ),
        )
        monkeypatch.setattr(
            "aura_music_studio.esp_social_provider_analytics.requests.get",
            lambda *args, **kwargs: pytest.fail("Provider network must not be called without the read scope"),
        )
        service = ProviderAnalyticsService()
        capability = service.capabilities(house)["youtube"]
        assert capability["ready"] is False
        assert capability["status"] == "blocked"
        assert capability["required_scopes"] == [_YOUTUBE_SCOPE]
        with pytest.raises(ProviderAnalyticsError):
            service.sync_youtube(user_id=user["id"], house=house)
    finally:
        reset_current_user_id(token)


def test_tiktok_publish_scope_never_implies_video_list_scope(tmp_path, monkeypatch):
    user = _creator(tmp_path, monkeypatch)
    token = set_current_user_id(user["id"])
    try:
        store = SocialHouseStore()
        house = store.create_space("TikTok Scope Guard")
        house = store.connect_placeholder(
            house.id,
            SocialConnection(
                platform="tiktok",
                account_label="Publishing TikTok",
                state="connected",
                token_secret_ref="social-token://tiktok-publish",
                metadata={"oauth_scopes": ["user.info.basic", "video.publish"]},
            ),
        )
        capability = ProviderAnalyticsService().capabilities(house)["tiktok"]
        assert capability["ready"] is False
        assert capability["status"] == "blocked"
        assert capability["required_scopes"] == ["video.list"]
    finally:
        reset_current_user_id(token)
