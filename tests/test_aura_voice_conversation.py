from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from aura_music_studio.aura_voice_conversation import AuraVoiceConversationMiddleware, router


def _app():
    app = FastAPI()
    app.include_router(router)

    @app.get("/aura-intelligence", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse("<html><body><main>Aura</main></body></html>")

    @app.get("/other", response_class=HTMLResponse)
    def other_page():
        return HTMLResponse("<html><body>Other</body></html>")

    @app.get("/api-test")
    def api_test():
        return JSONResponse({"ok": True})

    app.add_middleware(AuraVoiceConversationMiddleware)
    return app


def test_voice_layer_injects_only_into_aura_html():
    client = TestClient(_app())
    aura = client.get("/aura-intelligence")
    assert aura.status_code == 200
    assert "/aura-intelligence/rhiannon-turn-state.js" in aura.text
    assert "/aura-intelligence/voice-conversation.js" in aura.text

    other = client.get("/other")
    assert "/aura-intelligence/rhiannon-turn-state.js" not in other.text
    assert "/aura-intelligence/voice-conversation.js" not in other.text

    api = client.get("/api-test")
    assert api.json() == {"ok": True}
    assert "rhiannon-turn-state.js" not in api.text
    assert "voice-conversation.js" not in api.text


def test_voice_script_uses_vad_audited_endpoints_and_truthful_avatar_speech_lifecycle():
    client = TestClient(_app())
    script = client.get("/aura-intelligence/voice-conversation.js")
    assert script.status_code == 200
    assert "voice-transcribe" in script.text
    assert "messages/${encodeURIComponent(message.id)}/speech" in script.text
    assert "requestAnimationFrame" in script.text
    assert "lastVoiceAt" in script.text
    assert "messagesCache" in script.text
    assert "await send(transcript)" in script.text
    assert "window.AuraHost?.performance?.speechFrame" in script.text
    assert "source:'tts_lifecycle'" in script.text
    assert "speaking:true" in script.text
    assert "speaking:false,viseme:'sil'" in script.text
    assert "precise phoneme/viseme frames remain unavailable until a timing-capable runtime supplies them" in script.text
    # The generic fallback TTS is audio-only. It must not synthesize fake phoneme timing.
    assert "phonemeTimes" not in script.text
    assert "fakeViseme" not in script.text
