from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from aura_music_studio import shared_sky_streaming_studios as studios
from aura_music_studio.shared_skies_branding import PRODUCT_NAME, install_shared_skies_branding


def test_locked_shared_skies_branding_normalizes_legacy_presentation_only():
    app = FastAPI()

    @app.get("/legacy", tags=["Shared Sky Streaming Studios", "Shared Sky Owner Operations"])
    def legacy_route():
        return {"ok": True}

    install_shared_skies_branding(app)

    assert PRODUCT_NAME == "Shared Skies Streaming Studios"
    assert studios.PRODUCT_NAME == PRODUCT_NAME
    route = next(route for route in app.router.routes if getattr(route, "path", "") == "/legacy")
    assert route.tags == ["Shared Skies Streaming Studios", "Shared Skies Owner Operations"]
    assert route.path == "/legacy"


def test_multihost_capacity_is_explicit_and_fail_closed_in_example_configuration():
    env_text = Path(".env.example").read_text(encoding="utf-8")
    assert "SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS=0" in env_text
    assert "including the host" in env_text
    assert "does not enable WebRTC/SFU" in env_text
