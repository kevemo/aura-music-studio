from __future__ import annotations

from aura_music_studio.shared_sky_live_browser_playback import BrowserSafeChat2PlaybackAdapter


class Community:
    def _broadcast(self, broadcast_id: str) -> dict:
        return {"id": broadcast_id, "user_id": "creator-1", "state": "live"}


class Transport:
    def __init__(self, playback: dict):
        self.playback_state = playback

    def status(self, user_id: str, broadcast_id: str) -> dict:
        assert user_id == "creator-1" and broadcast_id == "live-1"
        return {
            "session": {"state": "live", "rendition_profile": {}},
            "playback": self.playback_state,
            "recordings": [],
        }

    def playback(self, user_id: str, broadcast_id: str) -> dict:
        raise AssertionError("status() already supplied playback")


def test_bearer_hls_descriptor_fails_closed_for_native_video_runtime():
    adapter = BrowserSafeChat2PlaybackAdapter(
        Transport(
            {
                "capability_state": "ready",
                "state": "live",
                "manifest_url": "https://media.example/live-1/master.m3u8",
                "authorization": {
                    "scheme": "Bearer",
                    "token": "server-token",
                    "expires_at": "2026-09-05T03:00:00+00:00",
                },
            }
        ),
        Community(),
    )
    result = adapter.descriptor("live-1", "viewer-1")
    assert result["available"] is False
    assert result["state"] == "unavailable"
    assert result["reason"] == "browser_bearer_playback_runtime_pending"
    assert result["manifest_url"] is None
    assert result["authorization"] is None
    assert result["token_expires_at"] is None
    assert result["browser_authorization_mode"] == "bearer_header_requires_hls_runtime"


def test_native_video_descriptor_without_custom_header_remains_playable():
    adapter = BrowserSafeChat2PlaybackAdapter(
        Transport(
            {
                "capability_state": "ready",
                "state": "live",
                "manifest_url": "https://media.example/live-1/master.m3u8",
                "authorization": {},
            }
        ),
        Community(),
    )
    result = adapter.descriptor("live-1", None)
    assert result["available"] is True
    assert result["state"] == "ready"
    assert result["manifest_url"] == "https://media.example/live-1/master.m3u8"
    assert result["browser_authorization_mode"] == "none"
