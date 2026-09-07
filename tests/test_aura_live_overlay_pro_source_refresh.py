from __future__ import annotations

import inspect


def test_advanced_source_is_mounted_under_existing_token_boundary():
    from aura_music_studio import access_control, api
    from aura_music_studio.aura_live_overlay_pro_source import router

    expected = {
        "/live-overlay-studio/advanced-source",
        "/api/live-overlays/advanced-source/rotate",
        "/live-overlay/source/advanced/{token}",
        "/live-overlay/source/advanced/{token}/state",
        "/live-overlay/source/advanced/{token}/events",
        "/live-overlay/source/advanced/{token}/media/{media_id}",
    }
    assert expected <= {getattr(route, "path", "") for route in router.routes}
    assert "/live-overlay/source/" in access_control.PUBLIC_PREFIXES

    production_source = inspect.getsource(api)
    assert "from .aura_live_overlay_pro_source import router as aura_live_overlay_pro_source_router" in production_source
    assert "app.include_router(aura_live_overlay_pro_source_router)" in production_source


def test_advanced_source_rotation_uses_hashed_source_identity():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    assert "hashlib.sha256" in inspect.getsource(mod._hash)
    source = inspect.getsource(mod.rotate_advanced_source)
    assert "secrets.token_urlsafe" in source
    assert "source_token_hash" in source
    assert "UPDATE live_overlay_profiles" in source


def test_advanced_source_media_is_token_and_tenant_confined():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod.source_media)
    assert "_user_for_token(token)" in source
    assert "id=? AND user_id=?" in source
    assert "root not in target.parents" in source
    assert "X-Content-Type-Options" in source
    assert "Referrer-Policy" in source


def test_advanced_renderer_is_bounded_to_known_reactions_and_actions():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod.advanced_source)
    for needle in (
        "giftCannon",
        "likeFountain",
        "boundedAutomation",
        "show_widget",
        "hide_widget",
        "set_text",
        "switch_scene",
        "spin_wheel",
        "LIVE Match",
        "Content-Security-Policy",
    ):
        assert needle in source

    assert "eval(" not in source
    assert "new Function" not in source
    assert "WebSocket(" not in source


def test_advanced_source_does_not_duplicate_provider_relay_ingress():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod)
    assert "connector_ingest" not in source
    assert "process_overlay_event" not in source
