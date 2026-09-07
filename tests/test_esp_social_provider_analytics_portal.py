from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.esp_social_connections_portal as connections_module
import aura_music_studio.esp_social_provider_analytics_portal as portal_module
from aura_music_studio.esp_social_portal_overlay import router as social_overlay_router


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


def test_provider_analytics_portal_is_private_command_center_route():
    assert _route_status(
        social_overlay_router,
        "/command-center/social/provider-analytics",
    ) != 404
    assert _route_status(social_overlay_router, "/social/provider-analytics") == 404
    assert _route_status(social_overlay_router, "/provider-analytics") == 404


def test_provider_analytics_portal_uses_private_sync_and_intelligence_endpoints(monkeypatch):
    monkeypatch.setattr(
        portal_module,
        "require_esp_social_member",
        lambda _request: (object(), {"roles": "creator", "status": "active"}, _profile()),
    )

    response = portal_module.provider_analytics_portal(object())
    html = response.body.decode("utf-8")

    assert "/command-center/api/social" in html
    assert "/provider-analytics/capabilities" in html
    assert "/provider-analytics/${encodeURIComponent(provider)}/sync" in html
    assert "/command-center/api/social-intelligence" in html
    assert "YouTube owned-channel video statistics are active" in html
    assert "TikTok and Instagram stay visibly blocked or pending" in html
    assert "No OAuth token is returned to this page." in html
    assert "token_secret_ref" not in html
    assert "access_token" not in html
    assert "refresh_token" not in html


def test_connections_navigation_links_to_provider_analytics(monkeypatch):
    monkeypatch.setattr(
        connections_module,
        "require_esp_social_member",
        lambda _request: (object(), {"roles": "creator", "status": "active"}, _profile()),
    )

    response = connections_module.social_connections_portal(object())
    html = response.body.decode("utf-8")

    assert "href='/command-center/social/provider-analytics'" in html
    assert ">Provider Analytics</a>" in html
