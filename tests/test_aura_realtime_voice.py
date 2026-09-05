from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import aura_music_studio.aura_realtime_voice as realtime


class _Plan:
    id = "pro"

    def has(self, feature: str) -> bool:
        return True


class _MemberMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["member"] = SimpleNamespace(user_id="u1", plan=_Plan())
        await self.app(scope, receive, send)


def test_gate_enforces_rolling_mint_limit_and_cancel(tmp_path):
    gate = realtime.RealtimeSessionGate(tmp_path / "realtime.sqlite3")
    ids = [gate.reserve("u1", now=1000) for _ in range(6)]
    with pytest.raises(PermissionError, match="session limit"):
        gate.reserve("u1", now=1000)
    gate.cancel(ids[-1])
    assert gate.reserve("u1", now=1000)


def test_diagnostics_never_claims_tools_roles_or_billing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-secret")
    monkeypatch.setenv("AURA_REALTIME_VOICE", "configured-voice")
    info = realtime.diagnostics()
    assert info["configured"] is True
    assert info["server_api_key_exposed"] is False
    assert info["tools_granted"] is False
    assert info["grants_esp_role_or_permission"] is False
    assert info["alters_billing_or_membership"] is False


def test_client_secret_requires_same_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(realtime, "gate", realtime.RealtimeSessionGate(tmp_path / "gate.sqlite3"))
    monkeypatch.setattr(realtime.chat_store, "thread", lambda user_id, thread_id: {"id": thread_id})
    app = FastAPI()
    app.include_router(realtime.router)
    app.add_middleware(_MemberMiddleware)
    client = TestClient(app)
    response = client.post("/aura-intelligence/api/threads/t1/realtime-client-secret")
    assert response.status_code == 403


def test_client_secret_is_bounded_and_server_key_is_not_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-secret")
    monkeypatch.setenv("AURA_REALTIME_VOICE", "configured-voice")
    monkeypatch.setenv("AURA_REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.setattr(realtime, "gate", realtime.RealtimeSessionGate(tmp_path / "gate.sqlite3"))
    monkeypatch.setattr(realtime.chat_store, "thread", lambda user_id, thread_id: {"id": thread_id})
    captured = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"client_secret": {"value": "ephemeral-value", "expires_at": 2000}}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(realtime.requests, "post", fake_post)
    app = FastAPI()
    app.include_router(realtime.router)
    app.add_middleware(_MemberMiddleware)
    client = TestClient(app)
    response = client.post(
        "/aura-intelligence/api/threads/t1/realtime-client-secret",
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["client_secret"] == "ephemeral-value"
    assert "server-secret" not in str(body)
    assert body["tools_granted"] is False
    assert captured["json"]["session"]["tools"] == []
    assert captured["headers"]["Authorization"] == "Bearer server-secret"


def test_provider_failure_rolls_back_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-secret")
    monkeypatch.setenv("AURA_REALTIME_VOICE", "configured-voice")
    gate = realtime.RealtimeSessionGate(tmp_path / "gate.sqlite3")
    monkeypatch.setattr(realtime, "gate", gate)
    monkeypatch.setattr(realtime.chat_store, "thread", lambda user_id, thread_id: {"id": thread_id})

    def fail(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(realtime.requests, "post", fail)
    app = FastAPI()
    app.include_router(realtime.router)
    app.add_middleware(_MemberMiddleware)
    client = TestClient(app)
    response = client.post(
        "/aura-intelligence/api/threads/t1/realtime-client-secret",
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 503
    with gate._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM aura_realtime_session_mints").fetchone()[0] == 0
