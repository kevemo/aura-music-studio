from __future__ import annotations

from aura_music_studio.rhiannon_voice_contracts import (
    CapabilityState,
    VoiceCapability,
    public_voice_contract,
    voice_capability_snapshot,
)


def _synthesis(diagnostics: dict):
    rows = voice_capability_snapshot(diagnostics)
    return next(row for row in rows if row.capability == VoiceCapability.SPEECH_SYNTHESIS)


def test_configured_speech_stays_unverified_without_health_evidence():
    row = _synthesis({"tts_command_configured": True})

    assert row.state == CapabilityState.CONFIGURED_UNVERIFIED
    assert row.health_evidence_verified is False
    assert row.health_evidence_source == ""


def test_verified_available_health_requires_configured_runtime():
    row = _synthesis(
        {
            "tts_health_verified": True,
            "tts_health_state": "available",
            "tts_health_source": "chat2-runtime-health",
        }
    )

    assert row.state == CapabilityState.UNAVAILABLE
    assert row.health_evidence_verified is True
    assert row.health_evidence_source == "chat2-runtime-health"


def test_verified_runtime_health_can_report_available_and_degraded():
    available = _synthesis(
        {
            "tts_url_configured": True,
            "tts_health_verified": True,
            "tts_health_state": "available",
            "tts_health_source": "authenticated-provider-probe",
        }
    )
    degraded = _synthesis(
        {
            "piper_model_configured": True,
            "tts_health_verified": True,
            "tts_health_state": "degraded",
            "tts_health_source": "local-runtime-supervisor",
        }
    )

    assert available.state == CapabilityState.AVAILABLE
    assert available.health_evidence_verified is True
    assert degraded.state == CapabilityState.DEGRADED
    assert degraded.health_evidence_verified is True


def test_missing_source_or_unknown_state_fails_closed_to_configured_unverified():
    missing_source = _synthesis(
        {
            "tts_command_configured": True,
            "tts_health_verified": True,
            "tts_health_state": "available",
        }
    )
    unknown_state = _synthesis(
        {
            "tts_command_configured": True,
            "tts_health_verified": True,
            "tts_health_state": "magically_perfect",
            "tts_health_source": "runtime",
        }
    )

    assert missing_source.state == CapabilityState.CONFIGURED_UNVERIFIED
    assert missing_source.health_evidence_verified is False
    assert unknown_state.state == CapabilityState.CONFIGURED_UNVERIFIED
    assert unknown_state.health_evidence_verified is False


def test_configured_unverified_cannot_be_supplied_as_verified_health_state():
    row = _synthesis(
        {
            "tts_url_configured": True,
            "tts_health_verified": True,
            "tts_health_state": "configured_unverified",
            "tts_health_source": "runtime",
        }
    )

    assert row.state == CapabilityState.CONFIGURED_UNVERIFIED
    assert row.health_evidence_verified is False


def test_public_contract_declares_server_evidence_boundary():
    contract = public_voice_contract(
        {
            "tts_url_configured": True,
            "tts_health_verified": True,
            "tts_health_state": "unhealthy",
            "tts_health_source": "authenticated-provider-probe",
        }
    )
    synthesis = next(
        row for row in contract["capabilities"] if row["capability"] == "speech_synthesis"
    )

    assert synthesis["state"] == "unhealthy"
    assert synthesis["health_evidence_verified"] is True
    assert contract["boundaries"]["runtime_health_requires_server_evidence"] is True
    assert contract["boundaries"]["configuration_implies_ready"] is False
    assert contract["boundaries"]["client_entitlement_authority"] is False
