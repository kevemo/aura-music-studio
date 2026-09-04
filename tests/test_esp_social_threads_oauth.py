from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

import aura_music_studio.esp_social_threads_oauth as threads_oauth
from aura_music_studio.esp_social_oauth import SocialOAuthVault, oauth_credential_id
from aura_music_studio.esp_social_provider_adapters import ProviderAdapterError
from aura_music_studio.social_management_api import router as social_api_router


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._payload


class FakeHouse:
    def __init__(self):
        self.connections = []
        self.activity = []


class FakeStore:
    def __init__(self):
        self.house = FakeHouse()
        self.saved = 0

    def load(self, space_id):
        if space_id != "space_threads":
            raise FileNotFoundError(space_id)
        return self.house

    def connect_placeholder(self, space_id, connection):
        self.load(space_id)
        self.house.connections = [
            row for row in self.house.connections if row.id != connection.id and row.platform != connection.platform
        ]
        self.house.connections.append(connection)
        return self.house

    def save(self, house):
        self.house = house
        self.saved += 1
        return house


def _env(monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://studio.example.test")
    monkeypatch.setenv("THREADS_OAUTH_APP_ID", "threads-app-123")
    monkeypatch.setenv("THREADS_OAUTH_APP_SECRET", "threads-secret-456")


def _provider_calls(monkeypatch, *, scopes=None):
    calls = []
    granted = scopes or ["threads_basic", "threads_content_publish"]

    def post(url, *, params=None, timeout=None, **kwargs):
        calls.append(("POST", url, dict(params or {}), dict(kwargs)))
        assert url == "https://graph.threads.net/oauth/access_token"
        return FakeResponse({"access_token": "short-user-token", "user_id": "threads-user-99"})

    def get(url, *, params=None, headers=None, timeout=None, **kwargs):
        calls.append(("GET", url, dict(params or {}), {"headers": dict(headers or {}), **kwargs}))
        if url == "https://graph.threads.net/access_token":
            assert params["grant_type"] == "th_exchange_token"
            assert params["access_token"] == "short-user-token"
            return FakeResponse({"access_token": "long-user-token", "token_type": "bearer", "expires_in": 5184000})
        if url == "https://graph.threads.net/oauth/access_token":
            assert params["grant_type"] == "client_credentials"
            return FakeResponse({"access_token": "TH|app|token", "token_type": "bearer"})
        if url == "https://graph.threads.net/debug_token":
            assert params == {"input_token": "long-user-token"}
            assert headers == {"Authorization": "Bearer TH|app|token"}
            return FakeResponse({"data": {"is_valid": True, "scopes": granted, "user_id": "threads-user-99"}})
        if url == "https://graph.threads.net/me":
            assert headers == {"Authorization": "Bearer long-user-token"}
            return FakeResponse({"id": "threads-user-99", "username": "aura_creator", "name": "Aura Creator"})
        raise AssertionError(url)

    monkeypatch.setattr(threads_oauth.requests, "post", post)
    monkeypatch.setattr(threads_oauth.requests, "get", get)
    return calls


def test_threads_authorization_url_is_two_scope_https_and_state_is_member_bound(monkeypatch, tmp_path):
    _env(monkeypatch)
    store = FakeStore()
    monkeypatch.setattr(threads_oauth, "SocialHouseStore", lambda: store)
    vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
    service = threads_oauth.ThreadsOAuthService(vault)

    url = service.authorization_url(user_id="member_a", space_id="space_threads")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "threads.net"
    assert parsed.path == "/oauth/authorize"
    assert query["client_id"] == ["threads-app-123"]
    assert query["redirect_uri"] == ["https://studio.example.test/command-center/api/social/oauth/threads/callback"]
    assert query["scope"] == ["threads_basic,threads_content_publish"]
    assert query["response_type"] == ["code"]
    assert len(query["state"][0]) >= 40

    with pytest.raises(PermissionError, match="another member"):
        service.state.consume("member_b", query["state"][0])
    pending = service.state.consume("member_a", query["state"][0])
    assert pending["space_id"] == "space_threads"
    assert pending["scopes"] == ["threads_basic", "threads_content_publish"]
    with pytest.raises(PermissionError):
        service.state.consume("member_a", query["state"][0])


def test_threads_complete_verifies_scopes_and_stores_only_encrypted_vault_reference(monkeypatch, tmp_path):
    _env(monkeypatch)
    store = FakeStore()
    monkeypatch.setattr(threads_oauth, "SocialHouseStore", lambda: store)
    calls = _provider_calls(monkeypatch)
    vault_path = tmp_path / "oauth.sqlite3"
    vault = SocialOAuthVault(vault_path)
    service = threads_oauth.ThreadsOAuthService(vault)
    auth = service.authorization_url(user_id="member_a", space_id="space_threads")
    state = parse_qs(urlsplit(auth).query)["state"][0]

    connection = service.complete(user_id="member_a", state=state, code="provider-code")
    assert connection.platform == "threads"
    assert connection.account_external_id == "threads-user-99"
    assert connection.account_label == "@aura_creator"
    assert connection.supports_auto_publish is True
    assert connection.metadata["publishing_adapter"] == "threads_graph"
    assert connection.metadata["oauth_scopes"] == ["threads_basic", "threads_content_publish"]
    assert connection.token_secret_ref.startswith("social-oauth://")
    assert "long-user-token" not in connection.model_dump_json()
    assert "short-user-token" not in connection.model_dump_json()

    credential_id = oauth_credential_id(connection.token_secret_ref)
    record = vault.load("member_a", credential_id or "")
    assert record is not None
    assert record["provider"] == "threads"
    assert record["token"]["access_token"] == "long-user-token"
    assert record["token"]["expires_at"]
    assert b"long-user-token" not in vault_path.read_bytes()
    assert store.saved == 1
    assert any(item.action == "social_oauth_connected" for item in store.house.activity)
    assert [call[1] for call in calls] == [
        "https://graph.threads.net/oauth/access_token",
        "https://graph.threads.net/access_token",
        "https://graph.threads.net/oauth/access_token",
        "https://graph.threads.net/debug_token",
        "https://graph.threads.net/me",
    ]


def test_threads_connection_fails_closed_when_publish_permission_is_missing(monkeypatch, tmp_path):
    _env(monkeypatch)
    store = FakeStore()
    monkeypatch.setattr(threads_oauth, "SocialHouseStore", lambda: store)
    _provider_calls(monkeypatch, scopes=["threads_basic"])
    service = threads_oauth.ThreadsOAuthService(SocialOAuthVault(tmp_path / "oauth.sqlite3"))
    auth = service.authorization_url(user_id="member_a", space_id="space_threads")
    state = parse_qs(urlsplit(auth).query)["state"][0]

    with pytest.raises(PermissionError, match="threads_content_publish"):
        service.complete(user_id="member_a", state=state, code="provider-code")
    assert store.house.connections == []


def test_threads_long_token_refresh_is_isolated_to_threads_provider(monkeypatch, tmp_path):
    _env(monkeypatch)
    vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
    threads_oauth.install_threads_oauth_token_extension()
    now = threads_oauth._now()
    vault.save(
        user_id="member_a",
        credential_id="socialcred_threads",
        provider="threads",  # type: ignore[arg-type]
        token={
            "access_token": "old-long-token",
            "issued_at": threads_oauth._iso(now - timedelta(days=30)),
            "expires_at": threads_oauth._iso(now + timedelta(minutes=2)),
        },
        scopes=["threads_basic", "threads_content_publish"],
        account_external_id="threads-user-99",
        account_label="@aura_creator",
    )
    calls = []

    def get(url, *, params=None, timeout=None, **kwargs):
        calls.append((url, dict(params or {})))
        assert url == "https://graph.threads.net/refresh_access_token"
        assert params == {"grant_type": "th_refresh_token", "access_token": "old-long-token"}
        return FakeResponse({"access_token": "refreshed-long-token", "token_type": "bearer", "expires_in": 5184000})

    monkeypatch.setattr(threads_oauth.requests, "get", get)
    assert vault.access_token("member_a", "socialcred_threads") == "refreshed-long-token"
    assert len(calls) == 1
    refreshed = vault.load("member_a", "socialcred_threads")
    assert refreshed["token"]["access_token"] == "refreshed-long-token"
    assert b"refreshed-long-token" not in (tmp_path / "oauth.sqlite3").read_bytes()

    vault.save(
        user_id="member_a",
        credential_id="socialcred_other",
        provider="instagram",
        token={"access_token": "instagram-current"},
        scopes=["instagram_business_basic"],
        account_external_id="ig1",
        account_label="Instagram",
    )
    assert vault.access_token("member_a", "socialcred_other") == "instagram-current"
    assert len(calls) == 1


def test_threads_expired_long_token_requires_reconnect_without_provider_call(monkeypatch, tmp_path):
    _env(monkeypatch)
    vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
    now = threads_oauth._now()
    vault.save(
        user_id="member_a",
        credential_id="expired_threads",
        provider="threads",  # type: ignore[arg-type]
        token={
            "access_token": "expired-token",
            "issued_at": threads_oauth._iso(now - timedelta(days=61)),
            "expires_at": threads_oauth._iso(now - timedelta(seconds=1)),
        },
        scopes=["threads_basic", "threads_content_publish"],
        account_external_id="threads-user-99",
        account_label="@aura_creator",
    )
    monkeypatch.setattr(
        threads_oauth.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    with pytest.raises(ProviderAdapterError, match="expired"):
        vault.access_token("member_a", "expired_threads")


def test_threads_specific_oauth_routes_precede_generic_provider_wildcard():
    paths = [getattr(route, "path", "") for route in social_api_router.routes]
    specific = "/command-center/api/social/oauth/threads/start"
    generic = "/command-center/api/social/oauth/{provider}/start"
    assert specific in paths
    assert generic in paths
    assert paths.index(specific) < paths.index(generic)
    assert "/command-center/api/social/oauth/threads/capability" in paths
    assert "/command-center/api/social/oauth/threads/callback" in paths
    assert "/command-center/api/social/oauth/threads/disconnect" in paths


def test_threads_disconnect_deletes_encrypted_credential_and_disables_adapter(monkeypatch, tmp_path):
    _env(monkeypatch)
    store = FakeStore()
    monkeypatch.setattr(threads_oauth, "SocialHouseStore", lambda: store)
    vault = SocialOAuthVault(tmp_path / "oauth.sqlite3")
    service = threads_oauth.ThreadsOAuthService(vault)
    vault.save(
        user_id="member_a",
        credential_id="disconnect_me",
        provider="threads",  # type: ignore[arg-type]
        token={"access_token": "long-token"},
        scopes=["threads_basic", "threads_content_publish"],
        account_external_id="threads-user-99",
        account_label="@aura_creator",
    )
    from aura_music_studio.social_management import SocialConnection

    connection = SocialConnection(
        id="conn_threads",
        platform="threads",
        account_label="@aura_creator",
        account_external_id="threads-user-99",
        state="connected",
        supports_auto_publish=True,
        token_secret_ref="social-oauth://disconnect_me",
        metadata={
            "publishing_adapter": "threads_graph",
            "publishing_adapter_active": True,
            "oauth_verified": True,
        },
    )
    store.house.connections.append(connection)

    assert service.disconnect(user_id="member_a", space_id="space_threads", connection_id="conn_threads") is True
    assert vault.load("member_a", "disconnect_me") is None
    assert connection.state == "not_connected"
    assert connection.supports_auto_publish is False
    assert connection.token_secret_ref is None
    assert connection.metadata["publishing_adapter_active"] is False
    assert connection.metadata["oauth_verified"] is False
