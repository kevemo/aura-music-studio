from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

import aura_music_studio.aura_avatar_client_health as health_module
from aura_music_studio.aura_avatar_client_health import (
    AvatarClientHealthReport,
    AvatarClientHealthStore,
    _derived_state,
)
from aura_music_studio.aura_avatar_runtime import AuraAvatarRuntimeMiddleware, router


class _MemberMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.member = SimpleNamespace(user_id="member-health-test")
        return await call_next(request)


def _report(**overrides):
    payload = {
        "client_class": "desktop",
        "page_hidden": False,
        "webgl": True,
        "webgl2": True,
        "renderer_attempted": True,
        "renderer_loaded": True,
        "model_loaded": True,
        "layered_performance_supported": True,
        "model_load_ms": 1400.0,
        "frame_rate_fps": 59.7,
        "frame_samples": 150,
        "renderer_error_code": "none",
    }
    payload.update(overrides)
    return AvatarClientHealthReport(**payload)


def test_health_classification_is_bounded_and_does_not_claim_release_authority():
    assert _derived_state(_report()) == "healthy_3d_session"
    assert _derived_state(_report(webgl=False, webgl2=False)) == "webgl_unavailable"
    assert _derived_state(_report(renderer_error_code="model_load_failed", model_loaded=False)) == "renderer_error"
    assert _derived_state(_report(renderer_loaded=False, model_loaded=False)) == "renderer_load_incomplete"
    assert _derived_state(_report(frame_rate_fps=18.0)) == "frame_cadence_degraded"
    assert _derived_state(_report(model_load_ms=20000.0)) == "model_load_slow"
    assert _derived_state(
        _report(
            renderer_attempted=False,
            renderer_loaded=False,
            model_loaded=False,
            layered_performance_supported=False,
            model_load_ms=None,
            frame_rate_fps=60.0,
        )
    ) == "runtime_capable_no_3d_attempt"


def test_store_is_privacy_bounded_and_prunes_old_member_samples(tmp_path):
    store = AvatarClientHealthStore(tmp_path / "health.db", keep_per_user=4)
    for index in range(7):
        store.record("member-a", _report(frame_rate_fps=50.0 + index))
    store.record("member-b", _report(client_class="mobile"))

    summary = store.summary("member-a")
    assert summary["sample_count"] == 4
    assert summary["has_healthy_3d_session"] is True
    assert summary["client_classes_observed"] == ["desktop"]
    assert summary["readiness_authority"] is False
    assert summary["production_3d_ready_can_be_promoted_by_client_health"] is False
    assert summary["operator_validation_still_required"] is True
    assert summary["privacy_contract"]["ip_address_collected"] is False
    assert summary["privacy_contract"]["user_agent_collected"] is False
    assert summary["privacy_contract"]["viewport_dimensions_collected"] is False
    assert summary["privacy_contract"]["cpu_or_memory_details_collected"] is False
    assert summary["privacy_contract"]["device_fingerprint_collected"] is False

    with sqlite3.connect(store.db_path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(aura_avatar_client_health)")}
        assert "ip_address" not in columns
        assert "user_agent" not in columns
        assert "hostname" not in columns
        assert "fingerprint" not in columns
        assert "viewport_width" not in columns
        assert "hardware_concurrency" not in columns
        assert "device_memory" not in columns
        assert con.execute(
            "SELECT COUNT(*) FROM aura_avatar_client_health WHERE user_id='member-a'"
        ).fetchone()[0] == 4
        assert con.execute(
            "SELECT COUNT(*) FROM aura_avatar_client_health WHERE user_id='member-b'"
        ).fetchone()[0] == 1


def _app():
    app = FastAPI()
    app.include_router(router)

    @app.get("/aura-intelligence", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse("<html><body>Aura health</body></html>")

    app.add_middleware(_MemberMiddleware)
    app.add_middleware(AuraAvatarRuntimeMiddleware)
    return app


def test_authenticated_health_endpoints_persist_evidence_without_promoting_readiness(tmp_path, monkeypatch):
    test_store = AvatarClientHealthStore(tmp_path / "api-health.db", keep_per_user=8)
    monkeypatch.setattr(health_module, "store", test_store)
    client = TestClient(_app())

    response = client.post(
        "/aura-intelligence/api/avatar/client-health",
        json=_report().model_dump(mode="json"),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["derived_state"] == "healthy_3d_session"
    assert saved["readiness_authority"] is False
    assert saved["production_3d_ready_changed"] is False
    assert saved["operator_validation_still_required"] is True

    summary = client.get("/aura-intelligence/api/avatar/client-health").json()
    assert summary["sample_count"] == 1
    assert summary["latest"]["derived_state"] == "healthy_3d_session"
    assert summary["has_healthy_3d_session"] is True
    assert summary["production_3d_ready_can_be_promoted_by_client_health"] is False


def test_avatar_page_injects_runtime_then_client_health_script():
    client = TestClient(_app())
    page = client.get("/aura-intelligence")
    assert page.status_code == 200
    runtime_marker = "<script src='/aura-intelligence/avatar-runtime.js'></script>"
    health_marker = "<script src='/aura-intelligence/avatar-client-health.js'></script>"
    assert runtime_marker in page.text
    assert health_marker in page.text
    assert page.text.index(runtime_marker) < page.text.index(health_marker)


def test_client_health_script_collects_coarse_runtime_evidence_only():
    client = TestClient(_app())
    script = client.get("/aura-intelligence/avatar-client-health.js")
    assert script.status_code == 200
    assert "webgl2" in script.text
    assert "requestAnimationFrame" in script.text
    assert "modelLoadMs" in script.text
    assert "layeredPerformanceSupported" in script.text
    assert "navigator.userAgentData?.mobile" in script.text
    assert "navigator.userAgent" not in script.text.replace("navigator.userAgentData", "")
    assert "navigator.hardwareConcurrency" not in script.text
    assert "navigator.deviceMemory" not in script.text
    assert "devicePixelRatio" not in script.text
    assert "viewport_width" not in script.text
    assert "viewport_height" not in script.text
    assert "navigator.geolocation" not in script.text
    assert "document.cookie" not in script.text
    assert "aura:client-health" in script.text
    assert "credentials:'same-origin'" in script.text
    assert "keepalive:true" in script.text
