from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import branding
from aura_music_studio.brand_migration import rebrand_text
from aura_music_studio.brand_ui import LOGO_PATH, hero_logo, router as brand_router


def test_authoritative_command_center_brand_constants():
    assert branding.PRODUCT_FULL_NAME == "Elevate Souls Productions Content Creation Command Center"
    assert branding.PRODUCT_SHORT_NAME == "Content Creation Command Center"
    assert branding.ENDORSEMENT == "Powered by Aura AI"
    assert branding.COMPANY_NAME == "Elevate Souls Productions"
    assert branding.AI_SYSTEM_NAME == "Aura AI"
    assert branding.BRAND_LOGO_PATH == "/brand/esp-logo.webp"
    assert branding.BRAND_LOGO_FILENAME == "esp-content-creation-command-center-brand.webp"
    assert "Pulsar-Frequency House" in branding.LEGACY_PRODUCT_NAMES
    assert "Pulsar-Frequency House" not in branding.product_header()


def test_supplied_command_center_artwork_is_packaged_and_served_by_global_brand_route():
    assert LOGO_PATH.name == branding.BRAND_LOGO_FILENAME
    assert LOGO_PATH.is_file()
    payload = LOGO_PATH.read_bytes()
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WEBP"

    app = FastAPI()
    app.include_router(brand_router)
    response = TestClient(app).get(branding.BRAND_LOGO_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WEBP"

    favicon = TestClient(app).get("/favicon.webp")
    assert favicon.status_code == 200
    assert favicon.content == response.content


def test_hero_logo_uses_current_identity_and_stable_brand_route():
    html = hero_logo()
    assert branding.BRAND_LOGO_PATH in html
    assert branding.PRODUCT_FULL_NAME in html
    assert "Pulsar-Frequency" not in html


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
    assert "Powered by Aura AI" in branded
    assert branding.BRAND_LOGO_PATH in branded
    assert "pulsar-frequency-house-logo.svg" not in branded


def test_previous_tagline_is_rewritten_to_esp_purpose_tagline():
    branded = rebrand_text("For Professional Creation Beyond The Cosmos")
    assert branded == "Elevate Your Soul Through Purposeful Media"


def test_rebrand_does_not_rewrite_internal_identifier_style_text():
    source = "development/full-site-build aura_music_studio esp_shop_provider_runtime"
    assert rebrand_text(source) == source
