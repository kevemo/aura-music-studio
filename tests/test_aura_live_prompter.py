from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.api import app
from aura_music_studio.aura_live_prompter import live_auto_cue_prompter, router


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member():
    return SimpleNamespace(user_id="creator-1", display_name="Creator")


def test_prompter_route_is_mounted_and_not_public_overlay_source():
    local_paths = [route.path for route in router.routes]
    app_paths = [getattr(route, "path", "") for route in app.routes]
    assert "/live-overlay-studio/prompter" in local_paths
    assert "/live-overlay-studio/prompter" in app_paths
    assert not any("source" in path for path in local_paths)


def test_prompter_requires_authenticated_member():
    with pytest.raises(HTTPException) as exc:
        live_auto_cue_prompter(RequestStub())
    assert exc.value.status_code == 401


def test_script_is_browser_local_and_not_submitted_or_persisted():
    response = live_auto_cue_prompter(RequestStub(_member()))
    body = response.body.decode("utf-8")
    assert "script text is not submitted to the server" in body
    assert "live_overlay_events" not in body
    assert "fetch(" not in body
    assert "XMLHttpRequest" not in body
    assert "WebSocket" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_prompter_has_delivery_controls_and_keyboard_shortcuts():
    response = live_auto_cue_prompter(RequestStub(_member()))
    body = response.body.decode("utf-8")
    for marker in [
        "Auto-scroll speed",
        "Text size",
        "Line spacing",
        "Mirror text",
        "3-second countdown",
        "ArrowUp",
        "ArrowDown",
        "requestFullscreen",
        "window.open",
    ]:
        assert marker in body


def test_prompter_response_is_no_store_and_no_referrer():
    response = live_auto_cue_prompter(RequestStub(_member()))
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
