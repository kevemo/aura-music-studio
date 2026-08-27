from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import branding
from aura_music_studio.brand_migration import rebrand_text
from aura_music_studio.brand_ui import LOGO_PATH, hero_logo, router as brand_router


def test_authoritative_public_brand_is_esp_content_creation_command_center():
    assert branding.PRODUCT_FULL_NAME == "Elevate Souls Productions Content Creation Command Center"
    assert branding.PRODUCT_SHORT_NAME == "ESP Content Creation Command Center"
    assert branding.ENDORSEMENT == "Powered by Aura AI"
    assert branding.BRAND_ASSET_PATH == "/brand/esp-logo.webp"
    assert branding.BRAND_ASSET_FILENAME == "esp-content-creation-command-center-brand.webp"
    assert branding.product_header().startswith(branding.PRODUCT_FULL_NAME)


def test_official_brand_artwork_is_packaged_webp_and_served_by_existing_route():
    assert LOGO_PATH.name == branding.BRAND_ASSET_FILENAME
    assert LOGO_PATH.is_file()
    payload = LOGO_PATH.read_bytes()
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WEBP"

    app = FastAPI()
    app.include_router(brand_router)
    response = TestClient(app).get("/brand/esp-logo.webp")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WEBP"


def test_hero_logo_uses_new_brand_identity_and_stable_route():
    html = hero_logo()
    assert "/brand/esp-logo.webp" in html
    assert branding.PRODUCT_FULL_NAME in html
    assert "Pulsar-Frequency" not in html


def test_legacy_public_brand_copy_and_logo_links_migrate_idempotently():
    legacy = (
        "Pulsar-Frequency House — For Professional Creation Beyond The Cosmos — "
        "Powered by Elevate Souls Productions & Aura AI Systems — "
        "/static/pulsar-frequency-house-logo.svg"
    )
    current = rebrand_text(legacy)
    assert branding.PRODUCT_FULL_NAME in current
    assert branding.TAGLINE in current
    assert branding.ENDORSEMENT in current
    assert branding.BRAND_ASSET_PATH in current
    assert "Pulsar-Frequency House" not in current
    assert "Aura AI Systems" not in current
    assert "pulsar-frequency-house-logo.svg" not in current
    assert rebrand_text(current) == current


def test_previous_brand_generations_all_resolve_to_current_public_identity():
    source = " | ".join(
        (
            "Cosmic Creative Studios",
            "Cosmic Creation Studios",
            "4Infinity Creative Studios",
            "The Live Sound Studio",
            "Pulsar-Frequency House",
        )
    )
    current = rebrand_text(source)
    assert current.count(branding.PRODUCT_FULL_NAME) == 5
    for legacy_name in (
        "Cosmic Creative Studios",
        "Cosmic Creation Studios",
        "4Infinity Creative Studios",
        "The Live Sound Studio",
        "Pulsar-Frequency House",
    ):
        assert legacy_name not in current
