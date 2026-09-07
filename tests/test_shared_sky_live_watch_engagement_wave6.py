from __future__ import annotations

from fastapi.responses import HTMLResponse
from starlette.requests import Request

import aura_music_studio.shared_sky_live_watch_engagement_wave6 as wave6


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
            "headers": [(b"host", b"testserver"), (b"user-agent", b"pytest-wave6")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def _minimal_watch_html(*, battle: bool = True) -> str:
    battle_html = (
        "<div class='info'><span class='badge'>Battle</span><b> live</b></div>"
        if battle
        else ""
    )
    return (
        "<!doctype html><html><body>"
        "<div id='announce'></div>"
        f"{battle_html}"
        "<div class='info'><h2>Cosmic Creation Coin Gifts</h2>"
        "<p class='muted'>Gift sending is enabled by the authoritative economy service.</p>"
        "</div>"
        "</body></html>"
    )


def test_enhancer_injects_operational_gift_and_read_only_battle_surfaces_once():
    html = _minimal_watch_html()
    enhanced = wave6._enhance_watch_html(html, "live-1")

    assert enhanced != html
    assert enhanced.count("sharedSkyEngagementWave6") == 1
    assert "id='giftLivePanel'" in enhanced
    assert "id='battleLivePanel'" in enhanced
    assert "id='battleInitialPanel'" in enhanced

    # Re-running the enhancer is intentionally idempotent.
    assert wave6._enhance_watch_html(enhanced, "live-1") == enhanced
    assert enhanced.count("sharedSkyEngagementWave6") == 1


def test_enhancer_fails_safe_when_watch_v2_contract_marker_is_missing():
    html = "<!doctype html><html><body><p>different watch contract</p></body></html>"
    assert wave6._enhance_watch_html(html, "live-1") == html


def test_wave6_script_calls_canonical_gift_authority_and_read_only_battle_projection():
    enhanced = wave6._enhance_watch_html(_minimal_watch_html(), "live-1")

    assert "/shared-sky/live/api/watch/${encodeURIComponent(broadcastId)}/gift-display" in enhanced
    assert "/shared-sky/live/api/watch/${encodeURIComponent(broadcastId)}/battle-display" in enhanced
    assert "'/economy/me/gifts/send'" in enhanced
    assert "'Idempotency-Key':idempotencyKey()" in enhanced
    assert "method:'POST'" in enhanced

    # Chat 4 does not become Battle mutation/scoring authority.
    assert "method:'POST',credentials:'same-origin'" in enhanced
    assert "/shared-sky/battle" not in enhanced
    assert "/shared-sky/battles" not in enhanced
    assert "/battle/score" not in enhanced
    assert "battle_id:" not in enhanced
    assert "battle_round_id:" not in enhanced

    # Playback/media credentials remain entirely outside this viewer enhancement.
    assert "Authorization" not in enhanced
    assert "Bearer " not in enhanced
    assert "?token=" not in enhanced


def test_wave6_gift_payload_uses_server_projected_creator_and_live_identity():
    enhanced = wave6._enhance_watch_html(_minimal_watch_html(), "live-1")

    assert "recipient_creator_id:String(giftState.recipient_creator_id||'')" in enhanced
    assert "live_session_id:String(giftState.live_session_id||broadcastId)" in enhanced
    assert "gift_id:String(item.gift_id)" in enhanced
    assert "gift_version:Number(item.version)" in enhanced
    assert "quantity:1" in enhanced


def test_wave6_delegates_to_existing_playback_guard_and_preserves_failure(monkeypatch):
    source = HTMLResponse("denied", status_code=404, headers={"Cache-Control": "no-store"})
    monkeypatch.setattr(wave6, "watch_page_bridge_guard", lambda broadcast_id, request: source)

    response = wave6.watch_page_engagement_wave6("live-1", _request())

    assert response is source
    assert response.status_code == 404
    assert response.body == b"denied"


def test_wave6_delegates_then_enhances_success_without_caching(monkeypatch):
    source = HTMLResponse(_minimal_watch_html(), status_code=200)
    monkeypatch.setattr(wave6, "watch_page_bridge_guard", lambda broadcast_id, request: source)

    response = wave6.watch_page_engagement_wave6("live-1", _request())
    html = response.body.decode()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "sharedSkyEngagementWave6" in html
    assert "id='giftLivePanel'" in html
    assert "id='battleLivePanel'" in html
