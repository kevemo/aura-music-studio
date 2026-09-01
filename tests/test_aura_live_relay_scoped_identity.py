from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient


def _configure(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine
    from aura_music_studio import aura_live_relay_identity as identity

    db = tmp_path / "scoped-relay.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._rule_last_fired.clear()
    engine._init_schema()
    monkeypatch.setattr(
        engine,
        "_require_active_member",
        lambda user_id: {"status": "active", "user_id": user_id},
    )
    identity._ensure_schema()
    token = "scoped-relay-test-token-0000000000000001"
    now = engine._now()
    with engine._connect() as con:
        con.execute(
            """INSERT INTO live_overlay_connectors(
                   user_id,token_hash,enabled,label,created_at,updated_at,last_event_at
               ) VALUES(?,?,1,'scoped relay',?,?,NULL)""",
            ("creator-1", engine._hash_relay_token(token), now, now),
        )
    return engine, identity, token


def _body(
    *,
    provider="documented_provider",
    session_id="live-session-001",
    event_id="provider-event-0001",
    event_type="gift",
    occurred_at="2026-09-01T05:30:00Z",
    payload=None,
):
    return {
        "provider": provider,
        "session_id": session_id,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "payload": payload or {"username": "Laura", "gift_name": "Rose", "coins": 1},
    }


def _client(identity, *, downstream_calls=None):
    from aura_music_studio.aura_live_relay_error_boundary import AuraLiveRelayErrorBoundaryMiddleware

    app = FastAPI()
    calls = downstream_calls if downstream_calls is not None else []

    @app.post("/live-overlay/source/relay/events")
    async def ingest(request: Request):
        data = await request.json()
        calls.append(data)
        return JSONResponse(
            {
                "accepted": True,
                "duplicate": False,
                "event_id": data["event_id"],
                "normalized_relay": True,
                "provider_connected": False,
                "provider_write_authority": False,
            }
        )

    @app.get("/api/live-overlays/event-contract")
    async def contract():
        return {"schema_version": 2, "accepted_event_types": ["gift"]}

    @app.get("/api/live-overlays/connector")
    async def status(request: Request):
        request.state.member = SimpleNamespace(user_id="creator-1")
        return {
            "recent_deliveries": [],
            "provider_connected": False,
            "provider_write_authority": False,
        }

    @app.get("/live-overlay-studio/connector")
    async def setup():
        return HTMLResponse(
            '<html><body>Send JSON with <code>{"event_id":"provider-unique-id",'
            '"event_type":"gift","payload":{"username":"viewer"}}</code>. '
            'Duplicate event IDs are applied once only;</body></html>'
        )

    app.add_middleware(identity.AuraLiveRelayIdentityMiddleware)
    # Added after identity so this is the outer relay-only HTTPException renderer.
    app.add_middleware(AuraLiveRelayErrorBoundaryMiddleware)
    return TestClient(app), calls


def test_provider_session_event_identity_is_idempotent_and_session_scoped(tmp_path, monkeypatch):
    engine, identity, token = _configure(tmp_path, monkeypatch)
    client, calls = _client(identity)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/live-overlay/source/relay/events", json=_body(), headers=headers)
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["event_id"] == "provider-event-0001"
    assert first_data["provider"] == "documented_provider"
    assert first_data["session_id"] == "live-session-001"
    assert first_data["occurred_at"] == "2026-09-01T05:30:00Z"
    assert first_data["scoped_provider_identity"] is True
    assert len(calls) == 1
    assert calls[0]["event_id"].startswith("scoped:")
    assert calls[0]["event_id"] != "provider-event-0001"

    duplicate = client.post("/live-overlay/source/relay/events", json=_body(), headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["event_id"] == "provider-event-0001"
    assert len(calls) == 1

    second_session = client.post(
        "/live-overlay/source/relay/events",
        json=_body(session_id="live-session-002"),
        headers=headers,
    )
    assert second_session.status_code == 200
    assert len(calls) == 2
    assert calls[0]["event_id"] != calls[1]["event_id"]

    second_provider = client.post(
        "/live-overlay/source/relay/events",
        json=_body(provider="provider_two"),
        headers=headers,
    )
    assert second_provider.status_code == 200
    assert len(calls) == 3

    with engine._connect() as con:
        rows = con.execute(
            """SELECT provider,session_id,event_id,state,occurred_at
               FROM live_overlay_connector_receipts_v2
               WHERE user_id='creator-1' AND provider<>'legacy'
               ORDER BY provider,session_id"""
        ).fetchall()
    assert len(rows) == 3
    assert all(row["state"] == "processed" for row in rows)
    assert all(row["event_id"] == "provider-event-0001" for row in rows)


def test_same_scoped_event_rejects_payload_type_or_timestamp_drift(tmp_path, monkeypatch):
    _, identity, token = _configure(tmp_path, monkeypatch)
    client, calls = _client(identity)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/live-overlay/source/relay/events", json=_body(), headers=headers).status_code == 200

    changed_payload = client.post(
        "/live-overlay/source/relay/events",
        json=_body(payload={"username": "Laura", "gift_name": "Rose", "coins": 2}),
        headers=headers,
    )
    assert changed_payload.status_code == 409

    changed_time = client.post(
        "/live-overlay/source/relay/events",
        json=_body(occurred_at="2026-09-01T05:31:00Z"),
        headers=headers,
    )
    assert changed_time.status_code == 409

    changed_type = client.post(
        "/live-overlay/source/relay/events",
        json=_body(event_type="follow"),
        headers=headers,
    )
    assert changed_type.status_code == 409
    assert len(calls) == 1


def test_relay_identity_validation_fails_closed_as_4xx_not_500(tmp_path, monkeypatch):
    _, identity, token = _configure(tmp_path, monkeypatch)
    client, calls = _client(identity)
    headers = {"Authorization": f"Bearer {token}"}

    missing_provider = _body()
    del missing_provider["provider"]
    assert client.post("/live-overlay/source/relay/events", json=missing_provider, headers=headers).status_code == 422

    naive = client.post(
        "/live-overlay/source/relay/events",
        json=_body(occurred_at="2026-09-01T05:30:00"),
        headers=headers,
    )
    assert naive.status_code == 400

    bad_provider = client.post(
        "/live-overlay/source/relay/events",
        json=_body(provider="TikTok LIVE"),
        headers=headers,
    )
    assert bad_provider.status_code == 422

    nested = client.post(
        "/live-overlay/source/relay/events",
        json=_body(payload={"username": {"nested": "not allowed"}}),
        headers=headers,
    )
    assert nested.status_code == 422
    assert calls == []


def test_legacy_receipts_are_consumed_without_guessing_provider_identity(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine
    from aura_music_studio import aura_live_relay_identity as identity

    db = tmp_path / "legacy-scoped-relay.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE live_overlay_connector_receipts (
                user_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                last_error_code TEXT,
                PRIMARY KEY(user_id,event_id)
            );
            INSERT INTO live_overlay_connector_receipts VALUES(
                'creator-1','legacy-event-0001','gift',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'processing','2026-08-31T23:00:00+00:00',NULL,NULL
            );
            """
        )

    identity._ensure_schema()
    with engine._connect() as con:
        row = con.execute(
            """SELECT provider,session_id,state,last_error_code
               FROM live_overlay_connector_receipts_v2
               WHERE user_id='creator-1' AND event_id='legacy-event-0001'"""
        ).fetchone()
    assert row["provider"] == "legacy"
    assert row["session_id"] == "legacy"
    assert row["state"] == "processed"
    assert row["last_error_code"] == "LegacyReceiptConsumed"


def test_contract_status_and_setup_surface_scoped_identity_without_raw_payload(tmp_path, monkeypatch):
    engine, identity, token = _configure(tmp_path, monkeypatch)
    client, _ = _client(identity)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/live-overlay/source/relay/events", json=_body(), headers=headers).status_code == 200

    contract = client.get("/api/live-overlays/event-contract").json()
    assert contract["schema_version"] == 3
    assert contract["relay_identity_required"] is True
    assert contract["replay_identity"] == ["provider", "session_id", "event_id"]
    assert "occurred_at" in contract["relay_identity_fields"]

    status = client.get("/api/live-overlays/connector").json()
    assert status["relay_identity_required"] is True
    assert status["recent_deliveries"][0]["provider"] == "documented_provider"
    assert status["recent_deliveries"][0]["session_id"] == "live-session-001"
    serialized = json.dumps(status)
    assert "Laura" not in serialized
    assert "Rose" not in serialized
    assert token not in serialized

    setup = client.get("/live-overlay-studio/connector")
    assert setup.status_code == 200
    assert '"provider":"documented_provider"' in setup.text
    assert '"session_id":"live-session-001"' in setup.text
    assert '"occurred_at":"2026-09-01T05:30:00Z"' in setup.text
    assert "Duplicate provider + LIVE session + event IDs" in setup.text

    with engine._connect() as con:
        row = con.execute(
            """SELECT payload_sha256,immutable_sha256
               FROM live_overlay_connector_receipts_v2
               WHERE provider='documented_provider' AND event_id='provider-event-0001'"""
        ).fetchone()
    assert len(row["payload_sha256"]) == 64
    assert len(row["immutable_sha256"]) == 64
    assert row["payload_sha256"] == hashlib.sha256(
        json.dumps(
            {"coins": 1, "gift_name": "Rose", "username": "Laura"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
