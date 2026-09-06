from aura_music_studio.branding import (
    AI_PRODUCER_NAME,
    AI_SYSTEM_NAME,
    COMPANY_NAME,
    ENDORSEMENT,
    GAME_FORGE_NAME,
    LEGACY_PRODUCT_NAMES,
    PRODUCT_FULL_NAME,
    PRODUCT_NAME,
    PRODUCT_SHORT_NAME,
    RHIANNON_OS_NAME,
    SECURITY_PRODUCT_NAME,
    STREAMING_STUDIOS_NAME,
    TAGLINE,
    product_header,
)


def test_current_public_platform_identity_is_shared_skies_media():
    assert PRODUCT_NAME == "Shared Skies Media"
    assert PRODUCT_FULL_NAME == "Shared Skies Media"
    assert PRODUCT_SHORT_NAME == "Shared Skies Media"
    assert ENDORSEMENT == "Powered by Elevate Souls Productions"
    assert TAGLINE == "Elevate Your Soul Through Purposeful Media"
    assert COMPANY_NAME == "Elevate Souls Productions"
    assert product_header() == "Shared Skies Media — Powered by Elevate Souls Productions"


def test_current_rhiannon_and_specialist_product_names_are_canonical():
    assert AI_PRODUCER_NAME == "Rhiannon"
    assert AI_SYSTEM_NAME == "Rhiannon Intelligence Systems"
    assert RHIANNON_OS_NAME == "Shared Skies Rhiannon OS"
    assert SECURITY_PRODUCT_NAME == "Shared Skies Security — powered by Rhiannon Intelligence"
    assert STREAMING_STUDIOS_NAME == "Shared Skies Streaming Studios"
    assert GAME_FORGE_NAME == "Game Forge Creative Labs"


def test_obsolete_public_platform_names_are_compatibility_aliases_only():
    assert "Elevate Souls Productions Content Creation Command Center" in LEGACY_PRODUCT_NAMES
    assert "Content Creation Command Center" in LEGACY_PRODUCT_NAMES
    assert "Aura AI" in LEGACY_PRODUCT_NAMES
    assert "Aura AI Systems" in LEGACY_PRODUCT_NAMES
    assert PRODUCT_NAME not in LEGACY_PRODUCT_NAMES
    assert AI_SYSTEM_NAME not in LEGACY_PRODUCT_NAMES
