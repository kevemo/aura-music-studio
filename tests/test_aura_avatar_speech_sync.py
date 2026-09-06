from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.aura_avatar_runtime import router
from aura_music_studio.aura_avatar_speech_sync import (
    MAX_SPEECH_TIMELINE_FRAMES,
    MAX_SPEECH_TIMELINE_MS,
    SPEECH_TIMELINE_PROTOCOL,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_speech_sync_script_is_wired_through_avatar_router():
    response = _client().get("/aura-intelligence/avatar-speech-sync.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"].startswith("application/javascript")


def test_speech_timeline_requires_authoritative_bounded_media_timing():
    script = _client().get("/aura-intelligence/avatar-speech-sync.js").text
    assert SPEECH_TIMELINE_PROTOCOL in script
    assert f"const MAX_FRAMES={MAX_SPEECH_TIMELINE_FRAMES};" in script
    assert f"const MAX_MS={MAX_SPEECH_TIMELINE_MS};" in script
    assert "at_ms" in script
    assert "frames must be ordered by at_ms" in script
    assert "requires an HTMLMediaElement in options.media" in script
    assert "Number(media.currentTime||0)*1000" in script
    assert "requestAnimationFrame" in script
    assert "authoritative_timing_required:true" in script
    assert "starts_media_playback:false" in script
    assert "fetches_audio:false" in script


def test_speech_timeline_controls_only_existing_avatar_performance_surface():
    script = _client().get("/aura-intelligence/avatar-speech-sync.js").text
    assert "host()?.speechFrame" in script
    assert "playSpeechTimeline" in script
    assert "cancelSpeechTimeline" in script
    assert "speechTimelineStatus" in script
    assert "aura:speech-timeline-input" in script
    assert "aura:performance" in script
    assert "eval(" not in script
    assert "new Function" not in script
    assert "fetch(" not in script
    assert "WebSocket" not in script
    assert "localStorage" not in script
    assert "document.cookie" not in script


def test_client_health_loader_installs_speech_sync_without_changing_auth_authority():
    script = _client().get("/aura-intelligence/avatar-client-health.js").text
    assert "SPEECH_SYNC='/aura-intelligence/avatar-speech-sync.js'" in script
    assert "data-aura-speech-sync" in script
    assert "loadSpeechSync();" in script
    # The loader only attaches the presentation synchronizer; it does not add a new
    # project/account/action endpoint or transfer any membership/role authority.
    assert "Authorization" not in script
    assert "role=" not in script
    assert "owner=" not in script
