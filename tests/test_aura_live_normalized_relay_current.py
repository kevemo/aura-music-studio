from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _prepare(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "live-relay.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._rule_last_fired.clear()
    engine._init_schema()
    return engine


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _install_connector(engine, token: str = "relay-token-for-tests-000000000000000000") -> str:
    now = engine._now()
    with engine._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at) VALUES(?,?,1,?,?,?)",
            ("u1", engine._hash_relay_token(token), "test relay", now, now),
        )
    return token


def _relay_request(token: str):
    return SimpleNamespace(headers={"Authorization": f"Bearer {token}"})


def _member_request(user_id: str = "u1"):
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(user_id=user_id, plan=SimpleNamespace(name="Pro")))
    )


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_source_event_dedup_is_atomic_with_stats_goals_and_event_log(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    now = engine._now()
    with engine._connect() as con:
        con.execute(
            """INSERT INTO live_overlay_goals(id,user_id,name,metric,target,current,reset_mode,enabled,created_at,updated_at)
               VALUES('g1','u1','Gift goal','gifts',10,0,'manual',1,?,?)""",
            (now, now),
        )

    payload = {"username": "viewer", "gift_name": "Rose", "gift_count": 2, "coins": 10}
    digest = _digest(payload)
    first = engine.process_overlay_event(
        "u1",
        "gift",
        payload,
        source=engine.CONNECTOR_SOURCE,
        source_event_id="provider-event-0001",
        source_payload_sha256=digest,
    )
    second = engine.process_overlay_event(
        "u1",
        "gift",
        payload,
        source=engine.CONNECTOR_SOURCE,
        source_event_id="provider-event-0001",
        source_payload_sha256=digest,
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["source_deduplicated"] is True
    with engine._connect() as con:
        event_count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='u1' AND event_type='gift'"
        ).fetchone()[0]
        stats = con.execute(
            "SELECT gift_count,gift_value FROM live_overlay_session_stats WHERE user_id='u1' AND username='viewer'"
        ).fetchone()
        goal = con.execute("SELECT current FROM live_overlay_goals WHERE id='g1'").fetchone()
        dedup_count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_source_event_dedup WHERE source=? AND user_id='u1'",
            (engine.CONNECTOR_SOURCE,),
        ).fetchone()[0]
    assert event_count == 1
    assert stats["gift_count"] == 2
    assert stats["gift_value"] == 10
    assert goal["current"] == 2
    assert dedup_count == 1


def test_source_event_id_conflict_fails_closed_without_second_side_effect(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    first_payload = {"username": "viewer", "likes": 10}
    engine.process_overlay_event(
        "u1",
        "like",
        first_payload,
        source=engine.CONNECTOR_SOURCE,
        source_event_id="provider-event-0002",
        source_payload_sha256=_digest(first_payload),
    )

    changed = {"username": "viewer", "likes": 1000}
    with pytest.raises(ValueError, match="different event data"):
        engine.process_overlay_event(
            "u1",
            "like",
            changed,
            source=engine.CONNECTOR_SOURCE,
            source_event_id="provider-event-0002",
            source_payload_sha256=_digest(changed),
        )

    with engine._connect() as con:
        stats = con.execute(
            "SELECT likes FROM live_overlay_session_stats WHERE user_id='u1' AND username='viewer'"
        ).fetchone()
        count = con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='u1' AND event_type='like'").fetchone()[0]
    assert stats["likes"] == 10
    assert count == 1


def test_connector_applies_provider_event_once_and_receipt_is_idempotent(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    token = _install_connector(engine)
    monkeypatch.setattr(engine, "_require_active_member", lambda user_id: {"id": user_id, "status": "active"})
    body = engine.ConnectorEvent(
        event_id="provider-event-0003",
        event_type="gift",
        payload={"username": "Laura", "gift_name": "Rose", "gift_count": 1, "coins": 1},
    )

    first = _json(engine.connector_ingest(body, _relay_request(token)))
    second = _json(engine.connector_ingest(body, _relay_request(token)))

    assert first["accepted"] is True
    assert first["duplicate"] is False
    assert second["accepted"] is True
    assert second["duplicate"] is True
    with engine._connect() as con:
        stats = con.execute(
            "SELECT gift_count,gift_value FROM live_overlay_session_stats WHERE user_id='u1' AND username='Laura'"
        ).fetchone()
        receipt = con.execute(
            "SELECT state FROM live_overlay_connector_receipts WHERE user_id='u1' AND event_id='provider-event-0003'"
        ).fetchone()
    assert stats["gift_count"] == 1
    assert stats["gift_value"] == 1
    assert receipt["state"] == "processed"


def test_committed_engine_event_recovers_after_worker_dies_before_receipt_ack(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    token = _install_connector(engine)
    monkeypatch.setattr(engine, "_require_active_member", lambda user_id: {"id": user_id, "status": "active"})
    payload = {"username": "viewer", "shares": 1}
    digest = _digest(payload)

    engine.process_overlay_event(
        "u1",
        "share",
        payload,
        source=engine.CONNECTOR_SOURCE,
        source_event_id="provider-event-0004",
        source_payload_sha256=digest,
    )
    stale = (datetime.now(timezone.utc) - timedelta(seconds=engine.PROCESSING_LEASE_SECONDS + 5)).isoformat()
    with engine._connect() as con:
        con.execute(
            """INSERT INTO live_overlay_connector_receipts(
                   user_id,event_id,event_type,payload_sha256,state,received_at,processed_at,last_error_code
               ) VALUES('u1','provider-event-0004','share',?,'processing',?,NULL,NULL)""",
            (digest, stale),
        )

    body = engine.ConnectorEvent(event_id="provider-event-0004", event_type="share", payload=payload)
    recovered = _json(engine.connector_ingest(body, _relay_request(token)))

    assert recovered["accepted"] is True
    assert recovered["duplicate"] is True
    assert recovered["recovered_retry"] is True
    with engine._connect() as con:
        stats = con.execute(
            "SELECT shares FROM live_overlay_session_stats WHERE user_id='u1' AND username='viewer'"
        ).fetchone()
        event_count = con.execute(
            "SELECT COUNT(*) FROM live_overlay_events WHERE user_id='u1' AND event_type='share'"
        ).fetchone()[0]
        receipt = con.execute(
            "SELECT state FROM live_overlay_connector_receipts WHERE user_id='u1' AND event_id='provider-event-0004'"
        ).fetchone()
    assert stats["shares"] == 1
    assert event_count == 1
    assert receipt["state"] == "processed"


def test_nonstale_processing_receipt_returns_retryable_conflict_without_reprocessing(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    token = _install_connector(engine)
    monkeypatch.setattr(engine, "_require_active_member", lambda user_id: {"id": user_id, "status": "active"})
    payload = {"username": "viewer", "message": "hello"}
    digest = _digest(payload)
    with engine._connect() as con:
        con.execute(
            """INSERT INTO live_overlay_connector_receipts(
                   user_id,event_id,event_type,payload_sha256,state,received_at,processed_at,last_error_code
               ) VALUES('u1','provider-event-0005','comment',?,'processing',?,NULL,NULL)""",
            (digest, engine._now()),
        )

    body = engine.ConnectorEvent(event_id="provider-event-0005", event_type="comment", payload=payload)
    response = engine.connector_ingest(body, _relay_request(token))
    data = _json(response)

    assert response.status_code == 409
    assert data["retryable"] is True
    assert response.headers["retry-after"] == str(engine.PROCESSING_LEASE_SECONDS)
    with engine._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='u1'").fetchone()[0] == 0


def test_failed_processing_marks_failed_and_same_event_id_can_retry(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    token = _install_connector(engine)
    monkeypatch.setattr(engine, "_require_active_member", lambda user_id: {"id": user_id, "status": "active"})
    original = engine.process_overlay_event
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic engine failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "process_overlay_event", flaky)
    body = engine.ConnectorEvent(
        event_id="provider-event-0006",
        event_type="follow",
        payload={"username": "viewer"},
    )
    with pytest.raises(HTTPException) as first:
        engine.connector_ingest(body, _relay_request(token))
    assert first.value.status_code == 503
    with engine._connect() as con:
        failed = con.execute(
            "SELECT state,last_error_code FROM live_overlay_connector_receipts WHERE user_id='u1' AND event_id='provider-event-0006'"
        ).fetchone()
    assert failed["state"] == "failed"
    assert failed["last_error_code"] == "RuntimeError"

    second = _json(engine.connector_ingest(body, _relay_request(token)))
    assert second["accepted"] is True
    assert second["duplicate"] is False
    with engine._connect() as con:
        stats = con.execute(
            "SELECT follows FROM live_overlay_session_stats WHERE user_id='u1' AND username='viewer'"
        ).fetchone()
    assert stats["follows"] == 1


def test_connector_revalidates_current_membership_before_any_event_mutation(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    token = _install_connector(engine)

    def inactive(_user_id):
        raise HTTPException(403, "Active membership is required for LIVE relay processing")

    monkeypatch.setattr(engine, "_require_active_member", inactive)
    body = engine.ConnectorEvent(
        event_id="provider-event-0007",
        event_type="like",
        payload={"username": "viewer", "likes": 1},
    )
    with pytest.raises(HTTPException) as exc:
        engine.connector_ingest(body, _relay_request(token))
    assert exc.value.status_code == 403
    with engine._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_overlay_connector_receipts").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM live_overlay_events").fetchone()[0] == 0


def test_relay_uses_header_secret_stores_only_digest_and_rotation_invalidates_old_token(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    request = _member_request()
    first = _json(engine.rotate_connector(engine.ConnectorRotate(label="Primary relay"), request))
    raw_one = first["relay_token"]
    assert first["ingest_path"] == "/live-overlay/source/relay/events"
    assert "token" not in first["ingest_path"]
    assert first["token_returned_once"] is True
    with engine._connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(live_overlay_connectors)").fetchall()}
        stored = con.execute("SELECT token_hash FROM live_overlay_connectors WHERE user_id='u1'").fetchone()[0]
    assert "token" not in columns
    assert stored != raw_one
    assert stored == engine._hash_relay_token(raw_one)

    second = _json(engine.rotate_connector(engine.ConnectorRotate(label="Primary relay"), request))
    raw_two = second["relay_token"]
    assert raw_two != raw_one
    with pytest.raises(HTTPException) as old:
        engine._resolve_connector(raw_one)
    assert old.value.status_code == 401
    assert engine._resolve_connector(raw_two)["user_id"] == "u1"


def test_relay_requires_bearer_header_and_rejects_invalid_secret(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    _install_connector(engine)
    with pytest.raises(HTTPException) as missing:
        engine._relay_token(SimpleNamespace(headers={}))
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as invalid:
        engine._resolve_connector("x" * 40)
    assert invalid.value.status_code == 401


def test_normalized_payload_contract_rejects_unknown_nested_negative_nan_and_oversized_values(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as unknown:
        engine._validated_connector_payload({"unknown_provider_field": "x"})
    assert unknown.value.status_code == 422
    with pytest.raises(HTTPException) as nested:
        engine._validated_connector_payload({"message": {"nested": "no"}})
    assert nested.value.status_code == 422
    with pytest.raises(HTTPException) as negative:
        engine._validated_connector_payload({"coins": -1})
    assert negative.value.status_code == 422
    with pytest.raises(HTTPException) as nan:
        engine._validated_connector_payload({"progress": float("nan")})
    assert nan.value.status_code == 422
    with pytest.raises(HTTPException) as oversized:
        engine._validated_connector_payload({"message": "x" * 501})
    assert oversized.value.status_code == 413


def test_sqlite_relay_rate_limit_is_shared_and_bounded(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "MAX_EVENTS_PER_MINUTE", 2)
    token_hash = engine._hash_relay_token("rate-limit-token")
    engine._rate_limit(token_hash)
    engine._rate_limit(token_hash)
    with pytest.raises(HTTPException) as exc:
        engine._rate_limit(token_hash)
    assert exc.value.status_code == 429


def test_connector_status_and_ui_are_truthful_and_do_not_reveal_secret(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    token = _install_connector(engine)
    request = _member_request()
    status = _json(engine.connector_status(request))
    assert status["configured"] is True
    assert status["provider_connected"] is False
    assert status["direct_tiktok_connection_claimed"] is False
    assert status["provider_write_authority"] is False
    assert status["authorization_scheme"] == "Bearer"
    assert token not in json.dumps(status)

    page = engine.connector_setup(request)
    html = page.body.decode("utf-8")
    assert "does not claim TikTok" in html
    assert "external dependency" in html.lower()
    assert "Authorization: Bearer" in html
    assert "/live-overlay-studio" in html
    assert page.headers["cache-control"] == "private, no-store"


def test_event_contract_reports_real_internal_relay_but_no_provider_authority(tmp_path, monkeypatch):
    engine = _prepare(tmp_path, monkeypatch)
    contract = engine.event_contract(_member_request())
    assert contract["schema_version"] == 2
    assert contract["normalized_relay_available"] is True
    assert contract["provider_connected"] is False
    assert contract["provider_write_authority"] is False
    assert contract["relay_authentication"] == "Bearer token returned only at rotation"
    assert "shell_commands" in contract and contract["shell_commands"] is False
