from __future__ import annotations

import json

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
    assert payload["product"] == "Shared Skies Streaming Studios"
    assert payload["scheduler"]["enabled"] is False
    assert payload["deployment"]["ingest_configured"] is False
    assert payload["deployment"]["provider_oauth_configured"] is False
    assert payload["truth_boundary"]["production_ready"] is False
    assert payload["truth_boundary"]["external_provider_approval_required"] is True
    assert "workers" in payload["scheduler"]
    assert payload["scheduler"]["raw_worker_errors_exposed"] is False
    assert "relay" in payload
    assert "vault" in payload


def test_owner_runtime_snapshot_redacts_worker_error_text(monkeypatch):
    class FakeWorker:
        def __init__(self, *_args, **_kwargs):
            pass

        def worker_health(self, **_kwargs):
            return [
                {
                    "worker_id": "worker-redaction-test",
                    "status": "retry",
                    "last_seen_at": "2026-09-04T19:00:00+00:00",
                    "last_claimed_schedule_id": "schedule-1",
                    "healthy": True,
                    "last_error": "provider rejected rtmp://secret.example/live/private-key",
                }
            ]

    monkeypatch.setattr(owner_ops, "SharedSkyWorker", FakeWorker)
    monkeypatch.setattr(
        owner_ops.shared_sky,
        "owner_status",
        lambda: {"vault": {}, "counts": {}, "live_broadcasts": []},
    )

    payload = owner_ops._runtime_snapshot()
    encoded = json.dumps(payload)

    assert payload["scheduler"]["workers"][0]["error_present"] is True
    assert payload["scheduler"]["raw_worker_errors_exposed"] is False
    assert "last_error" not in encoded
    assert "secret.example" not in encoded
    assert "private-key" not in encoded


def test_owner_runtime_page_is_no_store(monkeypatch):
    monkeypatch.setattr(owner_ops, "owner_session_authorized", lambda _request: True)
    app = FastAPI()
    app.include_router(owner_ops.router)
    client = TestClient(app)

    response = client.get("/owner/shared-sky/runtime")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Shared Skies Runtime &amp; Operations" in response.text or "Shared Skies Runtime & Operations" in response.text
