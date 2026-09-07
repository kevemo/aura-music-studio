from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_media_plane import (
    IngestSessionCreate,
    NodeHeartbeat,
    SharedSkyMediaPlane,
)
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    ProjectCreate,
    SharedSkyStore,
)


def _fixture(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "media-plane@example.com",
        "Media Plane Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp = EspStore(accounts)
    store = SharedSkyStore(esp, SharedSkyVault("media-plane-test-vault"))
    project = store.create_project(user["id"], ProjectCreate(name="Media Plane Show"))
    broadcast = store.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], title="Media Plane LIVE"),
    )
    plane = SharedSkyMediaPlane(store.db_path)
    return user, store, broadcast, plane


def test_ingest_session_fails_closed_without_signing_secret(tmp_path, monkeypatch):
    user, _, broadcast, plane = _fixture(tmp_path)
    monkeypatch.delenv("SHARED_SKY_INGEST_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.test/live")

    with pytest.raises(RuntimeError, match="signing"):
        plane.create_session(
            user["id"],
            IngestSessionCreate(broadcast_id=broadcast["id"], ttl_seconds=300),
        )


def test_signed_ingest_session_verifies_and_revocation_invalidates(tmp_path, monkeypatch):
    user, _, broadcast, plane = _fixture(tmp_path)
    monkeypatch.setenv("SHARED_SKY_INGEST_SIGNING_SECRET", "unit-test-signing-secret-123")
    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.test/live")

    session = plane.create_session(
        user["id"],
        IngestSessionCreate(broadcast_id=broadcast["id"], ttl_seconds=300),
    )
    assert session["credential"] not in {"", None}
    assert session["ingest_url"].startswith("rtmps://ingest.example.test/live/")
    verified = plane.verify_token(session["credential"])
    assert verified["session_id"] == session["id"]
    assert verified["broadcast_id"] == broadcast["id"]

    revoked = plane.revoke(user["id"], session["id"])
    assert revoked["state"] == "revoked"
    with pytest.raises(ValueError, match="not active"):
        plane.verify_token(session["credential"])


def test_media_node_heartbeat_becomes_selectable_ingest_target(tmp_path, monkeypatch):
    user, _, broadcast, plane = _fixture(tmp_path)
    monkeypatch.setenv("SHARED_SKY_INGEST_SIGNING_SECRET", "unit-test-signing-secret-123")
    monkeypatch.delenv("SHARED_SKY_INGEST_BASE_URL", raising=False)

    accepted = plane.heartbeat(
        NodeHeartbeat(
            node_id="lon-1",
            region="uk-london",
            ingest_protocols=["rtmps", "srt"],
            capacity=20,
            active_sessions=2,
            healthy=True,
            public_ingest_base="rtmps://lon-ingest.example.test/live",
        )
    )
    assert accepted["accepted"] is True

    session = plane.create_session(
        user["id"],
        IngestSessionCreate(broadcast_id=broadcast["id"], ttl_seconds=300),
    )
    assert session["node_id"] == "lon-1"
    assert session["ingest_url"].startswith("rtmps://lon-ingest.example.test/live/")
    status = plane.status()
    assert status["healthy_nodes"] == 1
    assert status["active_ingest_sessions"] == 1
    assert status["media_termination_deployed"] is False


def test_node_heartbeat_route_requires_configured_node_secret(monkeypatch):
    import aura_music_studio.shared_sky_media_plane as module

    monkeypatch.delenv("SHARED_SKY_MEDIA_NODE_SECRET", raising=False)
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/shared-sky/internal/media-nodes/heartbeat",
        json={
            "node_id": "node-1",
            "region": "test",
            "ingest_protocols": ["rtmps"],
            "capacity": 10,
            "active_sessions": 0,
            "healthy": True,
        },
        headers={"X-Shared-Sky-Node-Key": "anything"},
    )
    assert response.status_code == 401


def test_owner_media_plane_status_route_is_mounted_on_production_app():
    from app import app

    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.routes
    }
    assert ("/owner/shared-sky/api/media-plane", ("GET",)) in routes
    assert ("/shared-sky/api/ingest-sessions", ("POST",)) in routes
    assert ("/shared-sky/internal/media-nodes/heartbeat", ("POST",)) in routes


def test_status_never_exposes_signing_or_node_secret(tmp_path, monkeypatch):
    _, _, _, plane = _fixture(tmp_path)
    monkeypatch.setenv("SHARED_SKY_INGEST_SIGNING_SECRET", "super-secret-signing-value")
    monkeypatch.setenv("SHARED_SKY_MEDIA_NODE_SECRET", "super-secret-node-value")
    payload = plane.status()
    rendered = repr(payload)
    assert "super-secret-signing-value" not in rendered
    assert "super-secret-node-value" not in rendered
    assert payload["signing_configured"] is True
    assert payload["node_auth_configured"] is True


def test_ingest_credential_response_is_never_cacheable(monkeypatch):
    import aura_music_studio.shared_sky_media_plane as module

    member = type("Member", (), {"user_id": "member-1"})()
    monkeypatch.setattr(module, "_member", lambda _request: (member, None))
    monkeypatch.setattr(
        module.media_plane,
        "create_session",
        lambda user_id, body: {
            "id": "session-1",
            "broadcast_id": body.broadcast_id,
            "node_id": None,
            "state": "issued",
            "expires_at": datetime.now(timezone.utc).isoformat(),
            "ingest_url": "rtmps://ingest.example.test/live/broadcast-1/credential-secret",
            "credential": "credential-secret",
        },
    )

    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)
    response = client.post(
        "/shared-sky/api/ingest-sessions",
        json={"broadcast_id": "broadcast-1", "ttl_seconds": 300},
    )

    assert response.status_code == 200
    assert response.json()["session"]["credential"] == "credential-secret"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
