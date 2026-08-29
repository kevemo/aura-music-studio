from __future__ import annotations

import inspect

from aura_music_studio.game_forge_capture_card import capture_card_capabilities, router


def test_capture_card_capability_contract_is_truthful():
    source = inspect.getsource(capture_card_capabilities)
    assert '"browser_local_capture": True' in source
    assert '"requires_uvc_or_browser_visible_capture_device": True' in source
    assert '"hdmi_direct_input_without_capture_hardware": False' in source
    assert '"server_receives_capture_stream": False' in source
    assert '"recording_enabled_by_default": False' in source
    assert '"controller_input_routed_to_console": False' in source


def test_capture_portal_uses_browser_media_devices_without_upload_endpoint():
    from aura_music_studio import game_forge_capture_card as mod

    source = inspect.getsource(mod)
    assert "navigator.mediaDevices.getUserMedia" in source
    assert "navigator.mediaDevices.enumerateDevices" in source
    assert "requestPictureInPicture" in source
    assert "window.open" in source
    assert "server receives" not in source.lower()
    assert "UploadFile" not in source
    assert "File(" not in source


def test_capture_portal_requires_game_playtest_entitlement():
    from aura_music_studio import game_forge_capture_card as mod

    source = inspect.getsource(mod.capture_card_portal)
    assert "GAME_PLAYTEST" in source
    assert "member.plan.has" in source


def test_game_forge_composition_mounts_capture_card_routes():
    routes = {(getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set())) for route in router.routes}
    assert any(path == "/game-creation/capture-card/{game_id}" and "GET" in methods for path, methods in routes)
    assert any(path == "/api/game-forge/capture-card/capabilities" and "GET" in methods for path, methods in routes)

    from aura_music_studio import game_forge_export_portal as export_portal
    source = inspect.getsource(export_portal)
    assert "capture_card_router" in source
    assert "router.include_router(capture_card_router)" in source

    from aura_music_studio import game_forge_world_api as world_api
    world_source = inspect.getsource(world_api)
    assert "game_export_portal_router" in world_source
    assert "router.include_router(game_export_portal_router)" in world_source


def test_export_studio_links_to_capture_card():
    from aura_music_studio.game_forge_export_portal import game_export_portal

    source = inspect.getsource(game_export_portal)
    assert "Capture Card" in source
    assert "/game-creation/capture-card/" in source
