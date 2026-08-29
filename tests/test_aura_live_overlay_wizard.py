from __future__ import annotations

import inspect


def test_setup_wizard_is_reachable_through_mounted_connector_router():
    from aura_music_studio.aura_live_overlay_connector import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/live-overlay-studio/setup" in paths
    assert "/live-overlays" in paths
    assert "/api/live-overlays/setup-checklist" in paths


def test_wizard_keeps_audio_truth_and_separate_mute_controls():
    from aura_music_studio import aura_live_overlay_wizard as mod

    source = inspect.getsource(mod.setup_wizard)
    assert "Mute TikTok gift alert sounds" in source
    assert "Toggle gift TTS" in source
    assert "Mute all overlay audio" in source
    checklist = inspect.getsource(mod.setup_checklist)
    assert '"native_tiktok_audio_control": False' in checklist
    assert "not undocumented native TikTok LIVE Studio application audio" in checklist


def test_wizard_is_no_code_and_uses_single_advanced_source():
    from aura_music_studio import aura_live_overlay_wizard as mod

    source = inspect.getsource(mod.setup_wizard)
    assert "No scripts. No manual coding." in source
    assert "/api/live-overlays/advanced-source/rotate" in source
    assert "/live-overlay-studio/editor" in source
    assert "/live-overlay-studio/automations" in source
    assert "/api/live-overlays/simulate" in source


def test_wizard_does_not_fake_direct_tiktok_connection():
    from aura_music_studio import aura_live_overlay_wizard as mod

    source = inspect.getsource(mod.setup_wizard)
    assert "It does not pretend TikTok is directly connected" in source
    assert "/api/live-overlays/connector/rotate" in source
