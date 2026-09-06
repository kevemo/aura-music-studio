from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.speech_api import router as speech_api_router
from aura_music_studio.speech_portal import router as speech_portal_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(speech_portal_router)
    app.include_router(speech_api_router)
    return TestClient(app)


def test_rhiannon_is_canonical_public_speech_surface_with_legacy_url_compatibility():
    client = _client()
    canonical = client.get("/rhiannon")
    legacy = client.get("/aura")
    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert "Talk to <span class='gold'>Rhiannon</span>" in canonical.text
    assert "Rhiannon Voice" in canonical.text
    assert "Talk to <span class='gold'>Aura</span>" not in canonical.text
    assert "Spoken Aura" not in canonical.text


def test_browser_fallback_is_explicitly_synthetic_and_not_voice_cloning():
    response = _client().get("/rhiannon")
    assert response.status_code == 200
    body = response.text
    assert "synthetic speech, not voice cloning" in body
    assert "SpeechSynthesisUtterance" in body
    assert "window.SpeechRecognition||window.webkitSpeechRecognition" in body
    assert "/speech/text-command" in body
    assert "browserRate" in body
    assert "browserPitch" in body
    assert "browserVoice" in body


def test_browser_fallback_does_not_bypass_canonical_command_planning():
    body = _client().get("/rhiannon").text
    assert "sendText(text)" in body
    assert "credentials:'same-origin'" in body
    assert "project_name:p||null" in body


def test_voice_media_drives_existing_avatar_state_without_new_execution_authority():
    body = _client().get("/rhiannon").text
    assert "data-aura-speech-media='1'" in body
    assert "window.AuraHost?.setState?." in body
    assert "source:'rhiannon_voice'" in body
    assert "auraState('listening'" in body
    assert "auraState('thinking'" in body
    assert "auraState('speaking'" in body
    assert "configured_audio_ended" in body
    assert "browser_fallback_ended" in body
    assert "speech_stopped" in body
    assert "eval(" not in body
    assert "new Function" not in body
    assert "WebSocket" not in body
    assert "localStorage" not in body
    assert "document.cookie" not in body
    assert "Authorization" not in body


def test_voice_object_urls_are_bounded_and_released():
    body = _client().get("/rhiannon").text
    assert "currentVoiceUrl" in body
    assert "URL.createObjectURL(blob)" in body
    assert "URL.revokeObjectURL(currentVoiceUrl)" in body
    assert "releaseVoiceUrl();" in body


def test_speech_api_uses_rhiannon_public_tag_while_internal_service_can_remain_compatible():
    app = FastAPI()
    app.include_router(speech_api_router)
    schema = app.openapi()
    operations = schema["paths"]["/speech/text-command"]["post"]
    assert "Rhiannon Voice" in operations["tags"]
