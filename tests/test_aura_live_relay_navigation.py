from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

import aura_music_studio.aura_live_overlay_studio as studio


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/live-overlay-studio",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    request.state.member = SimpleNamespace(
        user_id="creator-relay-nav-test",
        plan=SimpleNamespace(id="free"),
    )
    return request


def test_overlay_studio_exposes_relay_without_faking_provider_connection(monkeypatch):
    profile = {
        "gift_sound_muted": False,
        "all_audio_muted": False,
        "tts_gifts_enabled": True,
        "tts_chat_enabled": False,
        "welcome_enabled": True,
        "welcome_audience": "followers",
        "welcome_template": "Welcome {username}!",
        "voice_mode": "browser",
    }
    monkeypatch.setattr(studio, "_ensure_profile", lambda _user_id: (profile, None))

    response = studio.live_overlay_studio(_request())
    html = response.body.decode("utf-8")

    assert "href='/live-overlay-studio/connector'" in html
    assert "Configure LIVE Event Relay" in html
    assert "Provider access remains external" in html
    assert "does not claim a direct TikTok connection" in html
    assert "provider connected" not in html.lower()
