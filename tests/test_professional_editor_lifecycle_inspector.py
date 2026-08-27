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


def test_lifecycle_router_exposes_secondary_script_once():
    paths = [route.path for route in router.routes]
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


def test_inspector_router_includes_lifecycle_script_without_duplicate_workspace():
    paths = [route.path for route in overlay.router.routes]
    assert paths.count("/creative/editor-workspace") == 1
    assert paths.count("/creative/editor-pro-controls.js") == 1
    assert paths.count("/creative/editor-pro-lifecycle-controls.js") == 1
