from __future__ import annotations

from fastapi.responses import HTMLResponse

import aura_music_studio.professional_editor_inspector_overlay as overlay
from aura_music_studio.creative_version_autopromotion import router as creative_overlay_router


def test_workspace_overlay_injects_controls_once_and_preserves_safe_headers(monkeypatch):
    def fake_workspace(_request, project=""):
        assert project == "Demo"
        return HTMLResponse(
            "<!doctype html><html><body><main>Base editor</main></body></html>",
            headers={"X-Editor-Test": "preserved"},
        )

    monkeypatch.setattr(overlay, "base_workspace", fake_workspace)
    response = overlay.enhanced_professional_editor_workspace(object(), project="Demo")
    body = response.body.decode("utf-8")

    assert body.count("/creative/editor-pro-controls.js") == 1
    assert "Base editor" in body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-editor-test"] == "preserved"
    assert int(response.headers["content-length"]) == len(response.body)


def test_pro_controls_script_targets_real_editor_contract_and_truth_boundary():
    script = overlay.PRO_EDITOR_CONTROLS_JS

    assert "/items/${encodeURIComponent(item.id)}/masks" in script
    assert "/item/${encodeURIComponent(item.id)}/effects" in script
    assert "/items/${encodeURIComponent(item.id)}/keyframes" in script
    assert "/sequences/${encodeURIComponent(sequence.id)}/render" in script
    assert "frame_time" in script
    assert "source media remains unchanged" in script
    assert "Unsupported renderer state remains fail-closed" in script
    assert "gaussian_blur" in script
    assert "pixelate" in script
    assert "transform.rotation" in script
    assert "color.gamma" in script


def test_overlay_router_exposes_script_and_workspace_only_once():
    paths = [route.path for route in overlay.router.routes]
    assert paths.count("/creative/editor-pro-controls.js") == 1
    assert paths.count("/creative/editor-workspace") == 1


def test_creative_overlay_mounts_only_one_professional_workspace_route():
    paths = [route.path for route in creative_overlay_router.routes]
    assert paths.count("/creative/editor-workspace") == 1
    assert paths.count("/creative/editor-pro-controls.js") == 1
