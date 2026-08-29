from __future__ import annotations

import inspect


def test_connector_router_is_mounted_and_relay_path_uses_token_auth_boundary():
    from aura_music_studio import access_control, api as api_mod
    from aura_music_studio.aura_live_overlay_connector import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/api/live-overlays/connector" in paths
    assert "/api/live-overlays/connector/rotate" in paths
    assert "/api/live-overlays/connector/disable" in paths
    assert "/live-overlay/connector/{token}" in paths
    assert "/live-overlay/connector/" in access_control.PUBLIC_PREFIXES
    assert "app.include_router(aura_live_overlay_connector_router)" in inspect.getsource(api_mod)


def test_connector_credential_is_random_and_stored_as_sha256_only():
    from aura_music_studio import aura_live_overlay_connector as mod

    assert "hashlib.sha256" in inspect.getsource(mod._hash)
    source = inspect.getsource(mod.rotate_connector)
    assert "secrets.token_urlsafe" in source
    assert "token_hash" in source
    assert '"token_returned_once": True' in source


def test_connector_fails_closed_on_event_contract_and_never_claims_direct_tiktok_connection():
    from aura_music_studio import aura_live_overlay_connector as mod

    status_source = inspect.getsource(mod.connector_status)
    assert '"direct_tiktok_connection_claimed": False' in status_source
    ingest_source = inspect.getsource(mod.connector_ingest)
    assert "body.event_type not in EVENT_TYPES" in ingest_source
    assert "process_overlay_event" in ingest_source


def test_connector_event_ids_are_idempotent(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "relay.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
    monkeypatch.setattr(engine, "DB_PATH", db)
    connector._init_schema()
    engine._init_schema()
    token = "relay-secret-token-for-tests"
    now = connector._now()
    with connector._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at) VALUES(?,?,1,?,?,?)",
            ("u1", connector._hash(token), "test relay", now, now),
        )
    body = connector.ConnectorEvent(event_id="event-00000001", event_type="gift", payload={"username": "Laura", "gift_name": "Rose", "coins": 1})
    first = connector.connector_ingest(token, body)
    second = connector.connector_ingest(token, body)
    assert first.body
    assert b'"duplicate":false' in first.body
    assert b'"duplicate":true' in second.body
    with engine._connect() as con:
        count = con.execute("SELECT COUNT(*) FROM live_overlay_events WHERE user_id='u1' AND event_type='gift'").fetchone()[0]
    assert count == 1


def test_connector_rate_limiter_is_bounded(monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as mod

    mod._rate.clear()
    monkeypatch.setattr(mod, "MAX_EVENTS_PER_MINUTE", 2)
    mod._rate_limit("token")
    mod._rate_limit("token")
    try:
        mod._rate_limit("token")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 429
    else:
        raise AssertionError("third event should be rate limited")


def test_voice_profile_route_remains_path_parameter_not_form_field():
    from aura_music_studio import api as api_mod

    sig = inspect.signature(api_mod.new_voice_profile)
    assert sig.parameters["project_name"].default is inspect.Signature.empty
