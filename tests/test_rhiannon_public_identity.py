from aura_music_studio import branding
from aura_music_studio.aura_agent_core import AURA_CORE_SYSTEM


def test_public_identity_is_rhiannon_and_rhian():
    assert branding.AI_SYSTEM_NAME == "Rhiannon Intelligence Systems"
    assert branding.AI_PRODUCER_NAME == "Rhian"
    assert branding.ENDORSEMENT == "Powered by Rhiannon Intelligence Systems"
    assert "Aura AI" not in branding.ENDORSEMENT


def test_runtime_prompt_identifies_rhian_without_renaming_compatibility_api():
    assert "You are Rhian" in AURA_CORE_SYSTEM
    assert "Rhiannon Intelligence Systems" in AURA_CORE_SYSTEM
    assert "Elevate Souls Productions Content Creation Command Center" in AURA_CORE_SYSTEM
    assert "You are Aura" not in AURA_CORE_SYSTEM


def test_legacy_ai_names_are_explicit_compatibility_metadata():
    assert "Aura" in branding.LEGACY_AI_NAMES
    assert "Aura AI" in branding.LEGACY_AI_NAMES
    assert "Aura AI Systems" in branding.LEGACY_AI_NAMES
