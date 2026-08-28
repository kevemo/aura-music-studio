from __future__ import annotations

from fastapi.responses import HTMLResponse

import aura_music_studio.professional_editor_inspector_overlay as overlay
from aura_music_studio.professional_editor_track_group_ui import (
    TRACK_GROUP_UI_JS,
    TRACK_GROUP_UI_MARKER,
    editor_track_group_controls_js,
    router as track_group_router,
)


def test_track_group_ui_targets_reversible_track_patch_contract():
    script = TRACK_GROUP_UI_JS

    assert TRACK_GROUP_UI_MARKER in script
    assert "document.body.dataset.pro==='true'" in script
    assert "Professional track grouping controls require Pro." in script
    assert "endpoint(`/tracks/${encodeURIComponent(track.id)}`)" in script
    assert "method:'PATCH'" in script
    assert "changes:{opacity,blend_mode}" in script
    assert "Undo can restore it." in script
    assert "source" not in "changes:{opacity,blend_mode}"

    for mode in (
        "normal",
        "multiply",
        "screen",
        "overlay",
        "soft_light",
        "hard_light",
        "darken",
        "lighten",
        "difference",
    ):
        assert f'value="{mode}"' in script

    assert 'id="proTrackOpacity"' in script
    assert 'min="0" max="1" step="0.01"' in script


def test_track_group_script_route_is_no_store_javascript():
    response = editor_track_group_controls_js()
    body = response.body.decode("utf-8")

    assert body.count(TRACK_GROUP_UI_MARKER) == 1
    assert response.media_type == "application/javascript"
    assert response.headers["cache-control"] == "no-store"


def test_workspace_injects_track_group_module_exactly_once(monkeypatch):
    monkeypatch.setattr(
        overlay,
        "base_workspace",
        lambda request, project="": HTMLResponse("<html><body><main>editor</main></body></html>"),
    )

    response = overlay.enhanced_professional_editor_workspace(object(), project="Demo")
    body = response.body.decode("utf-8")

    marker = "<script src='/creative/editor-track-group-controls.js'></script>"
    assert body.count(marker) == 1
    assert body.count("<script src='/creative/editor-pro-controls.js'></script>") == 1
    assert body.count("<script src='/creative/editor-pro-lifecycle-controls.js'></script>") == 1
    assert response.headers["cache-control"] == "private, no-store"


def test_track_group_child_router_owns_script_while_parent_workspace_stays_unique():
    # FastAPI represents included child routers as wrapper objects in the parent route list.
    # Assert the concrete script on its child router, matching the established lifecycle pattern.
    child_paths = [getattr(route, "path", None) for route in track_group_router.routes]
    parent_paths = [getattr(route, "path", None) for route in overlay.router.routes]
    assert child_paths.count("/creative/editor-track-group-controls.js") == 1
    assert parent_paths.count("/creative/editor-workspace") == 1
    assert parent_paths.count("/creative/editor-pro-controls.js") == 1
