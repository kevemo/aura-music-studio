from __future__ import annotations

import inspect


def test_advanced_source_router_is_mounted_and_token_routes_are_public_only_at_middleware_boundary():
    from aura_music_studio import access_control, api as api_mod
    from aura_music_studio.aura_live_overlay_pro_source import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/live-overlay-studio/advanced-source" in paths
    assert "/api/live-overlays/advanced-source/rotate" in paths
    assert "/live-overlay/advanced/{token}" in paths
    assert "/live-overlay/advanced/{token}/state" in paths
    assert "/live-overlay/advanced/{token}/events" in paths
    assert "/live-overlay/advanced/{token}/media/{media_id}" in paths
    assert "/live-overlay/advanced/" in access_control.PUBLIC_PREFIXES
    source = inspect.getsource(api_mod)
    assert "app.include_router(aura_live_overlay_pro_source_router)" in source


def test_advanced_source_token_is_hashed_and_rotation_invalidates_old_source():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    assert "hashlib.sha256" in inspect.getsource(mod._hash)
    source = inspect.getsource(mod.rotate_advanced_source)
    assert "secrets.token_urlsafe" in source
    assert "source_token_hash" in source
    assert "UPDATE live_overlay_profiles" in source


def test_single_source_renders_competitor_class_effects_and_safe_automation_actions():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod.advanced_source)
    for needle in (
        "giftCannon",
        "likeFountain",
        "Top Supporters",
        "automation(p)",
        "show_widget",
        "hide_widget",
        "play_media",
        "play_sound",
        "speak",
        "switch_scene",
        "spin_wheel",
        "LIVE Match",
    ):
        assert needle in source
    assert "eval(" not in source
    assert "new Function" not in source
    assert "WebSocket(" not in source


def test_advanced_source_respects_gift_and_global_audio_mute():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod.advanced_source)
    assert "c.all_audio_muted||c.gift_sound_muted" in source
    assert "if(c.all_audio_muted||!text)return" in source
    assert "cfg.gift_sound_muted" not in source  # advanced source reads state.profile explicitly


def test_advanced_source_media_is_tenant_and_token_confined():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod.source_media)
    assert "id=? AND user_id=?" in source
    assert "root not in target.parents" in source
    assert '"X-Content-Type-Options": "nosniff"' in source


def test_advanced_source_has_no_provider_connection_claim():
    from aura_music_studio import aura_live_overlay_pro_source as mod

    source = inspect.getsource(mod)
    assert "TikTok" in source
    assert "provider_connected" not in inspect.getsource(mod.advanced_source)
