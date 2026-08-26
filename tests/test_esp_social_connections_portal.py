from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.esp_social_connections_portal as portal_module
from aura_music_studio.esp_social_portal_overlay import router as social_overlay_router


def _route_status(router, path: str) -> int:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    return client.get(path, follow_redirects=False).status_code


def test_connections_portal_is_private_esp_command_center_route():
    assert _route_status(
        social_overlay_router,
        "/command-center/social/connections",
    ) != 404
    assert _route_status(social_overlay_router, "/social/connections") == 404
    assert _route_status(social_overlay_router, "/connections") == 404


def test_connections_portal_uses_secure_oauth_endpoints_and_hides_secret_refs(monkeypatch):
    profile = {
        "catalog": {
            "title": "Music & Performing Arts",
            "theme": {"accent": "#f4c873", "secondary": "#9f73ff"},
        }
    }
    monkeypatch.setattr(
        portal_module,
        "require_esp_social_member",
        lambda _request: (object(), {"roles": "creator", "status": "active"}, profile),
    )

    response = portal_module.social_connections_portal(object())
    html = response.body.decode("utf-8")

    assert "/command-center/api/social" in html
    assert "/oauth/providers" in html
    assert "/oauth/${encodeURIComponent(provider)}/start" in html
    assert "/oauth/${encodeURIComponent(provider)}/disconnect" in html
    assert "TikTok" in html
    assert "Instagram" in html
    assert "YouTube" in html
    assert "encrypted server-side" in html
    assert "token_secret_ref" not in html
    assert "access_token" not in html
    assert "refresh_token" not in html


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
