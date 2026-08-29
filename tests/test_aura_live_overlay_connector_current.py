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


def _event(
    connector,
    event_id: str,
    event_type: str = "gift",
    payload=None,
    *,
    provider="test-provider",
    session_id="session-0001",
    occurred_at=None,
):
    return connector.ConnectorEvent(
        provider=provider,
        session_id=session_id,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload or {},
    )


def _json(response):
    return json.loads(response.body)


def _payload_digest(connector, payload: dict) -> str:
    return connector._validated_payload(payload)[1]


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
    payload = _json(response)
    token = payload["token"]
    assert payload["ingest_path"] == "/live-overlay/source/relay/ingest"
    assert payload["authorization_scheme"] == "Bearer"
    assert payload["token_returned_once"] is True
    assert payload["token_in_url"] is False
    assert payload["schema_version"] == 2
    assert token not in payload["ingest_path"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    with connector._connect() as con:
        row = con.execute("SELECT token_hash FROM live_overlay_connectors WHERE user_id='creator-1'").fetchone()
    assert row["token_hash"] == connector._hash(token)
    assert row["token_hash"] != token


def test_rotation_invalidates_previous_token_and_disable_fails_closed(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    member = SimpleNamespace(user_id="creator-1")
    request = RequestStub(member=member)
    first = _json(connector.rotate_connector(connector.ConnectorRotate(), request))["token"]
    second = _json(connector.rotate_connector(connector.ConnectorRotate(), request))["token"]
    assert first != second
    with pytest.raises(HTTPException) as old:
        connector.connector_ingest(_event(connector, "event-old-token"), RequestStub(authorization=f"Bearer {first}"))
    assert old.value.status_code == 404
    assert connector.connector_ingest(
        _event(connector, "event-new-token"), RequestStub(authorization=f"Bearer {second}")
    ).status_code == 200
    disabled = _json(connector.disable_connector(request))
    assert disabled["disabled"] is True
    assert disabled["provider_moderation_authority_claimed"] is False
    with pytest.raises(HTTPException) as blocked:
        connector.connector_ingest(_event(connector, "event-disabled"), RequestStub(authorization=f"Bearer {second}"))
    assert blocked.value.status_code == 404


def test_ingest_requires_bearer_auth_and_unknown_tokens_fail_closed(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    body = _event(connector, "event-00000001")
    with pytest.raises(HTTPException) as missing:
        connector.connector_ingest(body, RequestStub())
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as malformed:
        connector.connector_ingest(body, RequestStub(authorization="Basic abc"))
    assert malformed.value.status_code == 401
    with pytest.raises(HTTPException) as unknown:
        connector.connector_ingest(body, RequestStub(authorization="Bearer " + "x" * 40))
    assert unknown.value.status_code == 404


def test_normalized_event_is_idempotent_and_updates_engine_once(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(
        connector,
        "event-00000001",
        payload={"username": "Laura", "gift_name": "Rose", "coins": 1},
        occurred_at="2026-08-29T23:00:00+00:00",
    )
    first = _json(connector.connector_ingest(body, request))
    second = _json(connector.connector_ingest(body, request))
    assert first["duplicate"] is False
    assert first["normalized_relay"] is True
    assert first["trust_level"] == "authenticated_normalized_relay"
    assert first["direct_tiktok_connection_claimed"] is False
    assert first["provider_moderation_authority_claimed"] is False
    assert second["duplicate"] is True
    assert second["state"] == "completed"
    with engine._connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0]
        event = con.execute(
            "SELECT payload_json FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()
    with connector._connect() as con:
        receipt = con.execute(
            """
            SELECT state,payload_sha256,provider_timestamp,completed_at,last_error_code
            FROM live_overlay_connector_receipts
            WHERE user_id='creator-1' AND provider='test-provider'
              AND session_id='session-0001' AND event_id='event-00000001'
            """
        ).fetchone()
        connector_row = con.execute(
            "SELECT last_event_at FROM live_overlay_connectors WHERE user_id='creator-1'"
        ).fetchone()
    assert count == 1
    assert json.loads(event["payload_json"])["source"] == "test-provider"
    assert receipt["state"] == "completed"
    assert len(receipt["payload_sha256"]) == 64
    assert receipt["provider_timestamp"].startswith("2026-08-29T23:00:00")
    assert receipt["completed_at"]
    assert receipt["last_error_code"] is None
    assert connector_row["last_event_at"]


def test_provider_and_live_session_are_part_of_deduplication_identity(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    for provider, session in (
        ("provider-a", "session-0001"),
        ("provider-b", "session-0001"),
        ("provider-a", "session-0002"),
    ):
        response = connector.connector_ingest(
            _event(
                connector,
                "shared-event-id",
                payload={"username": provider, "gift_name": "Rose", "coins": 1},
                provider=provider,
                session_id=session,
            ),
            request,
        )
        assert response.status_code == 200
    with engine._connect() as con:
        applied = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0]
    assert applied == 3


def test_token_binding_enforces_creator_tenant_isolation(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token_one = _seed_connector(connector, user_id="creator-1")
    token_two = "relay-secret-token-for-tests-0000000002"
    _seed_connector(connector, token=token_two, user_id="creator-2")
    connector.connector_ingest(
        _event(connector, "tenant-event-01", payload={"username": "One", "coins": 1}),
        RequestStub(authorization=f"Bearer {token_one}"),
    )
    connector.connector_ingest(
        _event(connector, "tenant-event-01", payload={"username": "Two", "coins": 1}),
        RequestStub(authorization=f"Bearer {token_two}"),
    )
    with engine._connect() as con:
        one = con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'").fetchone()[0]
        two = con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-2'").fetchone()[0]
    assert one == 1
    assert two == 1


def test_same_event_identity_cannot_be_reused_for_different_data(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    first = _event(connector, "event-bound-0001", payload={"username": "Laura", "coins": 1})
    connector.connector_ingest(first, request)
    with pytest.raises(HTTPException) as changed_payload:
        connector.connector_ingest(
            _event(connector, "event-bound-0001", payload={"username": "Laura", "coins": 999}),
            request,
        )
    assert changed_payload.value.status_code == 409
    with pytest.raises(HTTPException) as changed_type:
        connector.connector_ingest(
            _event(connector, "event-bound-0001", event_type="follow", payload={"username": "Laura", "coins": 1}),
            request,
        )
    assert changed_type.value.status_code == 409
    with engine._connect() as con:
        applied = con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'").fetchone()[0]
    assert applied == 1


def test_same_event_identity_cannot_change_provider_timestamp(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    connector.connector_ingest(
        _event(connector, "event-time-bind", occurred_at="2026-08-29T23:00:00+00:00"),
        request,
    )
    with pytest.raises(HTTPException) as changed:
        connector.connector_ingest(
            _event(connector, "event-time-bind", occurred_at="2026-08-29T23:01:00+00:00"),
            request,
        )
    assert changed.value.status_code == 409


def test_failed_processing_is_audited_and_same_event_can_retry(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(connector, "event-retry-0001", payload={"username": "Dave", "coins": 5})

    def fail_once(*args, **kwargs):
        raise RuntimeError("synthetic processing failure")

    monkeypatch.setattr(connector, "process_overlay_event", fail_once)
    with pytest.raises(HTTPException) as failed:
        connector.connector_ingest(body, request)
    assert failed.value.status_code == 503
    with connector._connect() as con:
        receipt = con.execute(
            """
            SELECT state,last_error_code,payload_sha256 FROM live_overlay_connector_receipts
            WHERE user_id='creator-1' AND provider='test-provider'
              AND session_id='session-0001' AND event_id='event-retry-0001'
            """
        ).fetchone()
    assert receipt["state"] == "failed"
    assert receipt["last_error_code"] == "RuntimeError"
    assert len(receipt["payload_sha256"]) == 64

    monkeypatch.setattr(connector, "process_overlay_event", engine.process_overlay_event)
    retry = _json(connector.connector_ingest(body, request))
    assert retry["duplicate"] is False
    with connector._connect() as con:
        completed = con.execute(
            """
            SELECT state,last_error_code FROM live_overlay_connector_receipts
            WHERE user_id='creator-1' AND provider='test-provider'
              AND session_id='session-0001' AND event_id='event-retry-0001'
            """
        ).fetchone()
    assert completed["state"] == "completed"
    assert completed["last_error_code"] is None


def test_failed_event_retry_must_match_original_digest(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")

    def fail(*args, **kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(connector, "process_overlay_event", fail)
    original = _event(connector, "failed-bind-01", payload={"username": "Dave", "coins": 5})
    with pytest.raises(HTTPException):
        connector.connector_ingest(original, request)
    with pytest.raises(HTTPException) as conflict:
        connector.connector_ingest(
            _event(connector, "failed-bind-01", payload={"username": "Dave", "coins": 6}),
            request,
        )
    assert conflict.value.status_code == 409


def test_completion_failure_keeps_processing_claim_to_prevent_replay(tmp_path, monkeypatch):
    connector, engine, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(
        connector,
        "event-complete-fail-01",
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
            """
            SELECT state FROM live_overlay_connector_receipts
            WHERE user_id='creator-1' AND provider='test-provider'
              AND session_id='session-0001' AND event_id='event-complete-fail-01'
            """
        ).fetchone()
    assert applied == 1
    assert receipt["state"] == "processing"

    retry = connector.connector_ingest(body, request)
    assert retry.status_code == 409
    assert _json(retry)["retryable"] is True
    with engine._connect() as con:
        applied_after_retry = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0]
    assert applied_after_retry == 1


def test_concurrent_processing_receipt_returns_retryable_conflict(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    now = connector._now()
    digest = _payload_digest(connector, {})
    with connector._connect() as con:
        con.execute(
            """
            INSERT INTO live_overlay_connector_receipts(
                user_id,provider,session_id,event_id,event_type,payload_sha256,state,
                provider_timestamp,received_at,completed_at,last_error_code
            ) VALUES(?,?,?,?,?,?,'processing',NULL,?,NULL,NULL)
            """,
            ("creator-1", "test-provider", "session-0001", "event-processing-01", "gift", digest, now),
        )
    response = connector.connector_ingest(
        _event(connector, "event-processing-01"),
        RequestStub(authorization=f"Bearer {token}"),
    )
    assert response.status_code == 409
    assert response.headers["retry-after"] == "1"
    assert _json(response)["processing"] is True


def test_connector_rejects_unknown_event_and_bounded_payload_violations(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    with pytest.raises(HTTPException) as unsupported:
        connector.connector_ingest(_event(connector, "event-unknown-01", event_type="not-a-live-event"), request)
    assert unsupported.value.status_code == 400

    monkeypatch.setattr(connector, "MAX_PAYLOAD_BYTES", 32)
    with pytest.raises(HTTPException) as oversized:
        connector.connector_ingest(
            _event(connector, "event-large-0001", event_type="comment", payload={"message": "x" * 100}),
            request,
        )
    assert oversized.value.status_code == 413

    monkeypatch.setattr(connector, "MAX_PAYLOAD_BYTES", 32768)
    with pytest.raises(HTTPException) as nested:
        connector.connector_ingest(
            _event(connector, "event-nested-001", payload={"username": {"nested": "not normalized"}}),
            request,
        )
    assert nested.value.status_code == 422

    with pytest.raises(HTTPException) as reserved:
        connector.connector_ingest(
            _event(connector, "event-reserved01", payload={"source": "pretend-provider"}),
            request,
        )
    assert reserved.value.status_code == 422

    with pytest.raises(HTTPException) as nonfinite:
        connector.connector_ingest(
            _event(connector, "event-nan-0001", payload={"coins": float("nan")}),
            request,
        )
    assert nonfinite.value.status_code == 422

    too_many = {f"field{i}": i for i in range(connector.MAX_PAYLOAD_KEYS + 1)}
    with pytest.raises(HTTPException) as many:
        connector.connector_ingest(_event(connector, "event-many-0001", payload=too_many), request)
    assert many.value.status_code == 413


def test_naive_provider_timestamp_is_rejected(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    token = _seed_connector(connector)
    request = RequestStub(authorization=f"Bearer {token}")
    with pytest.raises(HTTPException) as exc:
        connector.connector_ingest(
            _event(connector, "event-time-0001", occurred_at=datetime(2026, 8, 29, 23, 0, 0)),
            request,
        )
    assert exc.value.status_code == 400


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


def test_status_is_private_and_never_claims_provider_authority(tmp_path, monkeypatch):
    connector, _, _ = _configure(tmp_path, monkeypatch)
    response = connector.connector_status(RequestStub(member=SimpleNamespace(user_id="creator-1")))
    payload = _json(response)
    assert payload["schema_version"] == 2
    assert payload["provider_connection_state"] == "external_dependency"
    assert payload["direct_tiktok_connection_claimed"] is False
    assert payload["provider_moderation_authority_claimed"] is False
    assert payload["transport"] == "bearer_token_normalized_relay"
    assert payload["trust_level"] == "authenticated_normalized_relay"
    assert "adapter's responsibility" in payload["trust_note"]
    assert "token" not in json.dumps(payload).lower()
    assert response.headers["cache-control"] == "private, no-store"


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
            """
            SELECT provider,session_id,state,payload_sha256,completed_at
            FROM live_overlay_connector_receipts
            WHERE user_id='creator-1' AND event_id='legacy-event-01'
            """
        ).fetchone()
    assert {"provider", "session_id", "state", "payload_sha256", "provider_timestamp", "completed_at", "last_error_code"}.issubset(columns)
    assert migrated["provider"] == "legacy"
    assert migrated["session_id"] == "legacy"
    assert migrated["state"] == "completed"
    assert migrated["payload_sha256"] == connector.ZERO_SHA256
    assert migrated["completed_at"]


def test_interim_receipt_schema_migrates_all_rows_consumed_without_replay(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector

    db = tmp_path / "interim-relay.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
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
            INSERT INTO live_overlay_connector_receipts(
                user_id,event_id,event_type,payload_sha256,state,received_at,processed_at,last_error_code
            ) VALUES(
                'creator-1','interim-event-01','gift',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'failed','2026-08-29T00:00:00+00:00',NULL,'RuntimeError'
            );
            """
        )
    connector._init_schema()
    with connector._connect() as con:
        migrated = con.execute(
            """
            SELECT provider,session_id,state,payload_sha256,last_error_code
            FROM live_overlay_connector_receipts
            WHERE user_id='creator-1' AND event_id='interim-event-01'
            """
        ).fetchone()
    assert migrated["provider"] == "legacy"
    assert migrated["session_id"] == "legacy"
    assert migrated["state"] == "completed"
    assert migrated["payload_sha256"] == "a" * 64
    assert migrated["last_error_code"] is None
