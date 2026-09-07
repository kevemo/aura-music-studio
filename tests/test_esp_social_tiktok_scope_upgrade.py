from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.esp_social_oauth import SocialOAuthService, SocialOAuthVault
from aura_music_studio.esp_social_tiktok_scope_upgrade import (
    video_list_authorization_url,
    video_list_status,
)
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import SocialConnection, SocialHouseStore
from aura_music_studio.social_management_api import router as social_management_router


def configure(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_DB_PATH", str(tmp_path / "oauth.sqlite3"))
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("TIKTOK_OAUTH_CLIENT_KEY", "test-client-key")
    monkeypatch.setenv("TIKTOK_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "TIKTOK_OAUTH_REDIRECT_URI",
        "https://example.test/command-center/api/social/oauth/tiktok/callback",
    )
    monkeypatch.delenv("TIKTOK_OAUTH_VIDEO_LIST_ENABLED", raising=False)


def connected_tiktok(tmp_path: Path, monkeypatch, *, user_id: str = "creator-one"):
    configure(tmp_path, monkeypatch)
    token = set_current_user_id(user_id)
    try:
        store = SocialHouseStore()
        house = store.create_space("TikTok Analytics House")
        connection = SocialConnection(
            id="conn_tiktok_upgrade",
            platform="tiktok",
            account_label="TikTok Creator",
            account_external_id="open_creator_123",
            state="connected",
            supports_auto_publish=True,
            token_secret_ref="social-oauth://socialcred_tiktok_upgrade",
            metadata={
                "publishing_adapter": "tiktok_content_posting",
                "publishing_adapter_active": True,
                "oauth_provider": "tiktok",
                "oauth_scopes": ["user.info.basic", "video.publish"],
                "oauth_verified": True,
            },
        )
        store.connect_placeholder(house.id, connection)
        vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
        vault.save(
            user_id=user_id,
            credential_id="socialcred_tiktok_upgrade",
            provider="tiktok",
            token={
                "access_token": "existing-publishing-token",
                "refresh_token": "existing-refresh-token",
                "expires_at": "2099-01-01T00:00:00+00:00",
            },
            scopes=["user.info.basic", "video.publish"],
            account_external_id="open_creator_123",
            account_label="TikTok Creator",
        )
        return store, house, connection, vault
    finally:
        reset_current_user_id(token)


def _state_count(vault: SocialOAuthVault) -> int:
    import sqlite3

    with sqlite3.connect(vault.path) as con:
        return int(con.execute("SELECT COUNT(*) FROM esp_social_oauth_state").fetchone()[0])


def test_video_list_upgrade_is_disabled_until_app_scope_is_explicitly_enabled(tmp_path: Path, monkeypatch):
    _store, house, connection, vault = connected_tiktok(tmp_path, monkeypatch)
    token = set_current_user_id("creator-one")
    try:
        status = video_list_status("creator-one", house.id, connection.id)
        assert status["app_scope_enabled"] is False
        assert status["granted"] is False
        assert status["ready_to_request"] is False
        assert status["tokens_exposed"] is False
        with pytest.raises(RuntimeError, match="disabled until ESP confirms"):
            video_list_authorization_url("creator-one", house.id, connection.id)
    finally:
        reset_current_user_id(token)
    assert _state_count(vault) == 0


def test_video_list_upgrade_reuses_existing_connection_and_credential_ids(tmp_path: Path, monkeypatch):
    _store, house, connection, vault = connected_tiktok(tmp_path, monkeypatch)
    monkeypatch.setenv("TIKTOK_OAUTH_VIDEO_LIST_ENABLED", "true")
    token = set_current_user_id("creator-one")
    try:
        url = video_list_authorization_url("creator-one", house.id, connection.id)
        query = parse_qs(urlparse(url).query)
        assert urlparse(url).netloc == "www.tiktok.com"
        assert query["client_key"] == ["test-client-key"]
        requested = set(query["scope"][0].split(","))
        assert requested == {"user.info.basic", "video.publish", "video.list"}
        assert "test-client-secret" not in url
        pending = vault.consume_state("creator-one", "tiktok", query["state"][0])
        assert pending["space_id"] == house.id
        assert pending["connection_id"] == connection.id
        assert pending["credential_id"] == "socialcred_tiktok_upgrade"
        assert set(pending["scopes"]) == requested
        with pytest.raises(PermissionError):
            vault.consume_state("creator-one", "tiktok", query["state"][0])
    finally:
        reset_current_user_id(token)


def test_incomplete_tiktok_consent_preserves_existing_publishing_credential(tmp_path: Path, monkeypatch):
    store, house, connection, vault = connected_tiktok(tmp_path, monkeypatch)
    monkeypatch.setenv("TIKTOK_OAUTH_VIDEO_LIST_ENABLED", "true")

    class TokenResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "new-but-incomplete-token",
                "refresh_token": "new-refresh",
                "expires_in": 86400,
                "open_id": "open_creator_123",
                "scope": "user.info.basic,video.publish",
            }

    class UserResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"user": {"open_id": "open_creator_123", "display_name": "TikTok Creator"}}}

    monkeypatch.setattr("aura_music_studio.esp_social_oauth.requests.post", lambda *a, **k: TokenResponse())
    monkeypatch.setattr("aura_music_studio.esp_social_oauth.requests.get", lambda *a, **k: UserResponse())

    token = set_current_user_id("creator-one")
    try:
        url = video_list_authorization_url("creator-one", house.id, connection.id)
        state = parse_qs(urlparse(url).query)["state"][0]
        service = SocialOAuthService(vault)
        with pytest.raises(PermissionError, match="video.list"):
            service.complete(user_id="creator-one", provider="tiktok", state=state, code="code")
        record = vault.load("creator-one", "socialcred_tiktok_upgrade")
        saved_house = store.load(house.id)
    finally:
        reset_current_user_id(token)

    assert record["token"]["access_token"] == "existing-publishing-token"
    assert "video.list" not in record["scopes"]
    saved = next(item for item in saved_house.connections if item.id == connection.id)
    assert saved.state == "connected"
    assert saved.supports_auto_publish is True
    assert "video.list" not in saved.metadata.get("oauth_scopes", [])


def test_successful_tiktok_consent_atomically_upgrades_same_connection(tmp_path: Path, monkeypatch):
    store, house, connection, vault = connected_tiktok(tmp_path, monkeypatch)
    monkeypatch.setenv("TIKTOK_OAUTH_VIDEO_LIST_ENABLED", "true")

    class TokenResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "upgraded-token",
                "refresh_token": "upgraded-refresh",
                "expires_in": 86400,
                "open_id": "open_creator_123",
                "scope": "user.info.basic,video.publish,video.list",
            }

    class UserResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"user": {"open_id": "open_creator_123", "display_name": "TikTok Creator"}}}

    monkeypatch.setattr("aura_music_studio.esp_social_oauth.requests.post", lambda *a, **k: TokenResponse())
    monkeypatch.setattr("aura_music_studio.esp_social_oauth.requests.get", lambda *a, **k: UserResponse())

    token = set_current_user_id("creator-one")
    try:
        url = video_list_authorization_url("creator-one", house.id, connection.id)
        state = parse_qs(urlparse(url).query)["state"][0]
        upgraded = SocialOAuthService(vault).complete(
            user_id="creator-one", provider="tiktok", state=state, code="code"
        )
        record = vault.load("creator-one", "socialcred_tiktok_upgrade")
        saved_house = store.load(house.id)
        status = video_list_status("creator-one", house.id, connection.id)
    finally:
        reset_current_user_id(token)

    assert upgraded.id == connection.id
    assert upgraded.token_secret_ref == "social-oauth://socialcred_tiktok_upgrade"
    assert record["token"]["access_token"] == "upgraded-token"
    assert "video.list" in record["scopes"]
    same_id = [item for item in saved_house.connections if item.id == connection.id]
    assert len(same_id) == 1
    assert "video.list" in same_id[0].metadata.get("oauth_scopes", [])
    assert status["granted"] is True
    assert status["ready_to_request"] is False


def test_scope_upgrade_rejects_non_member_oauth_and_account_mismatch(tmp_path: Path, monkeypatch):
    store, house, connection, vault = connected_tiktok(tmp_path, monkeypatch)
    monkeypatch.setenv("TIKTOK_OAUTH_VIDEO_LIST_ENABLED", "true")
    token = set_current_user_id("creator-one")
    try:
        managed = SocialConnection(
            id="conn_managed",
            platform="tiktok",
            account_label="Managed TikTok",
            account_external_id="open_managed",
            state="connected",
            supports_auto_publish=True,
            token_secret_ref="social-token://managed-tiktok",
            metadata={"publishing_adapter": "tiktok_content_posting"},
        )
        store.connect_placeholder(house.id, managed)
        with pytest.raises(PermissionError, match="member-authorised"):
            video_list_authorization_url("creator-one", house.id, managed.id)

        record = vault.load("creator-one", "socialcred_tiktok_upgrade")
        vault.save(
            user_id="creator-one",
            credential_id="socialcred_tiktok_upgrade",
            provider="tiktok",
            token=record["token"],
            scopes=record["scopes"],
            account_external_id="different_open_id",
            account_label="Wrong TikTok",
        )
        with pytest.raises(PermissionError, match="does not match"):
            video_list_authorization_url("creator-one", house.id, connection.id)
    finally:
        reset_current_user_id(token)


def test_tiktok_video_list_routes_are_private_social_api_routes():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(social_management_router)
    client = TestClient(app, raise_server_exceptions=False)
    private = "/command-center/api/social/oauth/tiktok/video-list/status?space_id=test"
    assert client.get(private, follow_redirects=False).status_code != 404
    assert client.get("/oauth/tiktok/video-list/status?space_id=test", follow_redirects=False).status_code == 404
    assert client.get("/api/social/oauth/tiktok/video-list/status?space_id=test", follow_redirects=False).status_code == 404
