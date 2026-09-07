from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from aura_music_studio.esp_social_facebook_oauth import FacebookPageOAuthService
from aura_music_studio.esp_social_oauth import SocialOAuthVault
from aura_music_studio.social_management import SocialHouse


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._payload


class FakeStore:
    def __init__(self, house):
        self.house = house
        self.saved = None

    def load(self, space_id):
        if space_id != self.house.id:
            raise FileNotFoundError(space_id)
        return self.house

    def connect_placeholder(self, space_id, connection):
        if space_id != self.house.id:
            raise FileNotFoundError(space_id)
        self.house.connections = [
            existing for existing in self.house.connections if existing.id != connection.id
        ]
        self.house.connections.append(connection)
        return self.house

    def save(self, house):
        self.saved = house
        self.house = house
        return house


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("AURA_FACEBOOK_GRAPH_VERSION", "v23.0")
    monkeypatch.setenv("FACEBOOK_OAUTH_APP_ID", "facebook-app")
    monkeypatch.setenv("FACEBOOK_OAUTH_APP_SECRET", "facebook-secret")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example.test")
    vault = SocialOAuthVault(tmp_path / "social-oauth.sqlite3")
    return FacebookPageOAuthService(vault)


def test_facebook_oauth_start_requires_explicit_numeric_page_and_keeps_target_server_side(
    monkeypatch, tmp_path
):
    service = _configure(monkeypatch, tmp_path)
    house = SocialHouse(name="ESP Facebook")
    fake_store = FakeStore(house)
    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_oauth.SocialHouseStore",
        lambda: fake_store,
    )

    with pytest.raises(ValueError, match="numeric Facebook Page ID"):
        service.authorization_url(
            user_id="member-a",
            space_id=house.id,
            page_id="me",
        )

    url = service.authorization_url(
        user_id="member-a",
        space_id=house.id,
        page_id="123456789012345",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.facebook.com"
    assert parsed.path == "/v23.0/dialog/oauth"
    assert query["client_id"] == ["facebook-app"]
    assert query["redirect_uri"] == [
        "https://command.example.test/command-center/api/social/oauth/facebook/callback"
    ]
    assert set(query["scope"][0].split(",")) == {
        "pages_show_list",
        "pages_manage_posts",
        "pages_read_engagement",
    }
    assert "123456789012345" not in url


def test_facebook_oauth_completion_vaults_only_selected_page_token(
    monkeypatch, tmp_path
):
    service = _configure(monkeypatch, tmp_path)
    house = SocialHouse(name="ESP Facebook")
    fake_store = FakeStore(house)
    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_oauth.SocialHouseStore",
        lambda: fake_store,
    )
    state, _connection_id, credential_id = service.create_state(
        user_id="member-a",
        space_id=house.id,
        page_id="123456789012345",
    )

    def fake_post(url, *, data=None, timeout=None):
        assert url == "https://graph.facebook.com/v23.0/oauth/access_token"
        assert data["client_secret"] == "facebook-secret"
        return FakeResponse({"access_token": "user-access-token"})

    def fake_get(url, *, headers=None, params=None, timeout=None):
        assert headers == {"Authorization": "Bearer user-access-token"}
        if url.endswith("/me/permissions"):
            return FakeResponse(
                {
                    "data": [
                        {"permission": "pages_show_list", "status": "granted"},
                        {"permission": "pages_manage_posts", "status": "granted"},
                        {"permission": "pages_read_engagement", "status": "granted"},
                    ]
                }
            )
        if url.endswith("/me/accounts"):
            assert params["fields"] == "id,name,access_token,tasks"
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "999999999999999",
                            "name": "Other Page",
                            "access_token": "other-page-token",
                            "tasks": ["CREATE_CONTENT"],
                        },
                        {
                            "id": "123456789012345",
                            "name": "Elevate Souls Productions",
                            "access_token": "selected-page-token",
                            "tasks": ["CREATE_CONTENT", "MODERATE"],
                        },
                    ]
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_oauth.requests.post", fake_post
    )
    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_oauth.requests.get", fake_get
    )

    connection = service.complete(
        user_id="member-a",
        state=state,
        code="authorization-code",
    )

    assert connection.platform == "facebook"
    assert connection.account_external_id == "123456789012345"
    assert connection.account_label == "Elevate Souls Productions"
    assert connection.supports_auto_publish is True
    assert connection.supports_analytics is False
    assert connection.supports_inbox is False
    assert connection.token_secret_ref == f"social-oauth://{credential_id}"
    assert connection.metadata["publishing_adapter"] == "facebook_pages_graph"
    assert connection.metadata["oauth_verified"] is True
    assert connection.metadata["facebook_page_id"] == "123456789012345"
    assert connection.metadata["manual_reconnect_on_token_expiry"] is True

    stored = service.vault.load("member-a", credential_id)
    assert stored["provider"] == "facebook"
    assert stored["token"]["access_token"] == "selected-page-token"
    assert stored["account_external_id"] == "123456789012345"
    assert "selected-page-token" not in service.vault.path.read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert fake_store.saved is not None


def test_facebook_oauth_fails_closed_when_required_permission_or_target_page_is_missing(
    monkeypatch, tmp_path
):
    service = _configure(monkeypatch, tmp_path)
    cfg = {
        "graph_base": "https://graph.facebook.com/v23.0",
    }

    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_oauth.requests.get",
        lambda *args, **kwargs: FakeResponse(
            {
                "data": [
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "pages_manage_posts", "status": "granted"},
                ]
            }
        ),
    )
    with pytest.raises(PermissionError, match="pages_read_engagement"):
        service._verify_permissions(user_token="user-token", cfg=cfg)

    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_oauth.requests.get",
        lambda *args, **kwargs: FakeResponse(
            {
                "data": [
                    {
                        "id": "999999999999999",
                        "name": "Different Page",
                        "access_token": "other-page-token",
                        "tasks": ["CREATE_CONTENT"],
                    }
                ]
            }
        ),
    )
    with pytest.raises(PermissionError, match="explicitly selected Page"):
        service._resolve_page(
            user_token="user-token",
            target_page_id="123456789012345",
            cfg=cfg,
        )


def test_facebook_static_oauth_routes_are_registered_before_generic_provider_routes():
    from aura_music_studio.social_management_api import router

    paths = [route.path for route in router.routes if hasattr(route, "path")]
    facebook_callback = "/command-center/api/social/oauth/facebook/callback"
    generic_callback = "/command-center/api/social/oauth/{provider}/callback"
    assert facebook_callback in paths
    assert generic_callback in paths
    assert paths.index(facebook_callback) < paths.index(generic_callback)
