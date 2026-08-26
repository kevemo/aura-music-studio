from __future__ import annotations

from aura_music_studio.pulsar_player import PULSAR_PLAYER_SCRIPT, PulsarPlayerMiddleware, router


def test_player_script_exposes_persistent_audio_video_queue():
    assert "pulsar-frequency-house-player-v1" in PULSAR_PLAYER_SCRIPT
    assert "window.PulsarPlayer" in PULSAR_PLAYER_SCRIPT
    assert "playIndex" in PULSAR_PLAYER_SCRIPT
    assert "previous" in PULSAR_PLAYER_SCRIPT
    assert "next" in PULSAR_PLAYER_SCRIPT
    assert "openVideo" in PULSAR_PLAYER_SCRIPT
    assert "localStorage" in PULSAR_PLAYER_SCRIPT
    assert "type=\"range\"" in PULSAR_PLAYER_SCRIPT
    assert "pp-volume" in PULSAR_PLAYER_SCRIPT
    assert "pp-loop" in PULSAR_PLAYER_SCRIPT
    assert "playsinline" in PULSAR_PLAYER_SCRIPT


def test_player_uses_member_media_urls_not_filesystem_paths_or_secrets():
    lowered = PULSAR_PLAYER_SCRIPT.lower()
    assert "filesystem" not in lowered
    assert "access_token" not in lowered
    assert "refresh_token" not in lowered
    assert "authorization" not in lowered
    assert "data-pulsar-play" in PULSAR_PLAYER_SCRIPT


def test_player_ui_route_is_private_creative_support_route():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/creative/pulsar-player-ui.js" in paths
    assert "/pulsar-player-ui.js" not in paths


def test_player_middleware_is_available_for_authenticated_html_injection():
    assert PulsarPlayerMiddleware.__doc__
    assert "authenticated member" in PulsarPlayerMiddleware.__doc__.lower()
