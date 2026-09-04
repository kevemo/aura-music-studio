from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.shared_sky_owner_ops as owner_ops


def test_owner_runtime_routes_are_mounted_on_canonical_production_app():
    from app import app

    routes = {(getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set()))) for route in app.routes}
    assert ("/owner/shared-sky/api/runtime", ("GET",)) in routes
    assert ("/owner/shared-sky/runtime", ("GET",)) in routes


def test_owner_runtime_api_fails_closed_without_owner_session():
    app = FastAPI()
    app.include_router(owner_ops.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/owner/shared-sky/api/runtime")
    assert response.status_code == 401


def test_owner_runtime_snapshot_exposes_truthful_deployment_state(monkeypatch):
    monkeypatch.setattr(owner_ops, "owner_session_authorized", lambda _request: True)
    monkeypatch.setenv("SHARED_SKY_SCHEDULER_ENABLED", "0")
    monkeypatch.delenv("SHARED_SKY_INGEST_BASE_URL", raising=False)
    monkeypatch.delenv("SHARED_SKY_PROVIDER_OAUTH_READY", raising=False)

    app = FastAPI()
    app.include_router(owner_ops.router)
    client = TestClient(app)

    response = client.get("/owner/shared-sky/api/runtime")
    assert response.status_code == 200
    payload = response.json()
    assert payload["product"] == "Shared Sky Streaming Studios"
    assert payload["scheduler"]["enabled"] is False
    assert payload["deployment"]["ingest_configured"] is False
    assert payload["deployment"]["provider_oauth_configured"] is False
    assert payload["truth_boundary"]["production_ready"] is False
    assert payload["truth_boundary"]["external_provider_approval_required"] is True
    assert "workers" in payload["scheduler"]
    assert "relay" in payload
    assert "vault" in payload


def test_owner_runtime_page_is_no_store(monkeypatch):
    monkeypatch.setattr(owner_ops, "owner_session_authorized", lambda _request: True)
    app = FastAPI()
    app.include_router(owner_ops.router)
    client = TestClient(app)

    response = client.get("/owner/shared-sky/runtime")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Shared Sky Runtime &amp; Operations" in response.text or "Shared Sky Runtime & Operations" in response.text
