from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class RequestStub:
    def __init__(self, *, member=None, authorization: str | None = None):
        self.state = SimpleNamespace(member=member)
        self.headers = {}
        if authorization:
            self.headers["Authorization"] = authorization


def _configure(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "relay.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
    monkeypatch.setattr(engine, "DB_PATH", db)
    connector._init_schema()
    engine._init_schema()
    return connector, engine


def _seed(connector, user_id="creator-1", token="relay-secret-token-for-tests-0000000001"):
    now = connector._now()
    with connector._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at) VALUES(?,?,1,?,?,?)",
            (user_id, connector._hash(token), "test relay", now, now),
        )
    return token


def _event(connector, event_id, *, provider="provider-a", session_id="session-0001", event_type="gift", payload=None, occurred_at=None):
    return connector.ConnectorEvent(
        provider=provider,
        session_id=session_id,
        event_id=event_id,
        event_type=event_type,
        payload=payload or {},
        occurred_at=occurred_at,
    )


def _json(response):
    return json.loads(response.body)


def test_connector_routes_mount_once_and_reuse_existing_source_boundary():
    from aura_music_studio import access_control, api as api_mod
    from aura_music_studio import aura_live_overlay_engine as engine

    engine_paths = [getattr(r, "path", "") for r in engine.router.routes]
    app_paths = [getattr(r, "path", "") for r in api_mod.app.routes]
    for path in (
        "/live-overlay-studio/provider-relay",
        "/api/live-overlays/connector",
        "/api/live-overlays/connector/rotate",
        "/api/live-overlays/connector/disable",
        "/live-overlay/source/relay/ingest",
    ):
        assert engine_paths.count(path) == 1
        assert app_paths.count(path) == 1
    assert "/live-overlay/source/" in access_control.PUBLIC_PREFIXES
    assert "/live-overlay/connector" not in access_control.PUBLIC_PREFIXES


def test_rotate_hashes_secret_and_rotation_then_disable_fail_closed(tmp_path, monkeypatch):
    connector, _ = _configure(tmp_path, monkeypatch)
    member = SimpleNamespace(user_id="creator-1")
    request = RequestStub(member=member)
    first = _json(connector.rotate_connector(connector.ConnectorRotate(), request))
    first_token = first["token"]
    assert first["token_in_url"] is False
    assert first["authorization_scheme"] == "Bearer"
    assert first["ingest_path"] == "/live-overlay/source/relay/ingest"
    assert first_token not in first["ingest_path"]
    with connector._connect() as con:
        stored = con.execute("SELECT token_hash FROM live_overlay_connectors WHERE user_id='creator-1'").fetchone()[0]
    assert stored == connector._hash(first_token)
    assert stored != first_token

    second_token = _json(connector.rotate_connector(connector.ConnectorRotate(), request))["token"]
    with pytest.raises(HTTPException) as old:
        connector.connector_ingest(_event(connector, "event-old-0001"), RequestStub(authorization=f"Bearer {first_token}"))
    assert old.value.status_code == 404
    assert connector.connector_ingest(
        _event(connector, "event-new-0001"), RequestStub(authorization=f"Bearer {second_token}")
    ).status_code == 200
    connector.disable_connector(request)
    with pytest.raises(HTTPException) as disabled:
        connector.connector_ingest(_event(connector, "event-disabled"), RequestStub(authorization=f"Bearer {second_token}"))
    assert disabled.value.status_code == 404


def test_ingest_requires_bearer_and_token_binds_creator_tenant(tmp_path, monkeypatch):
    connector, engine = _configure(tmp_path, monkeypatch)
    body = _event(connector, "event-auth-0001")
    with pytest.raises(HTTPException) as missing:
        connector.connector_ingest(body, RequestStub())
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as unknown:
        connector.connector_ingest(body, RequestStub(authorization="Bearer " + "x" * 40))
    assert unknown.value.status_code == 404

    token1 = _seed(connector)
    token2 = _seed(connector, user_id="creator-2", token="relay-secret-token-for-tests-0000000002")
    connector.connector_ingest(_event(connector, "same-event-id"), RequestStub(authorization=f"Bearer {token1}"))
    connector.connector_ingest(_event(connector, "same-event-id"), RequestStub(authorization=f"Bearer {token2}"))
    with engine._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-2'").fetchone()[0] == 1


def test_provider_session_event_identity_is_idempotent_and_bound_to_exact_data(tmp_path, monkeypatch):
    connector, engine = _configure(tmp_path, monkeypatch)
    token = _seed(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(
        connector,
        "event-bound-0001",
        payload={"username": "Laura", "gift_name": "Rose", "coins": 1},
        occurred_at="2026-08-29T23:00:00+00:00",
    )
    first = _json(connector.connector_ingest(body, request))
    duplicate = _json(connector.connector_ingest(body, request))
    assert first["duplicate"] is False
    assert first["trust_level"] == "authenticated_normalized_relay"
    assert duplicate["duplicate"] is True
    assert duplicate["state"] == "completed"
    with engine._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'").fetchone()[0] == 1
        payload = json.loads(con.execute("SELECT payload_json FROM live_overlay_events WHERE user_id='creator-1'").fetchone()[0])
    assert payload["source"] == "provider-a"

    with pytest.raises(HTTPException) as changed_payload:
        connector.connector_ingest(
            _event(connector, "event-bound-0001", payload={"username": "Laura", "gift_name": "Rose", "coins": 2}, occurred_at="2026-08-29T23:00:00+00:00"),
            request,
        )
    assert changed_payload.value.status_code == 409
    with pytest.raises(HTTPException) as changed_time:
        connector.connector_ingest(
            _event(connector, "event-bound-0001", payload={"username": "Laura", "gift_name": "Rose", "coins": 1}, occurred_at="2026-08-29T23:01:00+00:00"),
            request,
        )
    assert changed_time.value.status_code == 409

    assert connector.connector_ingest(
        _event(connector, "event-bound-0001", provider="provider-b", payload={"username": "Other", "coins": 1}), request
    ).status_code == 200
    assert connector.connector_ingest(
        _event(connector, "event-bound-0001", session_id="session-0002", payload={"username": "Other", "coins": 1}), request
    ).status_code == 200


def test_failed_engine_attempt_is_audited_and_safe_to_retry(tmp_path, monkeypatch):
    connector, engine = _configure(tmp_path, monkeypatch)
    token = _seed(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(connector, "event-retry-0001", payload={"username": "Dave", "coins": 5})

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(connector, "process_overlay_event", fail)
    with pytest.raises(HTTPException) as failed:
        connector.connector_ingest(body, request)
    assert failed.value.status_code == 503
    with connector._connect() as con:
        receipt = con.execute("SELECT state,last_error_code,payload_sha256 FROM live_overlay_connector_receipts WHERE event_id='event-retry-0001'").fetchone()
    assert receipt["state"] == "failed"
    assert receipt["last_error_code"] == "RuntimeError"
    assert len(receipt["payload_sha256"]) == 64

    with pytest.raises(HTTPException) as conflict:
        connector.connector_ingest(_event(connector, "event-retry-0001", payload={"username": "Dave", "coins": 6}), request)
    assert conflict.value.status_code == 409

    monkeypatch.setattr(connector, "process_overlay_event", engine.process_overlay_event)
    assert connector.connector_ingest(body, request).status_code == 200
    with connector._connect() as con:
        assert con.execute("SELECT state FROM live_overlay_connector_receipts WHERE event_id='event-retry-0001'").fetchone()[0] == "completed"


def test_post_application_completion_failure_blocks_replay(tmp_path, monkeypatch):
    connector, engine = _configure(tmp_path, monkeypatch)
    token = _seed(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(connector, "event-complete-fail", payload={"username": "Tuckz", "coins": 1})

    def fail_completion(*args, **kwargs):
        raise RuntimeError("completion failure")

    monkeypatch.setattr(connector, "_complete_event", fail_completion)
    with pytest.raises(RuntimeError):
        connector.connector_ingest(body, request)
    with engine._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'").fetchone()[0] == 1
    with connector._connect() as con:
        assert con.execute("SELECT state FROM live_overlay_connector_receipts WHERE event_id='event-complete-fail'").fetchone()[0] == "processing"
    retry = connector.connector_ingest(body, request)
    assert retry.status_code == 409
    with engine._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'").fetchone()[0] == 1


def test_payload_validation_rate_limit_and_timestamp_fail_closed(tmp_path, monkeypatch):
    connector, _ = _configure(tmp_path, monkeypatch)
    token = _seed(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    with pytest.raises(HTTPException) as unsupported:
        connector.connector_ingest(_event(connector, "event-unknown1", event_type="unsupported"), request)
    assert unsupported.value.status_code == 400
    with pytest.raises(HTTPException) as nested:
        connector.connector_ingest(_event(connector, "event-nested01", payload={"username": {"bad": "nested"}}), request)
    assert nested.value.status_code == 422
    with pytest.raises(HTTPException) as reserved:
        connector.connector_ingest(_event(connector, "event-reserve1", payload={"source": "fake"}), request)
    assert reserved.value.status_code == 422
    with pytest.raises(HTTPException) as nonfinite:
        connector.connector_ingest(_event(connector, "event-nan-0001", payload={"coins": float("nan")}), request)
    assert nonfinite.value.status_code == 422
    with pytest.raises(HTTPException) as naive:
        connector.connector_ingest(_event(connector, "event-time-001", occurred_at=datetime(2026, 8, 29, 23, 0, 0)), request)
    assert naive.value.status_code == 400

    monkeypatch.setattr(connector, "MAX_EVENTS_PER_MINUTE", 2)
    digest = connector._hash("rate-token")
    connector._rate_limit(digest)
    connector._rate_limit(digest)
    with pytest.raises(HTTPException) as limited:
        connector._rate_limit(digest)
    assert limited.value.status_code == 429


def test_legacy_receipts_migrate_as_consumed_without_replay(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector

    db = tmp_path / "legacy.sqlite3"
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
            INSERT INTO live_overlay_connector_receipts VALUES('creator-1','legacy-event','gift','2026-08-29T00:00:00+00:00');
            """
        )
    connector._init_schema()
    with connector._connect() as con:
        row = con.execute("SELECT provider,session_id,state,payload_sha256,completed_at FROM live_overlay_connector_receipts WHERE event_id='legacy-event'").fetchone()
    assert row["provider"] == "legacy"
    assert row["session_id"] == "legacy"
    assert row["state"] == "completed"
    assert row["payload_sha256"] == connector.ZERO_SHA256
    assert row["completed_at"]
