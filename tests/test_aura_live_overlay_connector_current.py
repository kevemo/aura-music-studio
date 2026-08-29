from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class RequestStub:
    def __init__(self, *, member=None, authorization: str | None = None):
        self.state = SimpleNamespace(member=member)
        self.headers = {}
        if authorization is not None:
            self.headers["Authorization"] = authorization


def _configure(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "live-relay.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
    monkeypatch.setattr(engine, "DB_PATH", db)
    connector._init_schema()
    engine._init_schema()
    return connector, engine, db


def _seed_connector(connector, token: str = "relay-secret-token-for-tests-0000000001", user_id: str = "creator-1"):
    now = connector._now()
    with connector._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at) VALUES(?,?,1,?,?,?)",
            (user_id, connector._hash(token), "test relay", now, now),
        )
    return token


def test_connector_routes_mount_once_through_existing_live_engine():
    from aura_music_studio import access_control, api as api_mod
    from aura_music_studio import aura_live_overlay_engine as engine

    engine_paths = [getattr(route, "path", "") for route in engine.router.routes]
    app_paths = [getattr(route, "path", "") for route in api_mod.app.routes]
    for path in (
        "/api/live-overlays/connector",
        "/api/live-overlays/connector/rotate",
        "/api/live-overlays/connector/disable",
        "/live-overlay/source/relay/ingest",
    ):
        assert engine_paths.count(path) == 1
        assert app_paths.count(path) == 1
    assert "/live-overlay/source/" in access_control.PUBLIC_PREFIXES
    assert "/live-overlay/connector" not in access_control.PUBLIC_PREFIXES


def test_rotate_returns_bearer_secret_once_without_putting_it_in_url(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    member = SimpleNamespace(user_id="creator-1")
    response = connector.rotate_connector(connector.ConnectorRotate(label="Approved relay"), RequestStub(member=member))
    payload = json.loads(response.body)
    token = payload["token"]
    assert payload["ingest_path"] == "/live-overlay/source/relay/ingest"
    assert payload["authorization_scheme"] == "Bearer"
    assert payload["token_returned_once"] is True
    assert payload["token_in_url"] is False
    assert token not in payload["ingest_path"]
    assert response.headers["cache-control"] == "private, no-store"
    with connector._connect() as con:
        row = con.execute("SELECT token_hash FROM live_overlay_connectors WHERE user_id='creator-1'").fetchone()
    assert row["token_hash"] == connector._hash(token)
    assert row["token_hash"] != token


def test_ingest_requires_bearer_auth_and_unknown_tokens_fail_closed(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    body = connector.ConnectorEvent(event_id="event-00000001", event_type="gift", payload={})
    with pytest.raises(HTTPException) as missing:
        connector.connector_ingest(body, RequestStub())
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as unknown:
        connector.connector_ingest(body, RequestStub(authorization="Bearer " + "x" * 40))
    assert unknown.value.status_code == 404


def test_normalized_event_is_idempotent_and_updates_engine_once(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = connector.ConnectorEvent(
        event_id="event-00000001",
        event_type="gift",
        payload={"username": "Laura", "gift_name": "Rose", "coins": 1},
    )
    first = connector.connector_ingest(body, request)
    second = connector.connector_ingest(body, request)
    assert b'"duplicate":false' in first.body
    assert b'"normalized_relay":true' in first.body
    assert b'"direct_tiktok_connection_claimed":false' in first.body
    assert b'"duplicate":true' in second.body
    with engine._connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0]
    with connector._connect() as con:
        receipt = con.execute(
            "SELECT state,completed_at FROM live_overlay_connector_receipts WHERE user_id='creator-1' AND event_id='event-00000001'"
        ).fetchone()
    assert count == 1
    assert receipt["state"] == "completed"
    assert receipt["completed_at"]


def test_failed_processing_releases_receipt_so_retry_is_not_lost(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = connector.ConnectorEvent(event_id="event-retry-0001", event_type="gift", payload={"username": "Dave", "coins": 5})

    def fail_once(*args, **kwargs):
        raise RuntimeError("synthetic processing failure")

    monkeypatch.setattr(connector, "process_overlay_event", fail_once)
    with pytest.raises(RuntimeError, match="synthetic processing failure"):
        connector.connector_ingest(body, request)
    with connector._connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_connector_receipts WHERE user_id='creator-1' AND event_id='event-retry-0001'"
        ).fetchone()[0]
    assert count == 0

    monkeypatch.setattr(connector, "process_overlay_event", engine.process_overlay_event)
    retry = connector.connector_ingest(body, request)
    assert retry.status_code == 200
    assert b'"duplicate":false' in retry.body


def test_completion_failure_keeps_processing_claim_to_prevent_replay(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = connector.ConnectorEvent(
        event_id="event-complete-fail-01",
        event_type="gift",
        payload={"username": "Tuckz", "gift_name": "Rose", "coins": 1},
    )

    def completion_failure(*args, **kwargs):
        raise RuntimeError("receipt completion failure")

    monkeypatch.setattr(connector, "_complete_event", completion_failure)
    with pytest.raises(RuntimeError, match="receipt completion failure"):
        connector.connector_ingest(body, request)

    with engine._connect() as con:
        applied = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0]
    with connector._connect() as con:
        receipt = con.execute(
            "SELECT state FROM live_overlay_connector_receipts WHERE user_id='creator-1' AND event_id='event-complete-fail-01'"
        ).fetchone()
    assert applied == 1
    assert receipt["state"] == "processing"

    retry = connector.connector_ingest(body, request)
    assert retry.status_code == 409
    with engine._connect() as con:
        applied_after_retry = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0]
    assert applied_after_retry == 1


def test_concurrent_processing_receipt_returns_retryable_conflict(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    now = connector._now()
    with connector._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_connector_receipts(user_id,event_id,event_type,state,received_at) VALUES(?,?,?,'processing',?)",
            ("creator-1", "event-processing-01", "gift", now),
        )
    response = connector.connector_ingest(
        connector.ConnectorEvent(event_id="event-processing-01", event_type="gift", payload={}),
        RequestStub(authorization=f"Bearer {token}"),
    )
    assert response.status_code == 409
    assert response.headers["retry-after"] == "1"
    assert b'"processing":true' in response.body


def test_connector_rejects_unknown_event_and_oversize_payload(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    with pytest.raises(HTTPException) as unsupported:
        connector.connector_ingest(
            connector.ConnectorEvent(event_id="event-unknown-01", event_type="not-a-live-event", payload={}),
            request,
        )
    assert unsupported.value.status_code == 400

    monkeypatch.setattr(connector, "MAX_PAYLOAD_BYTES", 32)
    with pytest.raises(HTTPException) as oversized:
        connector.connector_ingest(
            connector.ConnectorEvent(event_id="event-large-0001", event_type="comment", payload={"message": "x" * 100}),
            request,
        )
    assert oversized.value.status_code == 413


def test_rate_limit_is_durable_in_sqlite_not_process_memory(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(connector, "MAX_EVENTS_PER_MINUTE", 2)
    digest = connector._hash("token-for-rate-limit")
    connector._rate_limit(digest)
    connector._rate_limit(digest)
    with pytest.raises(HTTPException) as limited:
        connector._rate_limit(digest)
    assert limited.value.status_code == 429
    with connector._connect() as con:
        count = con.execute(
            "SELECT event_count FROM live_overlay_connector_rate WHERE token_hash=?",
            (digest,),
        ).fetchone()[0]
    assert count == 2


def test_status_never_claims_direct_tiktok_or_moderation_authority(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    response = connector.connector_status(RequestStub(member=SimpleNamespace(user_id="creator-1")))
    assert response["provider_connection_state"] == "external_dependency"
    assert response["direct_tiktok_connection_claimed"] is False
    assert response["provider_moderation_authority_claimed"] is False
    assert response["transport"] == "bearer_token_normalized_relay"


def test_legacy_stale_pr_receipt_schema_migrates_without_replay(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector

    db = tmp_path / "legacy-relay.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE live_overlay_connector_receipts (
                user_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY(user_id,event_id)
            );
            INSERT INTO live_overlay_connector_receipts(user_id,event_id,event_type,received_at)
            VALUES('creator-1','legacy-event-01','gift','2026-08-29T00:00:00+00:00');
            """
        )
    connector._init_schema()
    with connector._connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(live_overlay_connector_receipts)").fetchall()}
        migrated = con.execute(
            "SELECT state FROM live_overlay_connector_receipts WHERE user_id='creator-1' AND event_id='legacy-event-01'"
        ).fetchone()
    assert {"state", "completed_at"}.issubset(columns)
    assert migrated["state"] == "completed"
