from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.shared_sky_destination_adapters import CapabilityState
from aura_music_studio.shared_sky_internal_media_api import (
    authorize_shared_sky_media,
    clear_shared_sky_media_authorization,
    router as media_router,
    shared_sky_media_asset,
)
from aura_music_studio.shared_sky_transport_browser_playback import TransportBrowserPlaybackMixin


class _PlaybackBase:
    def playback(self, user_id: str, broadcast_id: str, ttl: int = 120) -> dict:
        return {
            "capability_state": CapabilityState.READY,
            "mode": "hls",
            "manifest_url": f"https://sky.example/shared-sky/media/{broadcast_id}/master.m3u8",
            "authorization": {
                "scheme": "Bearer",
                "token": "signed-token-value",
                "expires_at": "2099-01-01T00:00:00+00:00",
            },
            "broadcast_id": broadcast_id,
            "state": "live",
        }


class _BrowserPlayback(TransportBrowserPlaybackMixin, _PlaybackBase):
    pass


def test_playback_descriptor_advertises_cookie_exchange_without_token_in_url():
    descriptor = _BrowserPlayback().playback("viewer", "broadcast_cookie")
    browser = descriptor["browser_authorization"]
    assert browser["mode"] == "cookie_exchange"
    assert browser["method"] == "POST"
    assert browser["cookie_http_only"] is True
    assert browser["cookie_path_scoped"] is True
    assert browser["token_in_manifest_url"] is False
    assert browser["exchange_url"] == "/shared-sky/media/broadcast_cookie/authorize"
    assert descriptor["authorization"]["token"] not in descriptor["manifest_url"]
    assert descriptor["authorization"]["token"] not in browser["exchange_url"]


def test_authorize_exchange_sets_secure_httponly_broadcast_scoped_cookie(monkeypatch):
    from aura_music_studio import shared_sky_internal_media_api as module

    monkeypatch.delenv("SHARED_SKY_ALLOW_INSECURE_PLAYBACK", raising=False)
    seen = {}

    def verify(token, *, expected_broadcast_id=None, **kwargs):
        seen["token"] = token
        seen["broadcast_id"] = expected_broadcast_id
        return {
            "broadcast_id": expected_broadcast_id,
            "user_id": "owner-user",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "nonce": "nonce",
        }

    monkeypatch.setattr(module.transport, "verify_playback_token", verify)
    response = authorize_shared_sky_media(
        "broadcast_cookie",
        authorization="Bearer signed-token-value",
    )
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "shared_sky_playback=signed-token-value" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/shared-sky/media/broadcast_cookie/" in cookie
    assert seen == {"token": "signed-token-value", "broadcast_id": "broadcast_cookie"}


def test_cookie_clear_matches_secure_and_development_modes(monkeypatch):
    monkeypatch.delenv("SHARED_SKY_ALLOW_INSECURE_PLAYBACK", raising=False)
    secure = clear_shared_sky_media_authorization("broadcast_cookie")
    secure_cookie = secure.headers["set-cookie"]
    assert "Path=/shared-sky/media/broadcast_cookie/" in secure_cookie
    assert "Secure" in secure_cookie
    assert "Max-Age=0" in secure_cookie

    monkeypatch.setenv("SHARED_SKY_ALLOW_INSECURE_PLAYBACK", "1")
    insecure = clear_shared_sky_media_authorization("broadcast_cookie")
    insecure_cookie = insecure.headers["set-cookie"]
    assert "Path=/shared-sky/media/broadcast_cookie/" in insecure_cookie
    assert "Secure" not in insecure_cookie
    assert "Max-Age=0" in insecure_cookie


def test_native_media_asset_accepts_scoped_cookie_and_rejects_wrong_broadcast(tmp_path, monkeypatch):
    from aura_music_studio import shared_sky_internal_media_api as module

    target = tmp_path / "master.m3u8"
    target.write_text("#EXTM3U\n", encoding="utf-8")

    def verify(token, *, expected_broadcast_id=None, **kwargs):
        if token != "cookie-token" or expected_broadcast_id != "broadcast_cookie":
            raise ValueError("invalid")
        return {
            "broadcast_id": expected_broadcast_id,
            "user_id": "owner-user",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "nonce": "nonce",
        }

    monkeypatch.setattr(module.transport, "verify_playback_token", verify)
    monkeypatch.setattr(module.internal_media, "playback_asset", lambda broadcast_id, asset_path: target)

    response = shared_sky_media_asset(
        "broadcast_cookie",
        "master.m3u8",
        playback_cookie="cookie-token",
    )
    assert Path(response.path).resolve() == target.resolve()
    assert response.media_type == "application/vnd.apple.mpegurl"

    with pytest.raises(Exception):
        shared_sky_media_asset(
            "another_broadcast",
            "master.m3u8",
            playback_cookie="cookie-token",
        )


def test_bearer_header_takes_precedence_over_cookie(monkeypatch, tmp_path):
    from aura_music_studio import shared_sky_internal_media_api as module

    target = tmp_path / "segment.ts"
    target.write_bytes(b"segment")
    seen = []

    def verify(token, *, expected_broadcast_id=None, **kwargs):
        seen.append(token)
        if token != "header-token":
            raise ValueError("wrong credential")
        return {"broadcast_id": expected_broadcast_id, "expires_at": "2099-01-01T00:00:00+00:00"}

    monkeypatch.setattr(module.transport, "verify_playback_token", verify)
    monkeypatch.setattr(module.internal_media, "playback_asset", lambda broadcast_id, asset_path: target)
    response = shared_sky_media_asset(
        "broadcast_cookie",
        "720p/segment.ts",
        authorization="Bearer header-token",
        playback_cookie="stale-cookie-token",
    )
    assert Path(response.path).resolve() == target.resolve()
    assert seen == ["header-token"]


def test_media_router_exposes_browser_authorization_exchange_before_asset_catchall():
    routes = [
        (getattr(route, "path", None), set(getattr(route, "methods", None) or set()))
        for route in media_router.routes
    ]
    assert ("/shared-sky/media/{broadcast_id}/authorize", {"POST"}) in routes
    assert ("/shared-sky/media/{broadcast_id}/authorize", {"DELETE"}) in routes
    asset_index = next(
        idx for idx, (path, methods) in enumerate(routes)
        if path == "/shared-sky/media/{broadcast_id}/{asset_path:path}" and "GET" in methods
    )
    post_index = next(
        idx for idx, (path, methods) in enumerate(routes)
        if path == "/shared-sky/media/{broadcast_id}/authorize" and "POST" in methods
    )
    delete_index = next(
        idx for idx, (path, methods) in enumerate(routes)
        if path == "/shared-sky/media/{broadcast_id}/authorize" and "DELETE" in methods
    )
    assert post_index < asset_index
    assert delete_index < asset_index
