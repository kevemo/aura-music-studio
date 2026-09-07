from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from aura_music_studio import access_control
from aura_music_studio import shared_sky_internal_media_api as media_api
from aura_music_studio import shared_sky_transport_browser_bridge as bridge_module
from aura_music_studio.shared_sky_transport_browser_bridge import (
    Chat2CookieBootstrapPlaybackAdapter,
    install_chat2_browser_playback_bridge,
)


class _Community:
    def _broadcast(self, broadcast_id: str):
        return {"id": broadcast_id, "user_id": "creator-user"}


class _Transport:
    def __init__(self, *, browser_mode: str = "cookie_exchange"):
        self.browser_mode = browser_mode

    def status(self, owner_user_id: str, broadcast_id: str):
        assert owner_user_id == "creator-user"
        return {
            "session": {"state": "live", "rendition_profile": {}},
            "playback": {
                "capability_state": "ready",
                "state": "live",
                "mode": "hls",
                "manifest_url": f"https://sky.example/shared-sky/media/{broadcast_id}/master.m3u8",
                "authorization": {
                    "scheme": "Bearer",
                    "token": "server-issued-token",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
                "browser_authorization": {"mode": self.browser_mode},
            },
        }


def _request(path: str = "/shared-sky/media/broadcast-1/bootstrap") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"sky.example"), (b"user-agent", b"test-viewer")],
            "client": ("203.0.113.7", 12345),
            "scheme": "https",
            "server": ("sky.example", 443),
        }
    )


def test_watch_adapter_returns_token_free_bootstrap_url_when_cookie_exchange_exists():
    adapter = Chat2CookieBootstrapPlaybackAdapter(_Transport(), _Community())
    descriptor = adapter.descriptor("broadcast-1", None)
    assert descriptor["available"] is True
    assert descriptor["manifest_url"] == "/shared-sky/media/broadcast-1/bootstrap"
    assert descriptor["authorization"] is None
    assert descriptor["token_expires_at"] is None
    assert descriptor["browser_authorization_mode"] == "cookie_bootstrap_redirect"
    assert descriptor["source"] == "chat2_transport_browser_bridge"
    assert "server-issued-token" not in repr(descriptor)


def test_watch_adapter_remains_fail_closed_without_cookie_exchange():
    adapter = Chat2CookieBootstrapPlaybackAdapter(
        _Transport(browser_mode="unsupported"), _Community()
    )
    descriptor = adapter.descriptor("broadcast-1", None)
    assert descriptor["available"] is False
    assert descriptor["manifest_url"] is None
    assert descriptor["authorization"] is None
    assert descriptor["reason"] == "browser_playback_exchange_unavailable"


def test_native_watch_bootstrap_checks_access_sets_cookie_and_redirects_without_token(monkeypatch):
    expires = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
    calls = {"playback": 0}
    monkeypatch.setattr(media_api, "optional_member", lambda request: None)
    monkeypatch.setattr(
        media_api.community,
        "access",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason="public"),
    )
    monkeypatch.setattr(
        media_api.community,
        "_broadcast",
        lambda broadcast_id: {"id": broadcast_id, "user_id": "creator-user"},
    )
    monkeypatch.setattr(media_api.community, "actor_key", lambda request, user_id=None: "anon:test")
    monkeypatch.setattr(media_api.community, "rate_limit", lambda *args, **kwargs: None)

    def playback(owner_user_id: str, broadcast_id: str, ttl: int = 120):
        calls["playback"] += 1
        assert owner_user_id == "creator-user"
        assert ttl == 120
        return {
            "capability_state": "ready",
            "state": "live",
            "manifest_url": f"https://public.example/shared-sky/media/{broadcast_id}/master.m3u8",
            "authorization": {
                "scheme": "Bearer",
                "token": "bootstrap-secret-token",
                "expires_at": expires,
            },
        }

    monkeypatch.setattr(media_api.transport, "playback", playback)
    response = media_api.bootstrap_shared_sky_media("broadcast-1", _request())
    assert response.status_code == 307
    assert response.headers["location"] == "/shared-sky/media/broadcast-1/master.m3u8"
    assert "bootstrap-secret-token" not in response.headers["location"]
    cookie = response.headers["set-cookie"]
    assert "shared_sky_playback=bootstrap-secret-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/shared-sky/media/broadcast-1/" in cookie
    assert calls["playback"] == 1


def test_native_watch_bootstrap_denies_before_minting_playback_token(monkeypatch):
    monkeypatch.setattr(media_api, "optional_member", lambda request: None)
    monkeypatch.setattr(
        media_api.community,
        "access",
        lambda *args, **kwargs: SimpleNamespace(allowed=False, reason="followers_only"),
    )
    called = {"playback": False}

    def playback(*args, **kwargs):
        called["playback"] = True
        raise AssertionError("playback token must not be minted for denied viewers")

    monkeypatch.setattr(media_api.transport, "playback", playback)
    with pytest.raises(HTTPException) as exc:
        media_api.bootstrap_shared_sky_media("broadcast-1", _request())
    assert exc.value.status_code == 403
    assert called["playback"] is False


def test_browser_bridge_installer_registers_public_media_prefix(monkeypatch):
    old_prefixes = tuple(access_control.PUBLIC_PREFIXES)
    old_status = dict(bridge_module._INTEGRATION_STATUS.get("chat2_playback") or {})
    registered = []
    monkeypatch.setitem(
        bridge_module._INTEGRATION_STATUS,
        "chat2_playback",
        {"state": "registered", "source": "test"},
    )
    monkeypatch.setattr(
        bridge_module.live,
        "register_playback_adapter",
        lambda adapter: registered.append(adapter),
    )
    try:
        result = install_chat2_browser_playback_bridge()
        assert result["state"] == "registered"
        assert result["browser_runtime"] == "chat2_cookie_bootstrap_redirect"
        assert result["browser_token_in_url"] is False
        assert "/shared-sky/media/" in access_control.PUBLIC_PREFIXES
        assert len(registered) == 1
        assert isinstance(registered[0], Chat2CookieBootstrapPlaybackAdapter)
    finally:
        access_control.PUBLIC_PREFIXES = old_prefixes
        bridge_module._INTEGRATION_STATUS["chat2_playback"] = old_status


def test_bootstrap_route_precedes_media_asset_catchall():
    routes = [
        (getattr(route, "path", None), set(getattr(route, "methods", None) or set()))
        for route in media_api.router.routes
    ]
    bootstrap_index = next(
        idx for idx, (path, methods) in enumerate(routes)
        if path == "/shared-sky/media/{broadcast_id}/bootstrap" and "GET" in methods
    )
    asset_index = next(
        idx for idx, (path, methods) in enumerate(routes)
        if path == "/shared-sky/media/{broadcast_id}/{asset_path:path}" and "GET" in methods
    )
    assert bootstrap_index < asset_index
