from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import shared_sky_studio_ingest as ingest


def _studio_session():
    return {
        "id": "studio-1",
        "project_id": "project-1",
        "broadcast_id": "broadcast-1",
        "version": 7,
        "profile": {"width": 1920, "height": 1080, "orientation": "landscape"},
    }


def test_issue_attaches_only_opaque_ingest_id_and_redacts_event(monkeypatch):
    configured = []
    revoked = []
    events = []
    monkeypatch.setattr(ingest, "_require_inactive", lambda *_: {"ingest_session_id": "old-1"})
    monkeypatch.setattr(
        ingest.media_plane,
        "create_session",
        lambda *_args, **_kwargs: {
            "id": "new-1",
            "broadcast_id": "broadcast-1",
            "node_id": "node-1",
            "state": "issued",
            "expires_at": "2026-09-05T04:00:00+00:00",
            "ingest_url": "rtmps://ingest.example/live/SECRET",
            "credential": "SECRET",
        },
    )
    monkeypatch.setattr(ingest, "_configure_ingest", lambda _u, _s, sid: configured.append(sid) or {})
    monkeypatch.setattr(ingest.media_plane, "revoke", lambda _u, sid: revoked.append(sid) or {"id": sid})
    monkeypatch.setattr(ingest.media_plane, "status", lambda: {"media_termination_deployed": False})
    monkeypatch.setattr(ingest.shared_sky, "event", lambda *args: events.append(args))

    payload = ingest.issue_ingest("user-1", _studio_session(), 900)
    assert configured == ["new-1"]
    assert revoked == ["old-1"]
    assert payload["session"]["credential"] == "SECRET"
    rendered_events = repr(events)
    assert "SECRET" not in rendered_events
    assert "rtmps://" not in rendered_events
    assert payload["secret_persisted_by_studio"] is False
    assert payload["media_termination_deployed"] is False


def test_issue_revokes_new_credential_if_transport_binding_fails(monkeypatch):
    revoked = []
    monkeypatch.setattr(ingest, "_require_inactive", lambda *_: {})
    monkeypatch.setattr(
        ingest.media_plane,
        "create_session",
        lambda *_args, **_kwargs: {"id": "new-1", "broadcast_id": "broadcast-1"},
    )
    monkeypatch.setattr(
        ingest,
        "_configure_ingest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("configure failed")),
    )
    monkeypatch.setattr(ingest.media_plane, "revoke", lambda _u, sid: revoked.append(sid) or {})
    with pytest.raises(RuntimeError, match="configure failed"):
        ingest.issue_ingest("user-1", _studio_session(), 900)
    assert revoked == ["new-1"]


def test_active_transport_blocks_rotation_and_revocation(monkeypatch):
    monkeypatch.setattr(
        ingest.transport,
        "status",
        lambda *_: {"session": {"state": "live", "ingest_session_id": "ingest-1"}},
    )
    with pytest.raises(ingest.StudioTransportError, match="cannot be replaced or revoked"):
        ingest._require_inactive("user-1", "broadcast-1")


def test_status_never_returns_secret_bearing_fields(monkeypatch):
    monkeypatch.setattr(
        ingest,
        "_transport_state",
        lambda *_: {"state": "ready", "ingest_session_id": "ingest-1"},
    )
    monkeypatch.setattr(
        ingest,
        "_metadata",
        lambda *_: {
            "id": "ingest-1",
            "broadcast_id": "broadcast-1",
            "state": "issued",
            "expires_at": "2026-09-05T04:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        ingest.media_plane,
        "status",
        lambda: {
            "signing_configured": True,
            "ingest_base_configured": True,
            "healthy_nodes": 1,
            "media_termination_deployed": False,
        },
    )
    payload = ingest.ingest_status("user-1", _studio_session())
    rendered = repr(payload)
    session = payload.get("session") or {}
    assert "credential" not in session
    assert "ingest_url" not in session
    assert "token_hash" not in session
    assert "token_hash" not in rendered.lower()
    assert "rtmps://" not in rendered.lower()
    assert payload["secret_recoverable"] is False
    assert payload["media_plane"]["media_termination_deployed"] is False


def test_issue_route_is_no_store(monkeypatch):
    member = type("Member", (), {"user_id": "user-1"})()
    monkeypatch.setattr(ingest, "_member", lambda _r: (member, None))
    monkeypatch.setattr(ingest, "_studio_session", lambda *_args, **_kwargs: _studio_session())
    monkeypatch.setattr(
        ingest,
        "issue_ingest",
        lambda *_args, **_kwargs: {
            "session": {"credential": "SECRET", "ingest_url": "rtmps://example/SECRET"},
            "display_once": True,
            "secret_persisted_by_studio": False,
            "media_termination_deployed": False,
        },
    )
    app = FastAPI()
    app.include_router(ingest.router)
    client = TestClient(app)
    response = client.post(
        "/shared-sky/studio/api/sessions/studio-1/ingest",
        json={"expected_studio_version": 7, "ttl_seconds": 900},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_installer_is_idempotent_on_production_app():
    from app import app

    prefix = "/shared-sky/studio/api/sessions/{session_id}/ingest"
    expected = {
        prefix,
        prefix + "/revoke",
    }

    def matching_paths():
        return [
            getattr(route, "path", "")
            for route in app.routes
            if getattr(route, "path", "").startswith(prefix)
        ]

    before = matching_paths()
    ingest.install_shared_sky_studio_ingest(app)
    after_once = matching_paths()
    ingest.install_shared_sky_studio_ingest(app)
    after_twice = matching_paths()
    assert after_twice == after_once
    assert len(after_twice) == 3
    assert expected.issubset(set(after_twice))
    assert set(before).issubset(set(after_twice))
