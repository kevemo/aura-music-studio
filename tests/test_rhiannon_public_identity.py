from aura_music_studio import branding
from aura_music_studio.aura_agent_core import AURA_CORE_SYSTEM
from aura_music_studio.aura_context_extensions import _inject_messages


def test_public_identity_is_rhiannon_and_rhian():
    assert branding.AI_SYSTEM_NAME == "Rhiannon Intelligence Systems"
    assert branding.AI_PRODUCER_NAME == "Rhian"
    assert branding.ENDORSEMENT == "Powered by Rhiannon Intelligence Systems"
    assert "Aura AI" not in branding.ENDORSEMENT


def test_runtime_prompt_rebrands_at_inference_boundary_without_renaming_compatibility_core():
    # The stored core signature remains stable so legacy context/profile detection keeps working.
    assert "You are Aura" in AURA_CORE_SYSTEM

    injected = _inject_messages(
        [{"role": "system", "content": AURA_CORE_SYSTEM}],
        "brand-regression-user",
        "brand-regression-thread",
    )
    prompt = str(injected[0]["content"])
    assert "You are Rhian" in prompt
    assert "Rhiannon Intelligence Systems" in prompt
    assert "Elevate Souls Productions Content Creation Command Center" in prompt
    assert "You are Aura" not in prompt


def test_legacy_ai_names_are_explicit_compatibility_metadata():
    assert "Aura" in branding.LEGACY_AI_NAMES
    assert "Aura AI" in branding.LEGACY_AI_NAMES
    assert "Aura AI Systems" in branding.LEGACY_AI_NAMES
