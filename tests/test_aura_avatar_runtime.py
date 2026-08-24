from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient
import pytest

from aura_music_studio.aura_avatar_runtime import (
    AVATAR_STATES,
    BROWSER_3D_RENDERER_IMPLEMENTED,
    AuraAvatarRuntimeMiddleware,
    _model_path,
    avatar_status,
    router,
)


def test_avatar_status_is_truthful_without_rig(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.delenv("AURA_AVATAR_MODEL_PATH", raising=False)
    monkeypatch.delenv("AURA_AVATAR_3D_RENDERER_READY", raising=False)
    status = avatar_status()
    assert status["software_runtime_connected"] is True
    assert status["state_bus_connected"] is True
    assert status["model_installed"] is False
    assert status["renderer_connected"] is False
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "runtime_ready_model_asset_missing"
    assert "speaking" in AVATAR_STATES
    assert "tool_running" in AVATAR_STATES


def test_model_asset_and_env_flag_cannot_fake_missing_renderer(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    (root / "aura.glb").write_bytes(b"glTF-test-placeholder")
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.delenv("AURA_AVATAR_MODEL_PATH", raising=False)
    monkeypatch.setenv("AURA_AVATAR_3D_RENDERER_READY", "true")
    status = avatar_status()
    assert status["model_installed"] is True
    assert status["renderer_requested"] is True
    assert status["renderer_implemented"] is BROWSER_3D_RENDERER_IMPLEMENTED
    assert status["renderer_connected"] is False
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "model_asset_installed_renderer_pending"


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


def test_avatar_script_exposes_state_bus_not_fake_model_claim():
    client = TestClient(_app())
    script = client.get("/aura-intelligence/avatar-runtime.js")
    assert script.status_code == 200
    assert "window.AuraHost" in script.text
    assert "aura:state" in script.text
    assert "production 3D rig pending" in script.text
    assert "Production 3D Aura ready" in script.text
