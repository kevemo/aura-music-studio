from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient
import pytest

from aura_music_studio.aura_avatar_runtime import (
    AVATAR_STATES,
    BROWSER_3D_RENDERER_IMPLEMENTED,
    AuraAvatarRuntimeMiddleware,
    _model_path,
    _renderer_module_url,
    avatar_status,
    router,
)


def test_avatar_status_is_truthful_without_rig(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.delenv("AURA_AVATAR_MODEL_PATH", raising=False)
    monkeypatch.delenv("AURA_AVATAR_RENDERER_MODULE_URL", raising=False)
    monkeypatch.delenv("AURA_AVATAR_3D_RENDERER_READY", raising=False)
    status = avatar_status()
    assert status["software_runtime_connected"] is True
    assert status["state_bus_connected"] is True
    assert status["model_installed"] is False
    assert status["renderer_implemented"] is True
    assert status["renderer_configured"] is True
    assert status["operator_validated"] is False
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "renderer_ready_model_asset_missing"
    assert "speaking" in AVATAR_STATES
    assert "tool_running" in AVATAR_STATES


def test_model_and_renderer_still_require_operator_validation(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    (root / "aura.glb").write_bytes(b"glTF-test-placeholder")
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.delenv("AURA_AVATAR_MODEL_PATH", raising=False)
    monkeypatch.delenv("AURA_AVATAR_RENDERER_MODULE_URL", raising=False)
    monkeypatch.setenv("AURA_AVATAR_3D_RENDERER_READY", "false")
    status = avatar_status()
    assert status["model_installed"] is True
    assert status["renderer_implemented"] is BROWSER_3D_RENDERER_IMPLEMENTED is True
    assert status["renderer_configured"] is True
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "model_and_renderer_configured_validation_pending"

    monkeypatch.setenv("AURA_AVATAR_3D_RENDERER_READY", "true")
    ready = avatar_status()
    assert ready["operator_validated"] is True
    assert ready["production_3d_ready"] is True
    assert ready["truthful_state"] == "production_3d_avatar_ready"


def test_renderer_module_rejects_http_and_allows_same_origin(monkeypatch):
    monkeypatch.setenv("AURA_AVATAR_RENDERER_MODULE_URL", "http://example.com/model-viewer.js")
    with pytest.raises(ValueError, match="same-origin path or public HTTPS"):
        _renderer_module_url()
    monkeypatch.setenv("AURA_AVATAR_RENDERER_MODULE_URL", "/static/vendor/model-viewer.min.js")
    assert _renderer_module_url() == "/static/vendor/model-viewer.min.js"


def test_avatar_model_cannot_escape_configured_asset_root(tmp_path, monkeypatch):
    root = tmp_path / "safe"
    root.mkdir()
    outside = tmp_path / "outside.glb"
    outside.write_bytes(b"outside")
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.setenv("AURA_AVATAR_MODEL_PATH", str(outside))
    with pytest.raises(ValueError, match="must resolve under"):
        _model_path()


def _app():
    app = FastAPI()
    app.include_router(router)

    @app.get("/aura-intelligence", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse("<html><body>Aura</body></html>")

    @app.get("/other", response_class=HTMLResponse)
    def other_page():
        return HTMLResponse("<html><body>Other</body></html>")

    @app.get("/api-test")
    def api_test():
        return JSONResponse({"ok": True})

    app.add_middleware(AuraAvatarRuntimeMiddleware)
    return app


def test_avatar_script_injects_only_into_aura_html():
    client = TestClient(_app())
    aura = client.get("/aura-intelligence")
    assert "/aura-intelligence/avatar-runtime.js" in aura.text
    assert "/aura-intelligence/avatar-runtime.js" not in client.get("/other").text
    assert "avatar-runtime.js" not in client.get("/api-test").text


def test_avatar_script_exposes_real_glb_renderer_and_state_bus():
    client = TestClient(_app())
    script = client.get("/aura-intelligence/avatar-runtime.js")
    assert script.status_code == 200
    assert "window.AuraHost" in script.text
    assert "aura:state" in script.text
    assert "customElements.get('model-viewer')" in script.text
    assert "availableAnimations" in script.text
    assert "animationName" in script.text
    assert "3D renderer ready · Aura rig asset pending" in script.text
    assert "deployment validation pending" in script.text
