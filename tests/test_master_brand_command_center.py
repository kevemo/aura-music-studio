import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from aura_music_studio.brand_migration import BrandMigrationMiddleware, rebrand_text
from aura_music_studio.branding import (
    AI_SYSTEM_NAME,
    BRAND_ASSET_URL,
    ENDORSEMENT,
    PRODUCT_FULL_NAME,
    PRODUCT_NAME,
    PRODUCT_SHORT_NAME,
    TAGLINE,
)


EXPECTED_NAME = "Elevate Souls Productions Content Creation Command Center"


def test_authoritative_public_brand_constants():
    assert PRODUCT_NAME == EXPECTED_NAME
    assert PRODUCT_FULL_NAME == EXPECTED_NAME
    assert PRODUCT_SHORT_NAME == "Content Creation Command Center"
    assert TAGLINE == "Elevate Your Soul Through Purposeful Media"
    assert ENDORSEMENT == "Powered by Aura AI"
    assert AI_SYSTEM_NAME == "Aura AI"
    assert BRAND_ASSET_URL == "/brand/elevate-souls-productions-content-creation-command-center.webp"


def test_legacy_public_copy_rebrands_without_changing_internal_route_slugs():
    legacy = """
    <title>Pulsar-Frequency House — For Professional Creation Beyond The Cosmos</title>
    <img src='/brand/pulsar-frequency-house-logo.svg'>
    <p>Powered by Elevate Souls Productions & Aura AI Systems</p>
    <a href='/command-center'>ESP Member Hub</a>
    """
    branded = rebrand_text(legacy)
    assert EXPECTED_NAME in branded
    assert "Elevate Your Soul Through Purposeful Media" in branded
    assert "Powered by Aura AI" in branded
    assert BRAND_ASSET_URL in branded
    assert "Elevate Souls Productions Member Hub" in branded
    assert "Pulsar-Frequency House" not in branded
    assert "Aura AI Systems" not in branded
    assert "href='/command-center'" in branded


def _brand_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy_page():
        return "<h1>Pulsar-Frequency House</h1><img src='/brand/pulsar-frequency-house-logo.svg'>"

    app.add_middleware(BrandMigrationMiddleware)
    return app


def test_brand_middleware_rewrites_html_and_serves_new_artwork():
    client = TestClient(_brand_test_app())

    page = client.get("/legacy")
    assert page.status_code == 200
    assert EXPECTED_NAME in page.text
    assert BRAND_ASSET_URL in page.text
    assert "Pulsar-Frequency House" not in page.text

    canonical = client.get(BRAND_ASSET_URL)
    assert canonical.status_code == 200
    assert canonical.headers["content-type"].startswith("image/webp")
    assert canonical.content[:4] == b"RIFF"
    assert canonical.content[8:12] == b"WEBP"

    legacy = client.get("/brand/pulsar-frequency-house-logo.svg")
    assert legacy.status_code == 200
    assert legacy.headers["content-type"].startswith("image/webp")
    assert legacy.content == canonical.content


def test_brand_artwork_is_packaged_and_branch_is_not_deployed_to_vercel():
    asset = Path("aura_music_studio/static/elevate-souls-productions-content-creation-command-center.webp")
    assert asset.exists()
    assert asset.stat().st_size > 20_000

    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    enabled = config["git"]["deploymentEnabled"]
    assert enabled["feature/esp-content-command-center-branding"] is False


def test_repository_package_metadata_uses_new_public_name():
    metadata = Path("pyproject.toml").read_text(encoding="utf-8")
    assert EXPECTED_NAME in metadata
    assert "product brand is Pulsar-Frequency House" not in metadata
