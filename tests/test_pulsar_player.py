from __future__ import annotations

from pathlib import Path

from aura_music_studio.creative_media_preview import MEDIA_PREVIEW_SCRIPT
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


def test_player_middleware_is_available_for_signed_in_member_html():
    assert PulsarPlayerMiddleware.__doc__
    assert "signed-in member html" in PulsarPlayerMiddleware.__doc__.lower()


def test_creative_media_gallery_can_send_audio_and_video_to_pulsar_player():
    assert "data-creative-play" in MEDIA_PREVIEW_SCRIPT
    assert "window.PulsarPlayer.play" in MEDIA_PREVIEW_SCRIPT
    assert "▶ Pulsar Player" in MEDIA_PREVIEW_SCRIPT
    assert "Download eligibility is enforced server-side by membership tier" in MEDIA_PREVIEW_SCRIPT


def test_app_mounts_entitlement_handlers_before_base_creative_handlers():
    source = Path("app.py").read_text(encoding="utf-8")
    gate = source.index("app.include_router(commercial_entitlement_router)")
    project = source.index("app.include_router(creative_project_router)")
    media = source.index("app.include_router(creative_media_preview_router)")
    assert gate < project
    assert gate < media
    assert "app.include_router(creative_library_router)" in source
    assert "app.include_router(pulsar_player_router)" in source
    assert "app.add_middleware(PulsarPlayerMiddleware)" in source
