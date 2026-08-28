import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_realtime_voice as realtime
from aura_music_studio.aura_realtime_voice_ui import REALTIME_VOICE_SCRIPT


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


def _ledger_db(path):
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE aura_chat_threads (
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            CREATE TABLE aura_chat_messages (
                id TEXT PRIMARY KEY,thread_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL
            );
            INSERT INTO aura_chat_threads(id,user_id,title,created_at,updated_at)
            VALUES('t1','u1','New conversation','now','now');
            """
        )
    return realtime.RealtimeTranscriptLedger(path)


def test_transcript_ledger_is_tenant_bound_idempotent_and_role_bounded(tmp_path):
    ledger = _ledger_db(tmp_path / "sync.sqlite3")
    session = ledger.issue("u1", "t1", now=100)
    first = ledger.append("u1", "t1", session, "evt-1", "user", "hello Aura", now=101)
    duplicate = ledger.append("u1", "t1", session, "evt-1", "user", "hello Aura", now=102)
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["message_id"] == first["message_id"]
    with pytest.raises(PermissionError):
        ledger.append("u2", "t1", session, "evt-2", "assistant", "no", now=102)
    with pytest.raises(ValueError):
        ledger.append("u1", "t1", session, "evt-3", "system", "no", now=102)
    with sqlite3.connect(tmp_path / "sync.sqlite3") as con:
        rows = con.execute("SELECT role,content FROM aura_chat_messages ORDER BY created_at").fetchall()
    assert rows == [("user", "hello Aura")]


def test_transcript_ledger_rejects_expired_session(tmp_path):
    ledger = _ledger_db(tmp_path / "sync.sqlite3")
    session = ledger.issue("u1", "t1", now=100)
    with pytest.raises(PermissionError, match="invalid or expired"):
        ledger.append("u1", "t1", session, "evt-1", "user", "late", now=100 + 3601)


def test_transcript_endpoint_derives_role_from_whitelisted_provider_event(monkeypatch):
    monkeypatch.setattr(realtime.chat_store, "thread", lambda user_id, thread_id: {"id": thread_id} if user_id == "u1" else None)
    captured = {}

    def fake_append(user_id, thread_id, session_id, event_id, role, content):
        captured.update(locals())
        return {"message_id": "m1", "role": role, "duplicate": False}

    monkeypatch.setattr(realtime.transcript_ledger, "append", fake_append)
    app = FastAPI()
    app.include_router(realtime.router)
    app.add_middleware(_MemberMiddleware)
    client = TestClient(app)
    response = client.post(
        "/aura-intelligence/api/threads/t1/realtime-transcript",
        headers={"Origin": "http://testserver"},
        json={
            "transcript_session_id": "session-123",
            "event_id": "evt-1",
            "event_type": "response.output_audio_transcript.done",
            "transcript": "Hello from Aura",
        },
    )
    assert response.status_code == 200
    assert captured["user_id"] == "u1"
    assert captured["thread_id"] == "t1"
    assert captured["role"] == "assistant"
    assert response.json()["grants_esp_role_or_permission"] is False
    assert response.json()["alters_billing_or_membership"] is False


def test_transcript_endpoint_rejects_unknown_event_and_cross_origin(monkeypatch):
    monkeypatch.setattr(realtime.chat_store, "thread", lambda user_id, thread_id: {"id": thread_id})
    app = FastAPI()
    app.include_router(realtime.router)
    app.add_middleware(_MemberMiddleware)
    client = TestClient(app)
    payload = {
        "transcript_session_id": "session-123",
        "event_id": "evt-1",
        "event_type": "session.update",
        "transcript": "forged",
    }
    assert client.post("/aura-intelligence/api/threads/t1/realtime-transcript", json=payload).status_code == 403
    response = client.post(
        "/aura-intelligence/api/threads/t1/realtime-transcript",
        headers={"Origin": "http://testserver"},
        json=payload,
    )
    assert response.status_code == 400


def test_browser_sync_only_posts_completed_transcripts_to_same_origin_api():
    script = REALTIME_VOICE_SCRIPT
    assert "conversation.item.input_audio_transcription.completed" in script
    assert "response.output_audio_transcript.done" in script
    assert "/realtime-transcript" in script
    assert "transcript_session_id" in script
    assert "credentials:'same-origin'" in script
    assert "session.update" not in script
    assert "conversation.item.create" not in script
