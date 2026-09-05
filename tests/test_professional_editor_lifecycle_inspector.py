from __future__ import annotations

from fastapi.responses import HTMLResponse

import aura_music_studio.professional_editor_inspector_overlay as overlay
from aura_music_studio.professional_editor_lifecycle_inspector import PRO_EDITOR_LIFECYCLE_JS, router


def test_lifecycle_script_targets_reversible_graph_routes_and_escapes_names():
    script = PRO_EDITOR_LIFECYCLE_JS

    assert "data-life-action" in script
    assert "method:'PATCH'" in script
    assert "method:'DELETE'" in script
    assert "/masks/${encodeURIComponent(editingMask.maskId)}" in script
    assert "/effects/${encodeURIComponent(editingEffect.effectId)}" in script
    assert "/effects/${encodeURIComponent(id)}/reorder" in script
    assert "/keyframes/${encodeURIComponent(id)}" in script
    assert "Undo can restore it" in script
    assert "replace(/[&<>\"']/g" in script
    assert "document.body.dataset.pro==='true'" in script


def test_lifecycle_script_exposes_reversible_professional_blend_modes():
    script = PRO_EDITOR_LIFECYCLE_JS

    assert "id=\"proBlendMode\"" in script
    assert "id=\"proApplyBlend\"" in script
    assert "changes:{blend_mode}" in script
    assert "/items/${encodeURIComponent(item.id)}" in script
    assert "Professional blend modes require Pro." in script
    assert "stored in the reversible edit graph" in script
    assert "Blend mode saved" in script
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


def test_lifecycle_router_exposes_secondary_script_once():
    paths = [getattr(route, "path", None) for route in router.routes]
    assert paths.count("/creative/editor-pro-lifecycle-controls.js") == 1


def test_workspace_injects_primary_then_lifecycle_script_once(monkeypatch):
    def fake_workspace(_request, project=""):
        assert project == "LifecycleDemo"
        return HTMLResponse("<!doctype html><html><body><main>Editor</main></body></html>")

    monkeypatch.setattr(overlay, "base_workspace", fake_workspace)
    response = overlay.enhanced_professional_editor_workspace(object(), project="LifecycleDemo")
    body = response.body.decode("utf-8")

    primary = "<script src='/creative/editor-pro-controls.js'></script>"
    lifecycle = "<script src='/creative/editor-pro-lifecycle-controls.js'></script>"
    assert body.count(primary) == 1
    assert body.count(lifecycle) == 1
    assert body.index(primary) < body.index(lifecycle)
    assert response.headers["cache-control"] == "private, no-store"


def test_inspector_router_keeps_direct_workspace_unique_while_child_owns_lifecycle_script():
    # The included lifecycle router is intentionally represented by a wrapper object in the
    # parent route list. Count concrete parent routes here and child routes in its own router.
    parent_paths = [getattr(route, "path", None) for route in overlay.router.routes]
    child_paths = [getattr(route, "path", None) for route in router.routes]
    assert parent_paths.count("/creative/editor-workspace") == 1
    assert parent_paths.count("/creative/editor-pro-controls.js") == 1
    assert child_paths.count("/creative/editor-pro-lifecycle-controls.js") == 1
