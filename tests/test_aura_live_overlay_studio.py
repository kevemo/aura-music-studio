from __future__ import annotations

import inspect


def test_live_overlay_routes_are_mounted_in_production_aggregate():
    from aura_music_studio.api import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/live-overlay-studio" in paths
    assert "/api/live-overlay/profile" in paths
    assert "/api/live-overlay/rotate-source" in paths
    assert "/api/live-overlay/test-event" in paths
    assert "/api/live-overlay/capabilities" in paths
    assert "/live-overlay/source/{token}" in paths
    assert "/live-overlay/source/{token}/config" in paths
    assert "/live-overlay/source/{token}/events" in paths
    assert "/live-overlay/source/{token}/speech" in paths


def test_gift_sound_mute_is_independent_from_visual_gift_alerts_and_tts():
    from aura_music_studio import aura_live_overlay_studio as mod

    source = inspect.getsource(mod)
    assert "gift_sound_muted" in source
    assert "Mute TikTok gift alert sounds" in source
    assert "Keeps gift visuals active" in source
    assert "Gift TTS remains a separate control" in source
    assert "if(cfg.all_audio_muted||cfg.gift_sound_muted)return" in source
    assert "giftFeed(p);beep();show" in source
    assert "cfg.tts_gifts_enabled" in source


def test_browser_source_is_private_token_based_and_rotatable():
    from aura_music_studio import aura_live_overlay_studio as mod

    source = inspect.getsource(mod)
    assert "secrets.token_urlsafe" in source
    assert "hashlib.sha256" in source
    assert "source_token_hash" in source
    assert "Rotate" not in source or "rotate_overlay_source" in source
    assert '"Cache-Control": "no-store"' in source
    assert '"Referrer-Policy": "no-referrer"' in source


def test_public_browser_source_boundary_is_explicit_in_access_control():
    from aura_music_studio import access_control

    assert "/live-overlay/source/" in access_control.PUBLIC_PREFIXES
    source = inspect.getsource(access_control)
    assert "high-entropy, rotatable source token" in source


def test_tier_matrix_progressively_unlocks_live_features():
    from aura_music_studio.aura_live_overlay_studio import TIER_MATRIX

    assert TIER_MATRIX["free"]["max_rules"] == 5
    assert not TIER_MATRIX["free"]["custom_sounds"]
    assert TIER_MATRIX["base"]["custom_sounds"]
    assert TIER_MATRIX["base"]["aura_voice"]
    assert not TIER_MATRIX["base"]["voice_clone"]
    assert TIER_MATRIX["pro"]["max_rules"] is None
    assert TIER_MATRIX["pro"]["voice_clone"]


def test_widget_catalog_covers_tikfinity_class_and_competitor_patterns():
    from aura_music_studio.aura_live_overlay_studio import WIDGET_CATALOG

    keys = {row[0] for row in WIDGET_CATALOG}
    expected = {
        "alert_box",
        "welcome",
        "gift_feed",
        "chat_box",
        "event_list",
        "like_goal",
        "follower_goal",
        "gift_goal",
        "subscriber_goal",
        "share_goal",
        "top_gifters",
        "top_likers",
        "gift_combo",
        "countdown",
        "spin_wheel",
        "battle",
        "poll",
        "pinned_message",
        "shopping",
        "camera_frame",
        "captions",
    }
    assert expected <= keys


def test_voice_clone_is_consent_bounded_and_provider_fails_closed():
    from aura_music_studio import aura_live_overlay_studio as mod

    source = inspect.getsource(mod)
    assert "APPROVED_VOICE_DUPLICATION" in source
    assert "Select an approved consent-backed voice profile" in source
    assert "AURA_LIVE_CLONE_TTS_URL" in source
    assert "AURA_LIVE_CLONE_TTS_SECRET" in source
    assert "Consent-approved cloned LIVE voice provider is not configured" in source


def test_capability_contract_does_not_claim_native_tiktok_audio_control_or_live_provider_connection():
    from aura_music_studio import aura_live_overlay_studio as mod

    source = inspect.getsource(mod.overlay_capabilities)
    assert '"provider_connection_claimed": False' in source
    assert '"native_tiktok_live_studio_audio_control_claimed": False' in source
    assert "does not claim undocumented control" in source


def test_overlay_event_payload_is_allowlisted_not_arbitrary():
    from aura_music_studio import aura_live_overlay_studio as mod

    source = inspect.getsource(mod._redact_payload)
    assert "allowed =" in source
    assert "if key not in allowed" in source
    assert "value[:500]" in source
