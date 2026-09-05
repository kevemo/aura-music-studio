import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_prompter import live_auto_cue_prompter, router


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member():
    return SimpleNamespace(user_id="creator-1", display_name="Creator")


def test_prompter_router_contains_only_creator_control_route():
    paths = [route.path for route in router.routes]
    assert "/live-overlay-studio/prompter" in paths
    assert not any("source" in path for path in paths)


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


def test_production_api_explicitly_mounts_prompter_router():
    # Match the established Overlay Studio production-mount regression pattern. The shared
    # FastAPI app route list is intentionally mutated by other integration tests, so validate
    # the prompter router itself plus the explicit production aggregate wiring in source.
    from aura_music_studio import api as api_mod

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/live-overlay-studio/prompter" in paths
    api_source = inspect.getsource(api_mod)
    assert "from .aura_live_prompter import router as aura_live_prompter_router" in api_source
    assert "app.include_router(aura_live_prompter_router)" in api_source
