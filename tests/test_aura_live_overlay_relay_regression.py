from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class RequestStub:
    def __init__(self, *, member=None, authorization: str | None = None):
        self.state = SimpleNamespace(member=member)
        self.headers = {}
        if authorization:
            self.headers["Authorization"] = authorization


def _json(response):
    return json.loads(response.body)


def _member(user_id: str = "creator-1"):
    return SimpleNamespace(user_id=user_id, plan=SimpleNamespace(name="Unlimited Pro"))


def _configure(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "aura-live-relay.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._rule_last_fired.clear()
    engine._init_schema()
    monkeypatch.setattr(engine, "_require_active_member", lambda user_id: {"status": "active", "user_id": user_id})
    return engine


def _rotate(engine, *, user_id: str = "creator-1") -> str:
    response = engine.rotate_connector(
        engine.ConnectorRotate(label="ESP trusted relay"),
        RequestStub(member=_member(user_id)),
    )
    payload = _json(response)
    assert payload["token_returned_once"] is True
    assert payload["provider_connected"] is False
    assert payload["provider_write_authority"] is False
    return payload["relay_token"]


def _event(engine, event_id: str, *, event_type: str = "gift", payload=None):
    return engine.ConnectorEvent(
        event_id=event_id,
        event_type=event_type,
        payload=payload or {"username": "Laura", "gift_name": "Rose", "coins": 1},
    )


def test_relay_routes_are_present_in_production_openapi():
    import app as production_entrypoint

    paths = production_entrypoint.app.openapi()["paths"]
    assert "get" in paths["/api/live-overlays/connector"]
    assert "post" in paths["/api/live-overlays/connector/rotate"]
    assert "post" in paths["/api/live-overlays/connector/disable"]
    assert "/live-overlay/source/relay/events" not in paths


def test_rotation_stores_only_hash_and_old_or_disabled_tokens_fail_closed(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)
    member = _member()
    request = RequestStub(member=member)

    first_token = _rotate(engine)
    with engine._connect() as con:
        stored = con.execute(
            "SELECT token_hash FROM live_overlay_connectors WHERE user_id=?",
            (member.user_id,),
        ).fetchone()[0]
    assert stored == engine._hash_relay_token(first_token)
    assert stored != first_token

    second_token = _rotate(engine)
    with pytest.raises(HTTPException) as old:
        engine.connector_ingest(
            _event(engine, "relay-old-token-0001"),
            RequestStub(authorization=f"Bearer {first_token}"),
        )
    assert old.value.status_code == 401

    assert engine.connector_ingest(
        _event(engine, "relay-new-token-0001"),
        RequestStub(authorization=f"Bearer {second_token}"),
    ).status_code == 200

    engine.disable_connector(request)
    with pytest.raises(HTTPException) as disabled:
        engine.connector_ingest(
            _event(engine, "relay-disabled-0001"),
            RequestStub(authorization=f"Bearer {second_token}"),
        )
    assert disabled.value.status_code == 401


def test_relay_requires_bearer_and_active_membership(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)
    token = _rotate(engine)
    body = _event(engine, "relay-auth-event-0001")

    with pytest.raises(HTTPException) as missing:
        engine.connector_ingest(body, RequestStub())
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as unknown:
        engine.connector_ingest(body, RequestStub(authorization="Bearer " + "x" * 40))
    assert unknown.value.status_code == 401

    def inactive(_user_id: str):
        raise HTTPException(403, "Active membership is required for LIVE relay processing")

    monkeypatch.setattr(engine, "_require_active_member", inactive)
    with pytest.raises(HTTPException) as denied:
        engine.connector_ingest(body, RequestStub(authorization=f"Bearer {token}"))
    assert denied.value.status_code == 403


def test_event_identity_is_idempotent_and_conflicting_reuse_fails_closed(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)
    token = _rotate(engine)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(
        engine,
        "relay-idempotent-0001",
        payload={"username": "Laura", "gift_name": "Rose", "coins": 1},
    )

    first = _json(engine.connector_ingest(body, request))
    duplicate = _json(engine.connector_ingest(body, request))
    assert first["accepted"] is True
    assert first["duplicate"] is False
    assert first["normalized_relay"] is True
    assert first["provider_connected"] is False
    assert duplicate["accepted"] is True
    assert duplicate["duplicate"] is True
    assert duplicate["state"] == "processed"

    with engine._connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1' AND event_type='gift'"
        ).fetchone()[0] == 1

    with pytest.raises(HTTPException) as conflict:
        engine.connector_ingest(
            _event(
                engine,
                "relay-idempotent-0001",
                payload={"username": "Laura", "gift_name": "Rose", "coins": 2},
            ),
            request,
        )
    assert conflict.value.status_code == 409


def test_failed_receipt_retries_safely_and_processing_receipt_blocks_replay(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)
    token = _rotate(engine)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(engine, "relay-retry-event-0001", payload={"username": "Dave", "coins": 5})
    real_process = engine.process_overlay_event

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic relay failure")

    monkeypatch.setattr(engine, "process_overlay_event", fail)
    with pytest.raises(HTTPException) as failed:
        engine.connector_ingest(body, request)
    assert failed.value.status_code == 503
    with engine._connect() as con:
        receipt = con.execute(
            "SELECT state,last_error_code FROM live_overlay_connector_receipts WHERE event_id=?",
            (body.event_id,),
        ).fetchone()
    assert receipt["state"] == "failed"
    assert receipt["last_error_code"] == "RuntimeError"

    monkeypatch.setattr(engine, "process_overlay_event", real_process)
    retried = _json(engine.connector_ingest(body, request))
    assert retried["accepted"] is True
    with engine._connect() as con:
        assert con.execute(
            "SELECT state FROM live_overlay_connector_receipts WHERE event_id=?",
            (body.event_id,),
        ).fetchone()[0] == "processed"
        assert con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'"
        ).fetchone()[0] == 1

    pending = _event(engine, "relay-processing-0001", payload={"username": "Tuckz", "coins": 1})
    _, digest = engine._validated_connector_payload(pending.payload)
    with engine._connect() as con:
        con.execute(
            """INSERT INTO live_overlay_connector_receipts(
                   user_id,event_id,event_type,payload_sha256,state,received_at,processed_at,last_error_code
               ) VALUES(?,?,?,?, 'processing', ?,NULL,NULL)""",
            ("creator-1", pending.event_id, pending.event_type, digest, engine._now()),
        )
    blocked = engine.connector_ingest(pending, request)
    assert blocked.status_code == 409
    blocked_payload = _json(blocked)
    assert blocked_payload["accepted"] is False
    assert blocked_payload["state"] == "processing"
    with engine._connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'"
        ).fetchone()[0] == 1


def test_stale_processing_receipt_recovers_without_duplicate_application(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)
    token = _rotate(engine)
    request = RequestStub(authorization=f"Bearer {token}")
    body = _event(engine, "relay-stale-recovery-0001", payload={"username": "Tuckz", "coins": 1})
    _, digest = engine._validated_connector_payload(body.payload)
    stale = (datetime.now(timezone.utc) - timedelta(seconds=engine.PROCESSING_LEASE_SECONDS + 5)).isoformat()
    with engine._connect() as con:
        con.execute(
            """INSERT INTO live_overlay_connector_receipts(
                   user_id,event_id,event_type,payload_sha256,state,received_at,processed_at,last_error_code
               ) VALUES(?,?,?,?, 'processing', ?,NULL,NULL)""",
            ("creator-1", body.event_id, body.event_type, digest, stale),
        )

    recovered = _json(engine.connector_ingest(body, request))
    assert recovered["accepted"] is True
    with engine._connect() as con:
        assert con.execute(
            "SELECT state FROM live_overlay_connector_receipts WHERE event_id=?",
            (body.event_id,),
        ).fetchone()[0] == "processed"
        assert con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='creator-1'"
        ).fetchone()[0] == 1


def test_payload_validation_rate_limit_and_status_never_expose_secret(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)
    token = _rotate(engine)
    member_request = RequestStub(member=_member())
    relay_request = RequestStub(authorization=f"Bearer {token}")

    status = _json(engine.connector_status(member_request))
    serialized = json.dumps(status).lower()
    assert status["provider_connected"] is False
    assert status["direct_tiktok_connection_claimed"] is False
    assert status["provider_write_authority"] is False
    assert "relay_token" not in serialized
    assert token.lower() not in serialized

    with pytest.raises(HTTPException) as reserved:
        engine.connector_ingest(
            _event(engine, "relay-reserved-field-0001", payload={"source": "fake"}),
            relay_request,
        )
    assert reserved.value.status_code == 422

    with pytest.raises(HTTPException) as nested:
        engine.connector_ingest(
            _event(engine, "relay-nested-field-0001", payload={"username": {"bad": "nested"}}),
            relay_request,
        )
    assert nested.value.status_code == 422

    monkeypatch.setattr(engine, "MAX_EVENTS_PER_MINUTE", 2)
    digest = engine._hash_relay_token("rate-limit-test-token")
    engine._rate_limit(digest)
    engine._rate_limit(digest)
    with pytest.raises(HTTPException) as limited:
        engine._rate_limit(digest)
    assert limited.value.status_code == 429


def test_connector_setup_page_is_truthful_and_private(tmp_path, monkeypatch):
    engine = _configure(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as denied:
        engine.connector_setup(RequestStub())
    assert denied.value.status_code == 401

    response = engine.connector_setup(RequestStub(member=_member()))
    body = response.body.decode("utf-8")
    assert "/api/live-overlays/connector/rotate" in body
    assert "lastEndpoint=location.origin+d.ingest_path" in body
    assert "does not claim TikTok or any other provider is connected" in body
    assert "Provider connected:</b> <span id='provider'>No" in body
    assert "Provider moderation/write authority:</b> None" in body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
