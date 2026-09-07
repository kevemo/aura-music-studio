from __future__ import annotations


def test_self_host_policy_prefers_local_aura_runtime():
    from aura_music_studio.aura_self_host_control import CAPABILITY_POLICIES

    policies = {row.capability: row for row in CAPABILITY_POLICIES}
    for capability in (
        "music_generation",
        "singing_synthesis",
        "voice_conversion",
        "speech_to_text",
        "text_to_speech",
        "source_separation",
        "mastering",
        "mixing_dsp",
        "audio_to_midi",
        "project_storage",
        "member_database",
        "overlay_rendering",
        "live_automation",
        "moderation_decision_support",
        "support_knowledge_base",
    ):
        assert policies[capability].preferred_mode == "self_host"


def test_external_services_are_explicit_exceptions_not_default_architecture():
    from aura_music_studio.aura_self_host_control import CAPABILITY_POLICIES

    policies = {row.capability: row for row in CAPABILITY_POLICIES}
    assert policies["tiktok_live_provider_actions"].preferred_mode == "external_required"
    assert policies["social_network_publishing"].preferred_mode == "external_required"
    assert policies["card_payment_processing"].preferred_mode == "external_required"
    assert policies["public_email_delivery"].preferred_mode == "hybrid"
    assert "approved TikTok or partner transport" in policies["tiktok_live_provider_actions"].external_boundary
    assert "raw card processing is not self-hosted" in policies["card_payment_processing"].external_boundary


def test_self_host_report_does_not_fake_runtime_readiness(monkeypatch):
    from aura_music_studio import aura_self_host_control as mod

    monkeypatch.setattr(
        mod.EngineManager,
        "status",
        lambda self: [
            {"name": name, "installed": False, "command_configured": False, "maturity": "test", "deployment": "local"}
            for policy in mod.CAPABILITY_POLICIES
            for name in policy.local_engines
        ],
    )
    report = mod.self_host_report()
    music = next(row for row in report["capabilities"] if row["capability"] == "music_generation")
    storage = next(row for row in report["capabilities"] if row["capability"] == "project_storage")
    assert report["policy"] == "self_host_first"
    assert report["external_services_are_exceptions"] is True
    assert music["self_host_ready"] is False
    assert storage["self_host_ready"] is True
    assert "not reported self-host ready merely because code exists" in report["truth_boundary"]


def test_local_engine_mapping_uses_existing_engine_manager_catalog():
    from aura_music_studio.aura_self_host_control import CAPABILITY_POLICIES
    from aura_music_studio.engine_manager import ENGINES

    known = {engine.name for engine in ENGINES}
    declared = {name for policy in CAPABILITY_POLICIES for name in policy.local_engines}
    assert declared <= known
    assert "ace-step-1.5" in declared
    assert "whisper-cpp" in declared
    assert "piper-tts" in declared
    assert "audio-separator" in declared
    assert "matchering" in declared


def test_self_host_report_contains_no_secret_values(monkeypatch):
    from aura_music_studio import aura_self_host_control as mod

    monkeypatch.setenv("AURA_SELF_HOST_MAIL_READY", "1")
    report = mod.self_host_report()
    text = repr(report).lower()
    for forbidden in ("password", "access_token", "private_key", "cookie", "bearer "):
        assert forbidden not in text
