from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.aura_context_extensions import _inject_messages
from aura_music_studio.brand_migration import BrandMigrationMiddleware, rebrand_text
from aura_music_studio.branding import BRAND_ART_ROUTE, BRAND_LOGO_PATH, ENDORSEMENT, PRODUCT_FULL_NAME


def test_aura_receives_current_identity_before_inference_without_profile_extensions():
    original = [
        {
            "role": "system",
            "content": (
                "You are Aura, the general AI co-creator and operating intelligence inside Pulsar-Frequency House, "
                "powered by Elevate Souls Productions & Aura AI Systems."
            ),
        },
        {"role": "user", "content": "Help me with this project."},
    ]

    rewritten = _inject_messages(original, "member-1", "thread-1")

    assert rewritten is not original
    assert PRODUCT_FULL_NAME in rewritten[0]["content"]
    assert ENDORSEMENT in rewritten[0]["content"]
    assert "Pulsar-Frequency House" not in rewritten[0]["content"]
    assert "Aura AI Systems" not in rewritten[0]["content"]
    assert "Pulsar-Frequency House" in original[0]["content"]


def test_private_tool_router_prompt_is_not_rewritten():
    messages = [{"role": "system", "content": "You are Aura's private tool router."}]
    assert _inject_messages(messages, "member-1", "thread-1") is messages


def test_retired_logo_urls_rewrite_to_current_mark_route():
    branded = rebrand_text(
        "<img src='/brand/pulsar-frequency-house-logo.svg'>"
        "<img src='/static/pulsar-frequency-house-logo.svg'>"
    )
    assert branded.count(BRAND_LOGO_PATH) == 2
    assert "pulsar-frequency-house-logo.svg" not in branded


def test_direct_retired_logo_requests_are_intercepted_before_static_routing():
    app = FastAPI()
    app.add_middleware(BrandMigrationMiddleware)
    client = TestClient(app)

    for path in ("/brand/pulsar-frequency-house-logo.svg", "/static/pulsar-frequency-house-logo.svg"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == BRAND_ART_ROUTE
