import json
import struct

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient
import pytest

from aura_music_studio.aura_avatar_runtime import (
    AVATAR_STATES,
    BROWSER_3D_RENDERER_IMPLEMENTED,
    LAYERED_PERFORMANCE_RUNTIME_IMPLEMENTED,
    AuraAvatarRuntimeMiddleware,
    _model_path,
    _renderer_module_url,
    avatar_status,
    router,
)
from aura_music_studio.aura_avatar_validator import (
    GAZE_ANIMATION_ALIASES,
    GESTURE_ANIMATION_ALIASES,
    VISEME_ANIMATION_ALIASES,
    validate_aura_glb,
)


def _write_glb(path, *, production_performance: bool = False):
    animations = ["idle", "welcome", "listen", "think", "speak", "celebrate", "warn"]
    mesh_name = "AuraFace"
    extensions_used = []
    morphs = ["viseme_aa", "blinkLeft"]
    if production_performance:
        animations = [
            "idle",
            "welcome",
            "listen",
            "think",
            "tool_running",
            "speak",
            "celebrate",
            "warn",
            "recording_coach",
            "studio_engineer",
        ]
        animations.extend(aliases[0] for aliases in VISEME_ANIMATION_ALIASES.values())
        animations.extend(aliases[0] for aliases in GAZE_ANIMATION_ALIASES.values())
        animations.extend(aliases[0] for aliases in GESTURE_ANIMATION_ALIASES.values())
        # Preserve order while removing collisions such as a future alias matching a base clip.
        animations = list(dict.fromkeys(animations))
        mesh_name = "AuraFaceLOD0"
        extensions_used = ["MSFT_lod"]
        morphs = ["viseme_aa", "viseme_oh", "jawOpen", "blinkLeft", "eyeLookOutLeft"]

    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "AuraRoot", "mesh": 0, "skin": 0}],
        "skins": [{"joints": [0]}],
        "meshes": [
            {
                "name": mesh_name,
                "extras": {"targetNames": morphs},
                "primitives": [{"attributes": {}, "targets": [{} for _ in morphs]}],
            }
        ],
        "animations": [{"name": name} for name in animations],
    }
    if extensions_used:
        document["extensionsUsed"] = extensions_used
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((4 - len(raw) % 4) % 4)
    total = 12 + 8 + len(raw)
    path.write_bytes(
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(raw), 0x4E4F534A)
        + raw
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
    assert status["model_valid"] is False
    assert status["renderer_implemented"] is True
    assert status["renderer_configured"] is True
    assert status["layered_performance_runtime_implemented"] is True
    assert LAYERED_PERFORMANCE_RUNTIME_IMPLEMENTED is True
    assert status["operator_validated"] is False
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "renderer_ready_model_asset_missing"
    assert status["performance_contract"]["protocol"] == "AuraHost.performance/v1"
    assert set(status["performance_contract"]["viseme_aliases"]) == set(VISEME_ANIMATION_ALIASES)
    assert "speaking" in AVATAR_STATES
    assert "tool_running" in AVATAR_STATES


def test_invalid_glb_cannot_become_production_ready(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    (root / "aura.glb").write_bytes(b"not-a-real-glb")
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.setenv("AURA_AVATAR_3D_RENDERER_READY", "true")
    status = avatar_status()
    assert status["model_installed"] is True
    assert status["model_valid"] is False
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "model_asset_invalid"
    assert status["model_url"] is None


def test_structurally_valid_but_incomplete_rig_cannot_be_promoted_by_flag(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    _write_glb(root / "aura.glb", production_performance=False)
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.delenv("AURA_AVATAR_MODEL_PATH", raising=False)
    monkeypatch.delenv("AURA_AVATAR_RENDERER_MODULE_URL", raising=False)
    monkeypatch.setenv("AURA_AVATAR_3D_RENDERER_READY", "true")

    status = avatar_status()
    assert status["model_installed"] is True
    assert status["model_valid"] is True
    assert status["model_validation"]["facial_rig_signal"] is True
    assert status["model_validation"]["production_rig_ready"] is False
    assert status["model_validation"]["full_viseme_animation_set"] is False
    assert status["renderer_implemented"] is BROWSER_3D_RENDERER_IMPLEMENTED is True
    assert status["renderer_configured"] is True
    assert status["operator_validated"] is True
    assert status["production_3d_ready"] is False
    assert status["truthful_state"] == "model_renderer_validated_performance_rig_incomplete"


def test_complete_performance_rig_still_requires_operator_validation(tmp_path, monkeypatch):
    root = tmp_path / "aura"
    root.mkdir()
    model = root / "aura.glb"
    _write_glb(model, production_performance=True)
    monkeypatch.setenv("AURA_AVATAR_ASSET_DIR", str(root))
    monkeypatch.delenv("AURA_AVATAR_MODEL_PATH", raising=False)
    monkeypatch.delenv("AURA_AVATAR_RENDERER_MODULE_URL", raising=False)
    monkeypatch.setenv("AURA_AVATAR_3D_RENDERER_READY", "false")

    validation = validate_aura_glb(model)
    assert validation["valid_glb"] is True
    assert validation["base_state_animation_ready"] is True
    assert validation["full_viseme_animation_set"] is True
    assert validation["viseme_animation_coverage"] == len(VISEME_ANIMATION_ALIASES)
    assert validation["gaze_animation_ready"] is True
    assert validation["gesture_animation_ready"] is True
    assert validation["layered_performance_clips_ready"] is True
    assert validation["production_rig_ready"] is True
    assert validation["root_bone_signal"] is True
    assert validation["lod_signal"] is True

    status = avatar_status()
    assert status["model_validation"]["production_rig_ready"] is True
    assert status["operator_validated"] is False
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


def test_avatar_script_exposes_real_glb_renderer_state_bus_and_layered_performance_api():
    client = TestClient(_app())
    script = client.get("/aura-intelligence/avatar-runtime.js")
    assert script.status_code == 200
    assert "window.AuraHost" in script.text
    assert "performance:{setViseme,setGaze,gesture,speechFrame" in script.text
    assert "aura:state" in script.text
    assert "aura:performance" in script.text
    assert "aura:viseme-input" in script.text
    assert "aura:gaze-input" in script.text
    assert "aura:gesture-input" in script.text
    assert "aura:speech-frame" in script.text
    assert "customElements.get('model-viewer')" in script.text
    assert "availableAnimations" in script.text
    assert "animationName" in script.text
    assert "appendAnimation" in script.text
    assert "detachAnimation" in script.text
    assert "animationCrossfadeDuration=180" in script.text
    assert "gesture('blink'" in script.text
    assert "3D renderer ready · Aura rig asset pending" in script.text
    assert "structure validation failed" in script.text
    assert "layered performance rig incomplete" in script.text
    assert "layered performance validated" in script.text
