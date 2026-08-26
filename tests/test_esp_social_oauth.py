from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from aura_music_studio.esp_social_oauth import (
    SocialOAuthService,
    SocialOAuthVault,
    resolve_social_oauth_token,
)
from aura_music_studio.esp_social_provider_adapters import InstagramGraphAdapter
from aura_music_studio.esp_social_secret_refs import resolve_social_token, valid_social_token_ref
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import SocialConnection, SocialHouseStore


def configure_oauth(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_DB_PATH", str(tmp_path / "oauth.sqlite3"))
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("TIKTOK_OAUTH_CLIENT_KEY", "test-client-key")
    monkeypatch.setenv("TIKTOK_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TIKTOK_OAUTH_REDIRECT_URI", "https://example.test/command-center/api/social/oauth/tiktok/callback")
    monkeypatch.setenv("INSTAGRAM_OAUTH_APP_ID", "ig-app")
    monkeypatch.setenv("INSTAGRAM_OAUTH_APP_SECRET", "ig-secret")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID", "yt-client")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET", "yt-secret")


def test_vault_encrypts_tokens_and_isolates_member_credentials(tmp_path: Path, monkeypatch):
    configure_oauth(tmp_path, monkeypatch)
    vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
    vault.save(
        user_id="creator-one",
        credential_id="socialcred_test",
        provider="tiktok",
        token={"access_token": "super-secret-access-token", "expires_at": "2099-01-01T00:00:00+00:00"},
        scopes=["video.publish"],
        account_external_id="open_1",
        account_label="Creator One",
    )

    assert b"super-secret-access-token" not in (tmp_path / "oauth.sqlite3").read_bytes()
    assert vault.access_token("creator-one", "socialcred_test") == "super-secret-access-token"
    with pytest.raises(LookupError):
        vault.access_token("creator-two", "socialcred_test")


def test_social_oauth_reference_uses_active_tenant_context(tmp_path: Path, monkeypatch):
    configure_oauth(tmp_path, monkeypatch)
    vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
    vault.save(
        user_id="tenant-a",
        credential_id="socialcred_a",
        provider="youtube",
        token={"access_token": "tenant-a-token", "expires_at": "2099-01-01T00:00:00+00:00"},
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
        account_external_id="channel-a",
        account_label="Channel A",
    )
    assert valid_social_token_ref("social-oauth://socialcred_a") is True
    assert valid_social_token_ref("env://PATH") is False

    token = set_current_user_id("tenant-a")
    try:
        assert resolve_social_oauth_token("social-oauth://socialcred_a") == "tenant-a-token"
        assert resolve_social_token("social-oauth://socialcred_a") == "tenant-a-token"
    finally:
        reset_current_user_id(token)

    token = set_current_user_id("tenant-b")
    try:
        with pytest.raises(LookupError):
            resolve_social_token("social-oauth://socialcred_a")
    finally:
        reset_current_user_id(token)


def test_tiktok_authorization_url_has_state_scopes_and_no_client_secret(tmp_path: Path, monkeypatch):
    configure_oauth(tmp_path, monkeypatch)
    token = set_current_user_id("oauth-user")
    try:
        store = SocialHouseStore()
        house = store.create_space("OAuth House")
        vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
        service = SocialOAuthService(vault)
        url = service.authorization_url(user_id="oauth-user", space_id=house.id, provider="tiktok")
    finally:
        reset_current_user_id(token)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.tiktok.com"
    assert query["client_key"] == ["test-client-key"]
    assert "video.publish" in query["scope"][0]
    assert query["state"][0]
    assert "test-client-secret" not in url

    pending = vault.consume_state("oauth-user", "tiktok", query["state"][0])
    assert pending["space_id"] == house.id
    with pytest.raises(PermissionError):
        vault.consume_state("oauth-user", "tiktok", query["state"][0])


def test_instagram_direct_oauth_connection_uses_graph_instagram_host(monkeypatch):
    monkeypatch.setenv("AURA_INSTAGRAM_GRAPH_VERSION", "v26.0")
    direct = SocialConnection(
        platform="instagram",
        state="connected",
        account_external_id="ig_123",
        metadata={"oauth_flow": "instagram_login"},
    )
    legacy = SocialConnection(
        platform="instagram",
        state="connected",
        account_external_id="ig_456",
        metadata={"oauth_flow": "facebook_login"},
    )
    assert InstagramGraphAdapter._base(direct) == "https://graph.instagram.com/v26.0"
    assert InstagramGraphAdapter._base(legacy) == "https://graph.facebook.com/v26.0"
