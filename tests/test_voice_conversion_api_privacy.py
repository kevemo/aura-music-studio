from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio import vocal_api


def test_voice_conversion_generation_response_does_not_expose_private_work_path(tmp_path, monkeypatch):
    monkeypatch.setattr(vocal_api, "_project", lambda _name: tmp_path)
    monkeypatch.setattr(vocal_api, "_audio_asset", lambda _project, _asset_id: object())
    monkeypatch.setattr(
        vocal_api,
        "generate_voice_conversion_candidate",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="candidate-1",
            candidate_path="work/voice_conversion/audio/private.wav",
            state="ready",
            voice_profile_id="voice-1",
            source_asset_id="asset-1",
        ),
    )

    payload = vocal_api.voice_convert(
        "demo",
        vocal_api.VoiceConvertRequest(
            source_asset_id="asset-1",
            voice_profile_id="voice-1",
            similarity=0.8,
            pitch_shift=0,
        ),
    )

    assert payload["candidate_id"] == "candidate-1"
    assert payload["audition_required"] is True
    assert payload["authoritative_daw_mutated"] is False
    assert "path" not in payload
    assert "candidate_path" not in payload
