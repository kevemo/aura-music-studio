from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from aura_music_studio.aura_realtime_voice_ui import REALTIME_VOICE_SCRIPT, router as realtime_ui_router
from aura_music_studio.aura_voice_conversation import AuraVoiceConversationMiddleware, router as voice_router


def test_realtime_voice_script_uses_only_ephemeral_browser_credential():
    script = REALTIME_VOICE_SCRIPT
    assert "OPENAI_API_KEY" not in script
    assert "/realtime-client-secret" in script
    assert "client_secret" in script
    assert "https://api.openai.com/v1/realtime/calls" in script
    assert "Authorization':`Bearer ${ephemeralKey}`" in script
    assert "Content-Type':'application/sdp'" in script


def test_realtime_voice_script_builds_bounded_audio_webrtc_session():
    script = REALTIME_VOICE_SCRIPT
    assert "new RTCPeerConnection()" in script
    assert "getUserMedia({audio:" in script
    assert "pc.addTrack(track,micStream)" in script
    assert "pc.createDataChannel('oai-events')" in script
    assert "pc.createOffer()" in script
    assert "pc.setRemoteDescription(answer)" in script
    assert "session.update" not in script
    assert "conversation.item.create" not in script
    assert "tools" not in script.lower()


def test_realtime_voice_ui_preserves_standard_voice_fallback():
    script = REALTIME_VOICE_SCRIPT
    assert "legacyClick=b.onclick" in script
    assert "legacyClick.call(b)" in script
    assert "Realtime voice is not configured on this host; using standard Rhiannon Voice." in script
    assert "RTCPeerConnection==='undefined'" in script


def test_realtime_voice_ui_cleans_up_microphone_peer_and_audio():
    script = REALTIME_VOICE_SCRIPT
    assert "track.stop()" in script
    assert "pc.close()" in script
    assert "remoteAudio.remove()" in script
    assert "visibilitychange" in script
    assert "beforeunload" in script


def test_realtime_script_route_is_mounted_and_hardened():
    app = FastAPI()
    app.include_router(realtime_ui_router)
    client = TestClient(app)
    response = client.get("/aura-intelligence/realtime-voice.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "RTCPeerConnection" in response.text


def test_voice_workspace_injects_fallback_before_realtime_layer():
    app = FastAPI()
    app.add_middleware(AuraVoiceConversationMiddleware)

    @app.get("/aura-intelligence")
    def workspace():
        return HTMLResponse("<html><body><main>Aura</main></body></html>")

    client = TestClient(app)
    response = client.get("/aura-intelligence")
    assert response.status_code == 200
    turn_state = "<script src='/aura-intelligence/rhiannon-turn-state.js'></script>"
    fallback = "<script src='/aura-intelligence/voice-conversation.js'></script>"
    realtime = "<script src='/aura-intelligence/realtime-voice.js'></script>"
    assert turn_state in response.text
    assert fallback in response.text
    assert realtime in response.text
    assert response.text.index(turn_state) < response.text.index(fallback) < response.text.index(realtime)


def test_voice_overlay_router_dispatches_realtime_ui_route():
    app = FastAPI()
    app.include_router(voice_router)
    client = TestClient(app)
    response = client.get("/aura-intelligence/realtime-voice.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "RTCPeerConnection" in response.text
