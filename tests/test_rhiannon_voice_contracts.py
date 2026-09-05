import pytest
from pydantic import ValidationError

from aura_music_studio.rhiannon_voice_contracts import (
    CapabilityState,
    ConsentState,
    RhiannonSpeechRequest,
    SpeechTimingSpan,
    SpeechTimingTrack,
    TimingKind,
    VoiceAssetProvenance,
    VoiceCapability,
    VoiceProfileKind,
    public_voice_contract,
    voice_capability_snapshot,
)


def _by_capability(items):
    return {item.capability: item for item in items}


def test_voice_capability_snapshot_is_fail_closed_without_runtime_evidence():
    capabilities = _by_capability(voice_capability_snapshot({}))
    assert capabilities[VoiceCapability.SPEECH_SYNTHESIS].state == CapabilityState.UNAVAILABLE
    assert capabilities[VoiceCapability.VISEME_TIMING].state == CapabilityState.UNAVAILABLE
    assert capabilities[VoiceCapability.CLONED_SPEECH].consent_required is True
    assert capabilities[VoiceCapability.CLONED_SPEECH].premium_entitlement_required is True


def test_runtime_configuration_is_not_reported_as_healthy():
    capabilities = _by_capability(
        voice_capability_snapshot(
            {
                "tts_command_configured": True,
                "piper_model_configured": False,
                "tts_url_configured": True,
            }
        )
    )
    synthesis = capabilities[VoiceCapability.SPEECH_SYNTHESIS]
    assert synthesis.state == CapabilityState.CONFIGURED_UNVERIFIED
    assert synthesis.local_runtime_configured is True
    assert synthesis.remote_provider_configured is True
    assert synthesis.self_hosted is True


def test_public_contract_keeps_rhiannon_identity_separate_from_clone_authority():
    contract = public_voice_contract({})
    assert contract["identity"]["identity"] == "rhiannon"
    assert contract["identity"]["approved_voice_profile_ref"] == "rhiannon.system.voice.v1"
    assert contract["boundaries"]["voice_cloning_engine_owned_here"] is False
    assert contract["boundaries"]["voice_profile_creation_owned_here"] is False
    assert contract["boundaries"]["raw_model_embeddings_exposed"] is False
    assert contract["boundaries"]["provider_secrets_exposed"] is False
    assert contract["boundaries"]["client_entitlement_authority"] is False
    assert contract["boundaries"]["microphone_audio_auto_enrolled_for_cloning"] is False


def test_canonical_timing_accepts_ordered_viseme_and_word_spans():
    track = SpeechTimingTrack(
        audio_duration_ms=900,
        precise_timing=True,
        spans=[
            SpeechTimingSpan(kind=TimingKind.WORD, value="hello", start_ms=0, end_ms=420),
            SpeechTimingSpan(kind=TimingKind.VISEME, value="sil", start_ms=0, end_ms=40),
            SpeechTimingSpan(kind=TimingKind.VISEME, value="aa", start_ms=40, end_ms=260, confidence=0.95),
            SpeechTimingSpan(kind=TimingKind.VISEME, value="sil", start_ms=260, end_ms=420),
        ],
    )
    assert track.precise_timing is True
    assert track.spans[1].value == "sil"


def test_canonical_timing_rejects_unknown_viseme():
    with pytest.raises(ValidationError, match="unknown canonical viseme"):
        SpeechTimingSpan(
            kind=TimingKind.VISEME,
            value="provider_secret_shape",
            start_ms=0,
            end_ms=50,
        )


def test_canonical_timing_rejects_malformed_ranges_and_ordering():
    with pytest.raises(ValidationError, match="end_ms"):
        SpeechTimingSpan(kind=TimingKind.WORD, value="bad", start_ms=100, end_ms=50)

    with pytest.raises(ValidationError, match="exceeds audio duration"):
        SpeechTimingTrack(
            audio_duration_ms=100,
            spans=[SpeechTimingSpan(kind=TimingKind.WORD, value="late", start_ms=80, end_ms=120)],
        )

    with pytest.raises(ValidationError, match="ordered by start_ms"):
        SpeechTimingTrack(
            audio_duration_ms=500,
            spans=[
                SpeechTimingSpan(kind=TimingKind.VISEME, value="aa", start_ms=200, end_ms=250),
                SpeechTimingSpan(kind=TimingKind.VISEME, value="sil", start_ms=100, end_ms=150),
            ],
        )


def test_precise_timing_requires_real_viseme_metadata():
    with pytest.raises(ValidationError, match="requires canonical viseme"):
        SpeechTimingTrack(
            audio_duration_ms=500,
            precise_timing=True,
            spans=[SpeechTimingSpan(kind=TimingKind.WORD, value="hello", start_ms=0, end_ms=400)],
        )


def test_rhiannon_speech_request_expression_is_allowlisted():
    request = RhiannonSpeechRequest(text="Hello", expression="thoughtful")
    assert request.voice_profile_ref == "rhiannon.system.voice.v1"
    assert request.job_id.startswith("speech_")

    with pytest.raises(ValidationError, match="unsupported Rhiannon expression"):
        RhiannonSpeechRequest(text="Hello", expression="run-arbitrary-animation")


def test_voice_asset_provenance_preserves_safe_cross_studio_reference():
    provenance = VoiceAssetProvenance(
        asset_id="asset_voice_123",
        generation_type="speech_synthesis",
        voice_profile_ref="rhiannon.system.voice.v1",
        voice_profile_kind=VoiceProfileKind.RHIANNON_SYSTEM,
        consent_state=ConsentState.SYSTEM_APPROVED,
        provider_runtime_class="local",
        generation_timestamp="2026-09-06T00:00:00Z",
        source_project_id="project_123",
        revision="rev_4",
    )
    payload = provenance.model_dump(mode="json")
    assert payload["private"] is True
    assert payload["synthetic_generated"] is True
    assert "embedding" not in payload
    assert "provider_id" not in payload
    assert "secret" not in payload
