from __future__ import annotations

import inspect
from types import SimpleNamespace


def test_live_overlay_routes_are_mounted_in_production_aggregate():
    from aura_music_studio import api as api_mod
    from aura_music_studio.aura_live_overlay_studio import router

    # Earlier tests may import the production app entrypoint, which mutates the shared FastAPI
    # route list while composing overlays. Validate the overlay router itself plus the explicit
    # production API mount instead of relying on test-order-sensitive global app state.
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/live-overlay-studio" in paths
    assert "/api/live-overlay/profile" in paths
    assert "/api/live-overlay/rotate-source" in paths
    assert "/api/live-overlay/test-event" in paths
    assert "/api/live-overlay/capabilities" in paths
    assert "/live-overlay/source/{token}" in paths
    assert "/live-overlay/source/{token}/config" in paths
    assert "/live-overlay/source/{token}/events" in paths
    assert "/live-overlay/source/{token}/speech" in paths
    api_source = inspect.getsource(api_mod)
    assert "from .aura_live_overlay_studio import router as aura_live_overlay_studio_router" in api_source
    assert "app.include_router(aura_live_overlay_studio_router)" in api_source


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


def test_live_source_aura_speech_rechecks_current_plan_after_downgrade(monkeypatch):
    from aura_music_studio import aura_live_overlay_studio as overlay
    from aura_music_studio.access_control import MembershipAccessMiddleware

    monkeypatch.setattr(overlay, "_user_for_source", lambda token: "user-1")
    monkeypatch.setattr(overlay, "_profile", lambda user_id: {"voice_mode": "aura"})
    guard = MembershipAccessMiddleware.__new__(MembershipAccessMiddleware)
    guard.store = SimpleNamespace(get_user=lambda user_id: {"id": user_id, "status": "active", "plan_id": "pro"})
    guard.memberships = SimpleNamespace(
        subscriptions=SimpleNamespace(
            enforce=lambda user: {**user, "status": "active", "plan_id": "free", "billing_status": "not_required"}
        )
    )

    response = guard._live_overlay_speech_denial("/live-overlay/source/private-token/speech", "POST")
    assert response is not None
    assert response.status_code == 403
    assert b"current Basic or Pro" in response.body


def test_live_source_clone_speech_rechecks_current_pro_entitlement(monkeypatch):
    from aura_music_studio import aura_live_overlay_studio as overlay
    from aura_music_studio.access_control import MembershipAccessMiddleware

    monkeypatch.setattr(overlay, "_user_for_source", lambda token: "user-2")
    monkeypatch.setattr(overlay, "_profile", lambda user_id: {"voice_mode": "clone"})
    guard = MembershipAccessMiddleware.__new__(MembershipAccessMiddleware)
    guard.store = SimpleNamespace(get_user=lambda user_id: {"id": user_id, "status": "active", "plan_id": "pro"})
    guard.memberships = SimpleNamespace(
        subscriptions=SimpleNamespace(enforce=lambda user: {**user, "status": "active", "plan_id": "base"})
    )

    response = guard._live_overlay_speech_denial("/live-overlay/source/private-token/speech", "POST")
    assert response is not None
    assert response.status_code == 403
    assert b"current Pro" in response.body


def test_live_source_browser_voice_does_not_gain_paid_entitlement(monkeypatch):
    from aura_music_studio import aura_live_overlay_studio as overlay
    from aura_music_studio.access_control import MembershipAccessMiddleware

    monkeypatch.setattr(overlay, "_user_for_source", lambda token: "user-3")
    monkeypatch.setattr(overlay, "_profile", lambda user_id: {"voice_mode": "browser"})
    guard = MembershipAccessMiddleware.__new__(MembershipAccessMiddleware)
    guard.store = SimpleNamespace(get_user=lambda user_id: {"id": user_id, "status": "active", "plan_id": "free"})
    guard.memberships = SimpleNamespace(subscriptions=SimpleNamespace(enforce=lambda user: user))

    assert guard._live_overlay_speech_denial("/live-overlay/source/private-token/speech", "POST") is None


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
