from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from starlette.requests import Request

import aura_music_studio.shared_sky_live_bootstrap as bootstrap
import aura_music_studio.shared_sky_live_neighbor_wave4 as wave4
import aura_music_studio.shared_sky_live_watch_ui_v4 as watch_v4


def _request(
    path: str = "/watch/live-1",
    *,
    scheme: str = "https",
    host: str = "command.example",
    watch_intent: bool = True,
) -> Request:
    host_header = host.encode("ascii")
    headers = [(b"host", host_header), (b"user-agent", b"pytest-wave4")]
    if watch_intent:
        headers.append((b"x-shared-sky-playback-intent", b"watch"))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": (host, 443 if scheme == "https" else 80),
        }
    )


def test_same_origin_media_url_allows_relative_and_exact_origin_only():
    request = _request()
    assert wave4._same_origin_media_url(request, "/shared-sky/media/live-1/master.m3u8") == "/shared-sky/media/live-1/master.m3u8"
    assert wave4._same_origin_media_url(
        request, "https://command.example/shared-sky/media/live-1/master.m3u8"
    ) == "https://command.example/shared-sky/media/live-1/master.m3u8"

    assert wave4._same_origin_media_url(request, "https://cdn.example/master.m3u8") == ""
    assert wave4._same_origin_media_url(request, "http://command.example/master.m3u8") == ""
    assert wave4._same_origin_media_url(request, "https://user:secret@command.example/master.m3u8") == ""
    assert wave4._same_origin_media_url(request, "//command.example/master.m3u8") == ""
    assert wave4._same_origin_media_url(request, "javascript:alert(1)") == ""
    assert wave4._same_origin_media_url(request, "https://command.example/master.m3u8\nX: bad") == ""


def _ready_playback(*, manifest_url: str = "/shared-sky/media/live-1/master.m3u8") -> dict:
    return {
        "capability_state": "ready",
        "state": "live",
        "mode": "hls",
        "manifest_url": manifest_url,
        "authorization": {
            "scheme": "Bearer",
            "token": "server-only-bearer-secret",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        "browser_authorization": {
            "mode": "cookie_exchange",
            "exchange_url": "/shared-sky/media/live-1/authorize",
            "method": "POST",
            "credential_source": "authorization_bearer_once",
            "cookie_http_only": True,
            "cookie_path_scoped": True,
            "token_in_manifest_url": False,
        },
        "broadcast_id": "live-1",
        "dvr": True,
        "captions": [],
        "renditions": [],
    }


def _install_fake_chat2(monkeypatch: pytest.MonkeyPatch, playback: dict):
    seen: dict[str, str] = {}

    class FakeTransport:
        def playback(self, owner_user_id: str, broadcast_id: str):
            seen["owner_user_id"] = owner_user_id
            seen["broadcast_id"] = broadcast_id
            return dict(playback)

    def authorize(broadcast_id: str, *, authorization: str):
        seen["authorize_broadcast_id"] = broadcast_id
        seen["authorization"] = authorization
        response = Response(status_code=204)
        response.set_cookie(
            "shared_sky_playback",
            "server-only-bearer-secret",
            secure=True,
            httponly=True,
            samesite="strict",
            path=f"/shared-sky/media/{broadcast_id}/",
        )
        return response

    modules = {
        "aura_music_studio.shared_sky_transport_domain": SimpleNamespace(
            transport=FakeTransport()
        ),
        "aura_music_studio.shared_sky_internal_media_api": SimpleNamespace(
            authorize_shared_sky_media=authorize
        ),
    }
    monkeypatch.setattr(
        wave4.importlib,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(
        wave4.live.community,
        "_broadcast",
        lambda broadcast_id: {"id": broadcast_id, "user_id": "creator-1"},
    )
    return seen


def test_browser_session_requires_explicit_watch_intent_before_access_or_exchange(monkeypatch: pytest.MonkeyPatch):
    called = {"access": 0}

    def access(*args, **kwargs):
        called["access"] += 1
        return "viewer-1"

    monkeypatch.setattr(wave4, "_access_or_raise", access)
    with pytest.raises(HTTPException) as exc:
        wave4.create_browser_playback_session(
            "live-1",
            _request(watch_intent=False),
        )

    assert exc.value.status_code == 400
    assert "Watch playback intent" in str(exc.value.detail)
    assert called["access"] == 0


def test_browser_session_exchanges_bearer_server_side_and_never_returns_it(monkeypatch: pytest.MonkeyPatch):
    seen = _install_fake_chat2(monkeypatch, _ready_playback())
    monkeypatch.setattr(wave4, "_access_or_raise", lambda broadcast_id, request: "viewer-1")

    response = wave4.create_browser_playback_session("live-1", _request())
    payload = json.loads(response.body)
    rendered = response.body.decode()

    assert response.status_code == 200
    assert payload["available"] is True
    assert payload["manifest_url"] == "/shared-sky/media/live-1/master.m3u8"
    assert payload["browser_authorization_mode"] == "cookie_exchange"
    assert "server-only-bearer-secret" not in rendered
    assert "authorization" not in payload
    assert "shared_sky_playback=server-only-bearer-secret" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    assert seen["authorization"] == "Bearer server-only-bearer-secret"
    assert seen["owner_user_id"] == "creator-1"


def test_browser_session_rejects_cross_origin_media_before_cookie_exchange(monkeypatch: pytest.MonkeyPatch):
    seen = _install_fake_chat2(
        monkeypatch,
        _ready_playback(manifest_url="https://cdn.example/master.m3u8"),
    )
    monkeypatch.setattr(wave4, "_access_or_raise", lambda broadcast_id, request: "viewer-1")

    with pytest.raises(HTTPException) as exc:
        wave4.create_browser_playback_session("live-1", _request())

    assert exc.value.status_code == 503
    assert "same-origin" in str(exc.value.detail)
    assert "authorization" not in seen


def test_browser_session_requires_exact_secure_browser_contract(monkeypatch: pytest.MonkeyPatch):
    playback = _ready_playback()
    playback["browser_authorization"]["token_in_manifest_url"] = True
    seen = _install_fake_chat2(monkeypatch, playback)
    monkeypatch.setattr(wave4, "_access_or_raise", lambda broadcast_id, request: "viewer-1")

    with pytest.raises(HTTPException) as exc:
        wave4.create_browser_playback_session("live-1", _request())

    assert exc.value.status_code == 503
    assert "secure browser playback exchange" in str(exc.value.detail)
    assert "authorization" not in seen


def test_browser_session_fails_if_chat2_does_not_return_httponly_cookie(monkeypatch: pytest.MonkeyPatch):
    playback = _ready_playback()

    class FakeTransport:
        def playback(self, owner_user_id: str, broadcast_id: str):
            return playback

    def weak_authorize(broadcast_id: str, *, authorization: str):
        response = Response(status_code=204)
        response.set_cookie("shared_sky_playback", "value", secure=True, httponly=False)
        return response

    modules = {
        "aura_music_studio.shared_sky_transport_domain": SimpleNamespace(
            transport=FakeTransport()
        ),
        "aura_music_studio.shared_sky_internal_media_api": SimpleNamespace(
            authorize_shared_sky_media=weak_authorize
        ),
    }
    monkeypatch.setattr(wave4.importlib, "import_module", lambda name: modules[name])
    monkeypatch.setattr(wave4.live.community, "_broadcast", lambda _: {"user_id": "creator-1"})
    monkeypatch.setattr(wave4, "_access_or_raise", lambda broadcast_id, request: "viewer-1")

    with pytest.raises(HTTPException) as exc:
        wave4.create_browser_playback_session("live-1", _request())
    assert exc.value.status_code == 503
    assert "HttpOnly" in str(exc.value.detail)


def test_battle_display_adapter_maps_viewer_safe_snapshot_and_validates_live_session():
    snapshot = {
        "battle": {
            "id": "battle-1",
            "live_session_id": "live-1",
            "status": "active",
            "mode": "1v1",
        },
        "participants": [
            {"participant_id": "p1", "user_id": "creator-1", "stage_state": "stage"},
            {"participant_id": "p2", "user_id": "creator-2", "stage_state": "stage"},
        ],
        "teams": [{"id": "team-1", "name": "Team 1"}],
        "scores": {"round:p:p1": 10},
        "score_version": 3,
        "event_cursor": 19,
        "remaining_ms": 42000,
        "current_round": {"round_index": 1},
        "result": None,
    }
    adapter = wave4.Chat6BattleDisplayAdapter(lambda live_session_id: snapshot)
    state = adapter.state("live-1", "viewer-1")

    assert state["available"] is True
    assert state["battle_id"] == "battle-1"
    assert state["score_version"] == 3
    assert state["event_cursor"] == 19
    assert len(state["participants"]) == 2
    assert state["source"] == "chat6_viewer_snapshot"

    mismatch = wave4.Chat6BattleDisplayAdapter(
        lambda live_session_id: {
            **snapshot,
            "battle": {**snapshot["battle"], "live_session_id": "another-live"},
        }
    ).state("live-1", None)
    assert mismatch["available"] is False
    assert mismatch["reason"] == "battle_live_session_mismatch"


def test_battle_display_adapter_stays_unavailable_when_no_active_battle():
    state = wave4.Chat6BattleDisplayAdapter(lambda _: None).state("live-1", None)
    assert state == {
        "available": False,
        "reason": "no_active_battle",
        "source": "chat6_battles",
    }


def test_wave4_watch_wrapper_injects_post_exchange_without_bearer_or_token_storage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        watch_v4,
        "watch_page_v2",
        lambda broadcast_id, request: HTMLResponse(
            "<!doctype html><html><body><div id='playerStatus'><button>Retry playback</button></div></body></html>",
            headers={"Cache-Control": "no-store"},
        ),
    )

    response = watch_v4.watch_page_v4("live-1", _request())
    html = response.body.decode()

    assert response.status_code == 200
    assert "browser-playback-session" in html
    assert "method:'POST'" in html
    assert "X-Shared-Sky-Playback-Intent" in html
    assert "/shared-sky/media/${encoded}/authorize" in html
    assert "method:'DELETE'" in html
    assert "localStorage" not in watch_v4._BROWSER_EXCHANGE_SCRIPT
    assert "Bearer " not in watch_v4._BROWSER_EXCHANGE_SCRIPT
    assert "authorization_bearer_once" not in watch_v4._BROWSER_EXCHANGE_SCRIPT
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_bootstrap_makes_wave4_watch_route_canonical_and_mounts_neighbor_routes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bootstrap, "configure_neighbor_live_integrations", lambda: {})
    monkeypatch.setattr(bootstrap, "harden_browser_playback_integration", lambda: {})
    monkeypatch.setattr(bootstrap, "install_live_community_hardening", lambda: None)
    monkeypatch.setattr(bootstrap, "configure_wave4_neighbor_adapters", lambda: {})
    app = FastAPI()

    bootstrap.install_shared_sky_live_community(app)

    watch_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/watch/{broadcast_id}"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    session_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None)
        == "/shared-sky/live/api/watch/{broadcast_id}/browser-playback-session"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    battle_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None)
        == "/shared-sky/live/api/watch/{broadcast_id}/battle-display"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]

    assert len(watch_routes) == 1
    assert watch_routes[0].endpoint.__name__ == "watch_page_v4"
    assert len(session_routes) == 1
    assert len(battle_routes) == 1
