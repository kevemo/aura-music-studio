from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import aura_music_studio.esp_social_connections_portal as portal_module
from aura_music_studio.esp_social_portal_overlay import router as social_overlay_router
from aura_music_studio.social_management import SocialConnection


def _route_status(router, path: str) -> int:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    return client.get(path, follow_redirects=False).status_code


def _profile():
    return {
        "catalog": {
            "title": "Music & Performing Arts",
            "theme": {"accent": "#f4c873", "secondary": "#9f73ff"},
        }
    }


def test_connections_portal_is_private_esp_command_center_route():
    assert _route_status(
        social_overlay_router,
        "/command-center/social/connections",
    ) != 404
    assert _route_status(social_overlay_router, "/social/connections") == 404
    assert _route_status(social_overlay_router, "/connections") == 404


def test_connections_portal_uses_secure_oauth_and_managed_facebook_runtime(monkeypatch):
    monkeypatch.setattr(
        portal_module,
        "require_esp_social_member",
        lambda _request: (object(), {"roles": "creator", "status": "active"}, _profile()),
    )

    response = portal_module.social_connections_portal(object())
    html = response.body.decode("utf-8")

    assert "/command-center/api/social" in html
    assert "/oauth/providers" in html
    assert "/platforms" in html
    assert "/connections/runtime" in html
    assert "/oauth/${encodeURIComponent(provider)}/start" in html
    assert "/oauth/${encodeURIComponent(provider)}/disconnect" in html
    assert "TikTok" in html
    assert "Instagram" in html
    assert "YouTube" in html
    assert "Facebook Pages" in html
    assert "mode:'managed'" in html
    assert "connections/facebook-pages" in html
    assert "Never paste a Facebook access token here" in html
    assert "encrypted or deployment-held server-side" in html
    assert "token_secret_ref" not in html
    assert "access_token" not in html
    assert "refresh_token" not in html


def test_facebook_pages_configuration_is_server_enforced_owner_only(monkeypatch):
    monkeypatch.setattr(
        portal_module,
        "require_esp_social_member",
        lambda _request: (SimpleNamespace(user_id="creator-1"), {"roles": "creator", "status": "active"}, _profile()),
    )
    body = portal_module.FacebookPagesConnectionRequest(
        account_label="ESP Facebook",
        page_id="1234567890",
        token_alias="facebook_pages",
    )
    with pytest.raises(HTTPException) as exc:
        portal_module.configure_facebook_pages("space-1", body, object())
    assert exc.value.status_code == 403


def test_owner_can_register_facebook_page_without_browser_token_storage(monkeypatch):
    class FakeHouse:
        def __init__(self):
            self.connections = []

    class FakeStore:
        def __init__(self):
            self.house = FakeHouse()

        def load(self, _space_id):
            return self.house

        def connect_placeholder(self, _space_id, connection):
            self.house.connections = [
                item for item in self.house.connections if item.id != connection.id
            ] + [connection]
            return self.house

    fake_store = FakeStore()
    monkeypatch.setattr(portal_module, "SocialHouseStore", lambda: fake_store)
    monkeypatch.setattr(
        portal_module,
        "require_esp_social_member",
        lambda _request: (SimpleNamespace(user_id="owner-1"), {"roles": "both", "status": "owner"}, _profile()),
    )
    monkeypatch.setenv("AURA_FACEBOOK_GRAPH_VERSION", "v23.0")
    monkeypatch.setenv("AURA_SOCIAL_TOKEN_FACEBOOK_PAGES", "server-only-secret-value")

    result = portal_module.configure_facebook_pages(
        "space-1",
        portal_module.FacebookPagesConnectionRequest(
            account_label="ESP Official Page",
            page_id="1234567890123",
            token_alias="facebook_pages",
        ),
        object(),
    )

    assert result["facebook"]["deployment_ready"] is True
    assert result["facebook"]["credential_configured"] is True
    assert result["facebook"]["graph_api_configured"] is True
    assert result["credential_value_stored_in_social_house"] is False
    assert "server-only-secret-value" not in str(result)
    connection: SocialConnection = fake_store.house.connections[0]
    assert connection.platform == "facebook"
    assert connection.account_external_id == "1234567890123"
    assert connection.token_secret_ref == "social-token://facebook_pages"
    assert connection.metadata["publishing_adapter"] == "facebook_pages_graph"
    assert connection.metadata["publishing_adapter_active"] is True


def test_publish_queue_navigation_links_to_connections(monkeypatch):
    from aura_music_studio import esp_social_publish_queue_portal as queue_module

    profile = {
        "catalog": {
            "title": "Gaming",
            "theme": {"accent": "#f4c873", "secondary": "#9f73ff"},
        }
    }
    monkeypatch.setattr(
        queue_module,
        "require_esp_social_member",
        lambda _request: (object(), {"roles": "creator", "status": "active"}, profile),
    )
    response = queue_module.publish_queue_portal(object())
    html = response.body.decode("utf-8")
    assert "href='/command-center/social/connections'" in html
    assert ">Connections</a>" in html
