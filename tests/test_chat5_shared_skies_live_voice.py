from __future__ import annotations

import json

import pytest
from fastapi import FastAPI

from aura_music_studio.rhiannon_voice_contracts import ConsentState, VoiceProfileKind
from aura_music_studio.shared_sky_control_room import StudioInvariantError
from aura_music_studio.shared_skies_live_voice import (
    build_emergency_voice_mute_snapshot,
    install_shared_skies_live_voice,
    project_voice_source,
    voice_runtime_truth,
)


def _programme_snapshot(source: dict) -> dict:
    return {
        "schema_version": 1,
        "scene": {"id": "scene-1", "name": "Live"},
        "sources": [source],
    }


def test_offline_stt_is_never_promoted_to_live_captions_or_realtime_conversion():
    truth = voice_runtime_truth(
        {
            "stt_command_configured": True,
            "whisper_model_configured": False,
            "tts_command_configured": True,
            "tts_url_configured": False,
            "piper_model_configured": False,
        }
    )
    assert truth["transcription_runtime"]["state"] == "offline_only"
    assert truth["transcription_runtime"]["real_time"] is False
    assert truth["real_time_voice_conversion"]["state"] == "unavailable"
    assert truth["real_time_voice_conversion"]["real_time"] is False
    assert truth["live_tts_queue"]["state"] == "unavailable"
    assert truth["speech_generation_runtime"]["state"] == "configured_unverified"


def test_voice_projection_is_redacted_and_preserves_canonical_provenance_indicator():
    source = {
        "id": "mic-1",
        "name": "Host Mic",
        "source_type": "microphone",
        "visible": True,
        "config": {
            "privacy": "programme_safe",
            "audio": {"gain": 1.2, "muted": False, "monitor": "headphones"},
            "voice_processing": {
                "enabled": True,
                "mode": "voice_conversion",
                "voice_profile_ref": "voice-123",
                "reference_files": ["/private/ref.wav"],
                "provider_url": "https://private.example/process",
            },
            "voice_asset_provenance": {
                "asset_id": "voice-asset-1",
                "generation_type": "voice_conversion",
                "voice_profile_ref": "voice-123",
                "voice_profile_kind": VoiceProfileKind.USER.value,
                "consent_state": ConsentState.EXPLICIT_CONSENT.value,
                "provider_runtime_class": "local",
                "generation_timestamp": "2026-09-06T00:00:00+00:00",
                "source_project_id": "project-1",
                "revision": "r1",
                "private": False,
                "synthetic_generated": True,
                "entitlement_ref": "entitlement-opaque-1",
            },
        },
    }
    projected = project_voice_source(source, "programme")
    assert projected is not None
    assert projected["voice_identity_indicator"] == "ai_converted_voice"
    assert projected["voice_processing"]["runtime_proven"] is False
    assert projected["voice_processing"]["real_time_proven"] is False
    assert projected["voice_asset_provenance"]["consent_state"] == "explicit_consent"
    assert projected["raw_config_exposed"] is False
    encoded = json.dumps(projected)
    assert "/private/ref.wav" not in encoded
    assert "private.example" not in encoded
    assert "entitlement-opaque-1" not in encoded


def test_private_voice_source_may_be_previewed_but_not_active_on_programme():
    source = {
        "id": "cue-1",
        "name": "Private Cue",
        "source_type": "audio",
        "visible": True,
        "config": {"privacy": "private", "audio": {"muted": False}},
    }
    preview = project_voice_source(source, "preview")
    assert preview is not None
    assert preview["private_or_backstage"] is True
    assert preview["programme_output_active"] is False
    with pytest.raises(StudioInvariantError):
        project_voice_source(source, "programme")


def test_secret_bearing_voice_config_fails_closed():
    source = {
        "id": "mic-secret",
        "name": "Mic",
        "source_type": "microphone",
        "visible": True,
        "config": {"api_secret": "do-not-expose", "audio": {}},
    }
    with pytest.raises(StudioInvariantError):
        project_voice_source(source, "preview")


def test_emergency_voice_mute_is_programme_only_and_idempotent():
    source = {
        "id": "mic-1",
        "name": "Host Mic",
        "source_type": "microphone",
        "visible": True,
        "config": {"privacy": "programme_safe", "audio": {"muted": False, "gain": 1.1}},
    }
    original = _programme_snapshot(source)
    muted, changed = build_emergency_voice_mute_snapshot(original, "mic-1")
    assert changed is True
    assert original["sources"][0]["config"]["audio"]["muted"] is False
    assert muted["sources"][0]["config"]["audio"]["muted"] is True
    muted_again, changed_again = build_emergency_voice_mute_snapshot(muted, "mic-1")
    assert changed_again is False
    assert muted_again["sources"][0]["config"]["audio"]["muted"] is True


def test_emergency_voice_mute_rejects_non_audio_source():
    snapshot = _programme_snapshot(
        {
            "id": "image-1",
            "name": "Poster",
            "source_type": "image",
            "visible": True,
            "config": {"privacy": "programme_safe"},
        }
    )
    with pytest.raises(StudioInvariantError):
        build_emergency_voice_mute_snapshot(snapshot, "image-1")


def test_live_voice_routes_mount_exactly_once():
    app = FastAPI()
    install_shared_skies_live_voice(app)
    install_shared_skies_live_voice(app)
    signatures = [
        (route.path, tuple(sorted(route.methods or set())))
        for route in app.router.routes
        if route.path.startswith("/shared-sky/studio/api/sessions/") and "/voice/" in route.path
    ]
    assert signatures.count(("/shared-sky/studio/api/sessions/{session_id}/voice/readiness", ("GET",))) == 1
    assert signatures.count(("/shared-sky/studio/api/sessions/{session_id}/voice/emergency/mute/{source_id}", ("POST",))) == 1
