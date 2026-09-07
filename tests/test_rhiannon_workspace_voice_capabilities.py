from types import SimpleNamespace

from fastapi.responses import Response

from aura_music_studio import aura_workspace_api
from aura_music_studio.rhiannon_voice_contracts import CapabilityState


class _ConfiguredSpeechService:
    def diagnostics(self):
        return {
            "stt_command_configured": True,
            "whisper_cli": None,
            "whisper_model_configured": False,
            "tts_command_configured": True,
            "tts_url_configured": True,
            "piper_model_configured": False,
        }


class _BrokenSpeechService:
    def diagnostics(self):
        raise RuntimeError("diagnostics unavailable")


def test_workspace_voice_configuration_does_not_claim_runtime_ready(monkeypatch):
    monkeypatch.setattr(aura_workspace_api, "AuraSpeechService", _ConfiguredSpeechService)

    status = aura_workspace_api._speech_status()

    assert status["stt_configured"] is True
    assert status["tts_configured"] is True
    assert status["tts_state"] == CapabilityState.CONFIGURED_UNVERIFIED.value
    assert status["tts_ready"] is False
    assert status["voice_contract"]["identity"]["identity"] == "rhiannon"
    assert status["voice_contract"]["boundaries"]["voice_cloning_engine_owned_here"] is False


def test_workspace_voice_diagnostics_failure_is_fail_closed(monkeypatch):
    monkeypatch.setattr(aura_workspace_api, "AuraSpeechService", _BrokenSpeechService)

    status = aura_workspace_api._speech_status()

    assert status["stt_configured"] is False
    assert status["tts_configured"] is False
    assert status["tts_state"] == CapabilityState.UNAVAILABLE.value
    assert status["tts_ready"] is False


def test_authenticated_voice_contract_surface_is_private_no_store(monkeypatch):
    monkeypatch.setattr(aura_workspace_api, "AuraSpeechService", _ConfiguredSpeechService)
    request = SimpleNamespace(state=SimpleNamespace(member=object()))
    response = Response()

    payload = aura_workspace_api.rhiannon_voice_contract(request, response)

    assert response.headers["cache-control"] == "private, no-store"
    assert payload["capability_protocol"] == "RhiannonVoice.capabilities/v1"
    assert payload["timing_protocol"] == "RhiannonVoice.timing/v1"
    assert payload["asset_provenance_protocol"] == "RhiannonVoice.asset-provenance/v1"
