from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.requests import Request

import aura_music_studio.shared_sky_live_bootstrap as bootstrap
import aura_music_studio.shared_sky_live_battle_bridge as battle_bridge
import aura_music_studio.shared_sky_live_watch_bridge_guard as watch_guard
import aura_music_studio.shared_sky_live_watch_engagement_wave6 as watch_engagement


def _request(path: str = "/watch/live-1") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"command.example"), (b"user-agent", b"pytest-wave4-current")],
            "client": ("127.0.0.1", 12345),
            "server": ("command.example", 443),
        }
    )


def _bootstrap_html() -> str:
    return """<!doctype html><html><body>
<video id='video' controls playsinline preload='metadata' src='/shared-sky/media/live-1/bootstrap'><track kind='captions' src='https://captions.example/en.vtt' srclang='en'></video>
<script id='initialState' type='application/json'>{"playback":{"available":true,"manifest_url":"/shared-sky/media/live-1/bootstrap","browser_authorization_mode":"cookie_bootstrap_redirect"}}</script>
<script>
function nativeHlsSupported(v){return Boolean(v.canPlayType('application/vnd.apple.mpegurl'))}
function populateQuality(d){const q={add(){}};for(const r of d.renditions||[]){if(!r||!r.manifest_url)continue;const u=r.manifest_url;if(u)q.add(new Option(r.name||'Rendition',u))}}
function applyDescriptor(d){descriptor=d||{};const src=descriptor.manifest_url;const v={};let path='';if(path.endsWith('.m3u8')&&!nativeHlsSupported(v)){return}}
applyDescriptor(descriptor);history();join();events();refreshInteractives();
</script></body></html>"""


def test_cookie_bootstrap_guard_removes_eager_media_fetch_and_treats_redirect_as_hls():
    hardened = watch_guard._harden_cookie_bootstrap_html(_bootstrap_html())

    opening_video = hardened.split("</video>", 1)[0]
    assert "src='/shared-sky/media/live-1/bootstrap'" not in opening_video
    assert "preload='metadata' hidden" in opening_video
    assert "<track" not in opening_video
    assert "bootstrapHls=descriptor.browser_authorization_mode==='cookie_bootstrap_redirect'" in hardened
    assert "(bootstrapHls||path.endsWith('.m3u8'))&&!nativeHlsSupported(v)" in hardened
    assert "d.browser_authorization_mode!=='cookie_bootstrap_redirect'||new URL(u,location.origin).origin===location.origin" in hardened
    # The token-free bootstrap remains in inert initial JSON so supported browsers can use it only
    # after the capability guard runs.
    assert '"manifest_url":"/shared-sky/media/live-1/bootstrap"' in hardened
    assert "authorization" not in hardened.lower() or "browser_authorization_mode" in hardened


def test_non_cookie_playback_html_is_unchanged():
    html = "<video id='video' src='/replay.mp4'></video><script>{\"browser_authorization_mode\":\"none\"}</script>"
    assert watch_guard._harden_cookie_bootstrap_html(html) == html


def test_watch_guard_endpoint_delegates_to_wave2_and_hardens_only_success(monkeypatch):
    monkeypatch.setattr(watch_guard, "watch_page_v2", lambda broadcast_id, request: HTMLResponse(_bootstrap_html()))
    response = watch_guard.watch_page_bridge_guard("live-1", _request())
    body = response.body.decode()
    assert response.status_code == 200
    assert "preload='metadata' hidden" in body
    assert response.headers["cache-control"] == "no-store"

    monkeypatch.setattr(
        watch_guard,
        "watch_page_v2",
        lambda broadcast_id, request: HTMLResponse("not available", status_code=404),
    )
    unavailable = watch_guard.watch_page_bridge_guard("live-1", _request())
    assert unavailable.status_code == 404
    assert unavailable.body == b"not available"


def test_bootstrap_mounts_one_canonical_watch_engagement_route_over_guard(monkeypatch):
    monkeypatch.setattr(bootstrap, "configure_neighbor_live_integrations", lambda: {})
    monkeypatch.setattr(bootstrap, "harden_browser_playback_integration", lambda: {})
    monkeypatch.setattr(bootstrap, "install_chat2_browser_playback_bridge", lambda: {})
    monkeypatch.setattr(bootstrap, "install_chat6_battle_viewer_bridge", lambda: {})
    monkeypatch.setattr(bootstrap, "install_live_community_hardening", lambda: None)
    app = FastAPI()

    bootstrap.install_shared_sky_live_community(app)
    routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/watch/{broadcast_id}"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].endpoint.__name__ == "watch_page_engagement_wave6"
    # Wave 6 remains a composition layer over the previously validated playback guard rather than
    # replacing Chat 2/4 playback authorization with a second implementation.
    assert watch_engagement.watch_page_bridge_guard is watch_guard.watch_page_bridge_guard


def _battle_snapshot(live_session_id: str = "live-1") -> dict:
    return {
        "battle": {"id": "battle-1", "live_session_id": live_session_id, "status": "active", "mode": "1v1"},
        "participants": [{"participant_id": "p1", "user_id": "creator-1", "stage_state": "stage"}],
        "teams": [{"id": "team-1", "name": "Team 1"}],
        "scores": {"round-1:participant:p1": 12},
        "score_version": 3,
        "event_cursor": 9,
        "remaining_ms": 42000,
        "current_round": {"round_index": 1},
        "result": None,
    }


def test_chat6_adapter_accepts_only_matching_viewer_safe_live_snapshot():
    adapter = battle_bridge.Chat6BattleDisplayAdapter(lambda live_id: _battle_snapshot(live_id))
    state = adapter.state("live-1", None)
    assert state["available"] is True
    assert state["battle_id"] == "battle-1"
    assert state["score_version"] == 3
    assert state["event_cursor"] == 9

    mismatch = battle_bridge.Chat6BattleDisplayAdapter(lambda live_id: _battle_snapshot("other-live"))
    rejected = mismatch.state("live-1", None)
    assert rejected == {
        "available": False,
        "reason": "battle_live_session_mismatch",
        "source": "chat6_viewer_bridge",
    }


def test_chat6_bridge_remains_pending_without_explicit_live_lookup(monkeypatch):
    class _BattleApiWithoutLiveLookup:
        pass

    monkeypatch.setattr(
        battle_bridge.importlib,
        "import_module",
        lambda name: _BattleApiWithoutLiveLookup(),
    )
    state = battle_bridge.install_chat6_battle_viewer_bridge()
    assert state["state"] == "pending"
    assert state["reason"] == "chat6_viewer_live_battle_lookup_unavailable"
