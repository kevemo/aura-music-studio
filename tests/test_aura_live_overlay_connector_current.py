from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _prepare(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "relay.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
    monkeypatch.setattr(engine, "DB_PATH", db)
    connector._init_schema()
    engine._init_schema()
    token = "relay-secret-token-for-tests-000000000000"
    now = connector._now()
    with connector._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at) VALUES(?,?,1,?,?,?)",
            ("u1", connector._hash(token), "test relay", now, now),
        )
    return connector, engine, token


def test_connector_router_is_mounted_and_ingress_has_independent_token_boundary():
    from aura_music_studio import access_control, api as api_mod
    from aura_music_studio.aura_live_overlay_connector import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/live-overlays/connector" in paths
    assert "/api/live-overlays/connector/rotate" in paths
    assert "/api/live-overlays/connector/disable" in paths
    assert "/live-overlay/connector/{token}" in paths
    assert "/live-overlay/connector/" in access_control.PUBLIC_PREFIXES
    assert "app.include_router(aura_live_overlay_connector_router)" in inspect.getsource(api_mod)


def test_connector_credential_is_random_returned_once_and_stored_as_sha256_only():
    from aura_music_studio import aura_live_overlay_connector as mod

    assert "hashlib.sha256" in inspect.getsource(mod._hash)
    source = inspect.getsource(mod.rotate_connector)
    assert "secrets.token_urlsafe" in source
    assert "token_hash" in source
    assert '"token_returned_once": True' in source
    schema = inspect.getsource(mod._init_schema)
    assert "token_hash TEXT NOT NULL UNIQUE" in schema
    assert "token TEXT" not in schema


def test_status_never_claims_direct_tiktok_or_provider_write_authority(tmp_path, monkeypatch):
    connector, _engine, _token = _prepare(tmp_path, monkeypatch)
    request = SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id="u1")))
    response = connector.connector_status(request)
    assert b'"direct_tiktok_connection_claimed":false' in response.body
    assert b'"provider_write_authority":false' in response.body
    assert response.headers["cache-control"] == "private, no-store"


def test_event_id_is_idempotent_and_only_one_overlay_event_is_recorded(tmp_path, monkeypatch):
    connector, engine, token = _prepare(tmp_path, monkeypatch)
    body = connector.ConnectorEvent(
        event_id="event-00000001",
        event_type="gift",
        payload={"username": "Laura", "gift_name": "Rose", "coins": 1},
    )
    first = connector.connector_ingest(token, body)
    second = connector.connector_ingest(token, body)
    assert b'"duplicate":false' in first.body
    assert b'"duplicate":true' in second.body
    with engine._connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='u1' AND event_type='gift'"
        ).fetchone()[0]
    assert count == 1


def test_same_event_id_with_changed_payload_fails_closed(tmp_path, monkeypatch):
    connector, _engine, token = _prepare(tmp_path, monkeypatch)
    first = connector.ConnectorEvent(
        event_id="event-00000002", event_type="like", payload={"username": "viewer", "likes": 10}
    )
    changed = connector.ConnectorEvent(
        event_id="event-00000002", event_type="like", payload={"username": "viewer", "likes": 1000}
    )
    connector.connector_ingest(token, first)
    with pytest.raises(HTTPException) as exc:
        connector.connector_ingest(token, changed)
    assert exc.value.status_code == 409


def test_failed_processing_can_retry_same_event_id_without_permanent_false_duplicate(tmp_path, monkeypatch):
    connector, _engine, token = _prepare(tmp_path, monkeypatch)
    body = connector.ConnectorEvent(
        event_id="event-00000003", event_type="share", payload={"username": "viewer", "shares": 1}
    )
    calls = {"count": 0}

    def flaky(user_id, event_type, payload, *, synthetic=False):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic failure")
        return {"event_type": event_type, "user_id": user_id}

    monkeypatch.setattr(connector, "process_overlay_event", flaky)
    with pytest.raises(HTTPException) as exc:
        connector.connector_ingest(token, body)
    assert exc.value.status_code == 503
    result = connector.connector_ingest(token, body)
    assert b'"accepted":true' in result.body
    assert b'"duplicate":false' in result.body
    with connector._connect() as con:
        receipt = con.execute(
            "SELECT state,last_error_code FROM live_overlay_connector_receipts WHERE user_id='u1' AND event_id='event-00000003'"
        ).fetchone()
    assert receipt["state"] == "processed"
    assert receipt["last_error_code"] is None


def test_receipts_store_digest_not_raw_event_payload(tmp_path, monkeypatch):
    connector, _engine, token = _prepare(tmp_path, monkeypatch)
    body = connector.ConnectorEvent(
        event_id="event-00000004",
        event_type="comment",
        payload={"username": "viewer", "message": "bounded message"},
    )
    connector.connector_ingest(token, body)
    with connector._connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(live_overlay_connector_receipts)").fetchall()}
        row = con.execute(
            "SELECT payload_sha256 FROM live_overlay_connector_receipts WHERE user_id='u1' AND event_id='event-00000004'"
        ).fetchone()
    assert "payload_json" not in columns
    assert len(row["payload_sha256"]) == 64


def test_payload_contract_rejects_nested_or_oversized_values():
    from aura_music_studio import aura_live_overlay_connector as connector

    with pytest.raises(HTTPException) as nested:
        connector._validated_payload({"message": {"nested": "not normalized"}})
    assert nested.value.status_code == 422
    with pytest.raises(HTTPException) as oversized:
        connector._validated_payload({"message": "x" * 2001})
    assert oversized.value.status_code == 413


def test_sqlite_rate_limit_is_shared_and_bounded(tmp_path, monkeypatch):
    connector, _engine, token = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(connector, "MAX_EVENTS_PER_MINUTE", 2)
    token_hash = connector._hash(token)
    connector._rate_limit(token_hash)
    connector._rate_limit(token_hash)
    with pytest.raises(HTTPException) as exc:
        connector._rate_limit(token_hash)
    assert exc.value.status_code == 429


def test_bad_or_disabled_token_fails_closed(tmp_path, monkeypatch):
    connector, _engine, token = _prepare(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as bad:
        connector._resolve("not-a-valid-relay-token")
    assert bad.value.status_code == 404
    with connector._connect() as con:
        con.execute("UPDATE live_overlay_connectors SET enabled=0 WHERE user_id='u1'")
    with pytest.raises(HTTPException) as disabled:
        connector._resolve(token)
    assert disabled.value.status_code == 404
