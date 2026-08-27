import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from aura_music_studio.aura_context_extensions import _inject_messages
from aura_music_studio.brand_migration import BrandMigrationMiddleware, rebrand_text
from aura_music_studio import branding


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy_page():
        return "<h1>Pulsar-Frequency House</h1><img src='/brand/pulsar-frequency-house-logo.svg'>"

    app.add_middleware(BrandMigrationMiddleware)
    return app


def test_supplied_artwork_is_the_canonical_brand_visual():
    assert branding.BRAND_LOGO_PATH == "/brand/elevate-souls-productions-content-creation-command-center.webp"
    assert branding.BRAND_ICON_PATH == "/static/elevate-souls-command-center-logo.svg"
    asset = Path("aura_music_studio/static") / branding.BRAND_ARTWORK_FILENAME
    assert asset.exists()
    payload = asset.read_bytes()
    assert len(payload) > 20_000
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WEBP"


def test_homepage_legacy_brand_logo_url_rewrites_to_supplied_artwork():
    branded = rebrand_text("<img src='/brand/pulsar-frequency-house-logo.svg'>")
    assert branding.BRAND_LOGO_PATH in branded
    assert "/brand/pulsar-frequency-house-logo.svg" not in branded


def test_legacy_and_canonical_logo_routes_serve_the_same_supplied_artwork():
    client = TestClient(_app())
    canonical = client.get(branding.BRAND_LOGO_PATH)
    legacy_brand = client.get("/brand/pulsar-frequency-house-logo.svg")
    legacy_static = client.get("/static/pulsar-frequency-house-logo.svg")

    assert canonical.status_code == 200
    assert canonical.headers["content-type"].startswith("image/webp")
    assert legacy_brand.status_code == 200
    assert legacy_static.status_code == 200
    assert legacy_brand.content == canonical.content
    assert legacy_static.content == canonical.content


def test_visible_esp_shorthand_is_expanded_without_touching_internal_identifiers():
    rendered = rebrand_text("ESP Creator Network Hub · ESP Agent · ESP Social · ESP Hub")
    assert rendered == (
        "Elevate Souls Productions Creator Network Hub · "
        "Elevate Souls Productions Agent · Elevate Souls Productions Social · Elevate Souls Productions Hub"
    )
    internal = "aura_music_studio esp_shop_provider_runtime /command-center/api/esp/internal"
    assert rebrand_text(internal) == internal


def test_aura_receives_current_command_center_identity_before_inference():
    original = [
        {
            "role": "system",
            "content": (
                "You are Aura, the general AI co-creator and operating intelligence inside Pulsar-Frequency House, "
                "powered by Elevate Souls Productions & Aura AI Systems."
            ),
        },
        {"role": "user", "content": "Hello Aura"},
    ]
    rewritten = _inject_messages(original, "member-1", "thread-1")
    system = rewritten[0]["content"]
    assert branding.PRODUCT_FULL_NAME in system
    assert branding.ENDORSEMENT in system
    assert "Pulsar-Frequency House" not in system
    assert "Aura AI Systems" not in system
    assert "Pulsar-Frequency House" in original[0]["content"]


def test_branding_followup_branch_cannot_consume_vercel_deployment_allowance():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    assert config["git"]["deploymentEnabled"]["feature/command-center-master-artwork"] is False
