from aura_music_studio import branding
from aura_music_studio.brand_migration import rebrand_text


def test_authoritative_command_center_brand_constants():
    assert branding.PRODUCT_FULL_NAME == "Elevate Souls Productions Content Creation Command Center"
    assert branding.PRODUCT_SHORT_NAME == "Content Creation Command Center"
    assert branding.ENDORSEMENT == "Powered by Rhiannon Intelligence Systems"
    assert branding.COMPANY_NAME == "Elevate Souls Productions"
    assert branding.AI_SYSTEM_NAME == "Rhiannon Intelligence Systems"
    assert branding.AI_PRODUCER_NAME == "Rhian"
    assert branding.BRAND_LOGO_PATH == "/static/elevate-souls-command-center-logo.svg"
    assert "Pulsar-Frequency House" in branding.LEGACY_PRODUCT_NAMES
    assert "Aura" in branding.LEGACY_AI_NAMES
    assert "Pulsar-Frequency House" not in branding.product_header()
    assert "Aura AI" not in branding.product_header()


def test_legacy_pulsar_public_copy_is_rewritten():
    source = (
        "<title>Pulsar-Frequency House</title>"
        "<h1>Pulsar-Frequency House</h1>"
        "<p>Powered by Elevate Souls Productions & Aura AI Systems</p>"
        "<img src='/static/pulsar-frequency-house-logo.svg'>"
    )
    branded = rebrand_text(source)
    assert "Pulsar-Frequency House" not in branded
    assert "Aura AI Systems" not in branded
    assert branded.count("Elevate Souls Productions Content Creation Command Center") == 2
    assert "Powered by Rhiannon Intelligence Systems" in branded
    assert "/static/elevate-souls-command-center-logo.svg" in branded


def test_previous_tagline_is_rewritten_to_esp_purpose_tagline():
    branded = rebrand_text("For Professional Creation Beyond The Cosmos")
    assert branded == "Elevate Your Soul Through Purposeful Media"


def test_rebrand_does_not_rewrite_internal_identifier_style_text():
    source = "development/full-site-build aura_music_studio esp_shop_provider_runtime"
    assert rebrand_text(source) == source
