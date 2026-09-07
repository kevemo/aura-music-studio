from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .aura_chat_store import AuraChatStore
from .plans import AURA_SPEECH

router = APIRouter(prefix="/aura-intelligence/api", tags=["Aura Realtime Voice"])

_CLIENT_SECRET_URL = "https://api.openai.com/v1/realtime/client_secrets"
_DEFAULT_MODEL = "gpt-realtime-2.1"
_DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
_WINDOW_SECONDS = 600
_MAX_MINTS_PER_WINDOW = 6
_TRANSCRIPT_SESSION_SECONDS = 3600
_MAX_TRANSCRIPT_CHARS = 12000


def _effective_port(parts) -> int | None:
    if parts.port is not None:
        return parts.port
    if parts.scheme == "https":
        return 443
    if parts.scheme == "http":
        return 80
    return None


def _same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    try:
        supplied = urlsplit(source)
        target = urlsplit(str(request.base_url))
    except ValueError:
        return False
    return (
        supplied.scheme.lower() == target.scheme.lower()
        and (supplied.hostname or "").lower() == (target.hostname or "").lower()
        and _effective_port(supplied) == _effective_port(target)
    )


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not getattr(member, "user_id", None):
        raise HTTPException(401, "Authenticated member identity unavailable")
    if not member.plan.has(AURA_SPEECH):
        raise HTTPException(403, "Aura Realtime Voice is not enabled for this membership")
    return member


class RealtimeSessionGate:
    """Atomic mint-rate gate for short-lived browser Realtime credentials."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS aura_realtime_session_mints (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,created_at INTEGER NOT NULL)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_aura_realtime_mints_user_time ON aura_realtime_session_mints(user_id, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def reserve(self, user_id: str, now: int | None = None) -> str:
        now = int(now if now is not None else time.time())
        cutoff = now - _WINDOW_SECONDS
        reservation_id = uuid4().hex
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM aura_realtime_session_mints WHERE created_at < ?", (cutoff,))
            count = con.execute(
                "SELECT COUNT(*) AS n FROM aura_realtime_session_mints WHERE user_id=? AND created_at>=?",
                (user_id, cutoff),
            ).fetchone()["n"]
            if int(count) >= _MAX_MINTS_PER_WINDOW:
                raise PermissionError("Aura Realtime Voice session limit reached; retry after the current window resets")
            con.execute("INSERT INTO aura_realtime_session_mints(id,user_id,created_at) VALUES(?,?,?)", (reservation_id, user_id, now))
        return reservation_id

    def cancel(self, reservation_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM aura_realtime_session_mints WHERE id=?", (reservation_id,))


class RealtimeTranscriptLedger:
    """Tenant-bound, idempotent bridge from Realtime transcript events to Aura history."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_realtime_transcript_sessions (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,thread_id TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aura_realtime_transcript_events (
                    session_id TEXT NOT NULL,event_id TEXT NOT NULL,message_id TEXT NOT NULL,role TEXT NOT NULL,created_at INTEGER NOT NULL,
                    PRIMARY KEY(session_id,event_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def issue(self, user_id: str, thread_id: str, now: int | None = None) -> str:
        now = int(now if now is not None else time.time())
        session_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_realtime_transcript_sessions(id,user_id,thread_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                (session_id, user_id, thread_id, now, now + _TRANSCRIPT_SESSION_SECONDS),
            )
        return session_id

    def append(self, user_id: str, thread_id: str, session_id: str, event_id: str, role: str, content: str, now: int | None = None) -> dict:
        if role not in {"user", "assistant"}:
            raise ValueError("Unsupported transcript role")
        clean = (content or "").strip()[:_MAX_TRANSCRIPT_CHARS]
        event_id = (event_id or "").strip()[:180]
        if not clean or not event_id:
            raise ValueError("Transcript content and event id are required")
        now = int(now if now is not None else time.time())
        message_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = con.execute(
                "SELECT 1 FROM aura_realtime_transcript_sessions WHERE id=? AND user_id=? AND thread_id=? AND expires_at>=?",
                (session_id, user_id, thread_id, now),
            ).fetchone()
            if not session:
                raise PermissionError("Realtime transcript session is invalid or expired")
            owned = con.execute("SELECT 1 FROM aura_chat_threads WHERE id=? AND user_id=?", (thread_id, user_id)).fetchone()
            if not owned:
                raise KeyError(thread_id)
            existing = con.execute(
                "SELECT message_id,role FROM aura_realtime_transcript_events WHERE session_id=? AND event_id=?",
                (session_id, event_id),
            ).fetchone()
            if existing:
                return {"message_id": existing["message_id"], "role": existing["role"], "duplicate": True}
            con.execute(
                "INSERT INTO aura_chat_messages(id,thread_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (message_id, thread_id, role, clean, created_at),
            )
            con.execute("UPDATE aura_chat_threads SET updated_at=? WHERE id=?", (created_at, thread_id))
            if role == "user":
                count = int(con.execute("SELECT COUNT(*) AS n FROM aura_chat_messages WHERE thread_id=?", (thread_id,)).fetchone()["n"])
                if count == 1:
                    con.execute("UPDATE aura_chat_threads SET title=? WHERE id=?", (" ".join(clean.split())[:90] or "New conversation", thread_id))
            con.execute(
                "INSERT INTO aura_realtime_transcript_events(session_id,event_id,message_id,role,created_at) VALUES(?,?,?,?,?)",
                (session_id, event_id, message_id, role, now),
            )
        return {"message_id": message_id, "role": role, "duplicate": False}


class RealtimeTranscriptEvent(BaseModel):
    transcript_session_id: str = Field(min_length=8, max_length=128)
    event_id: str = Field(min_length=1, max_length=180)
    event_type: str = Field(min_length=1, max_length=120)
    transcript: str = Field(min_length=1, max_length=_MAX_TRANSCRIPT_CHARS)


gate = RealtimeSessionGate()
chat_store = AuraChatStore()
transcript_ledger = RealtimeTranscriptLedger(chat_store.db_path)


def diagnostics() -> dict:
    return {
        "configured": bool(os.getenv("OPENAI_API_KEY") and os.getenv("AURA_REALTIME_VOICE")),
        "model": os.getenv("AURA_REALTIME_MODEL", _DEFAULT_MODEL),
        "transcription_model": os.getenv("AURA_REALTIME_TRANSCRIBE_MODEL", _DEFAULT_TRANSCRIBE_MODEL),
        "voice_configured": bool(os.getenv("AURA_REALTIME_VOICE")),
        "transport": "webrtc_client_secret",
        "thread_transcript_sync": True,
        "server_api_key_exposed": False,
        "tools_granted": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.get("/realtime-voice/status")
def realtime_voice_status(request: Request):
    member = _member(request)
    return {**diagnostics(), "available_to_plan": member.plan.has(AURA_SPEECH)}


@router.post("/threads/{thread_id}/realtime-client-secret")
def create_realtime_client_secret(thread_id: str, request: Request):
    member = _member(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin browser evidence required")
    if not chat_store.thread(str(member.user_id), thread_id):
        raise HTTPException(404, "Aura conversation not found")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    voice = os.getenv("AURA_REALTIME_VOICE", "").strip()
    model = os.getenv("AURA_REALTIME_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    transcription_model = os.getenv("AURA_REALTIME_TRANSCRIBE_MODEL", _DEFAULT_TRANSCRIBE_MODEL).strip() or _DEFAULT_TRANSCRIBE_MODEL
    if not api_key or not voice:
        raise HTTPException(503, "Aura Realtime Voice is not configured on this host")

    try:
        reservation_id = gate.reserve(str(member.user_id))
    except PermissionError as exc:
        raise HTTPException(429, str(exc)) from exc

    payload = {
        "session": {
            "type": "realtime",
            "model": model,
            "audio": {"input": {"transcription": {"model": transcription_model}}, "output": {"voice": voice}},
            "tools": [],
        }
    }
    try:
        response = requests.post(
            _CLIENT_SECRET_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=12,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Realtime provider rejected session creation ({response.status_code})")
        body = response.json()
        secret = body.get("client_secret") if isinstance(body, dict) else None
        if not isinstance(secret, dict):
            secret = body if isinstance(body, dict) else {}
        value = str(secret.get("value") or "")
        expires_at = secret.get("expires_at")
        if not value:
            raise RuntimeError("Realtime provider returned no client secret")
        transcript_session_id = transcript_ledger.issue(str(member.user_id), thread_id)
    except Exception as exc:
        gate.cancel(reservation_id)
        raise HTTPException(503, "Aura Realtime Voice session creation failed") from exc

    return {
        "client_secret": value,
        "expires_at": expires_at,
        "model": model,
        "voice": voice,
        "transport": "webrtc",
        "thread_id": thread_id,
        "transcript_session_id": transcript_session_id,
        "server_api_key_exposed": False,
        "tools_granted": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.post("/threads/{thread_id}/realtime-transcript")
def persist_realtime_transcript(thread_id: str, data: RealtimeTranscriptEvent, request: Request):
    member = _member(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin browser evidence required")
    if not chat_store.thread(str(member.user_id), thread_id):
        raise HTTPException(404, "Aura conversation not found")
    roles = {
        "conversation.item.input_audio_transcription.completed": "user",
        "response.output_audio_transcript.done": "assistant",
        "response.audio_transcript.done": "assistant",
    }
    role = roles.get(data.event_type)
    if role is None:
        raise HTTPException(400, "Unsupported Realtime transcript event")
    try:
        result = transcript_ledger.append(
            str(member.user_id), thread_id, data.transcript_session_id, data.event_id, role, data.transcript
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **result,
        "thread_id": thread_id,
        "persisted": True,
        "source": "realtime_transcript",
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = [
    "router",
    "RealtimeSessionGate",
    "RealtimeTranscriptLedger",
    "RealtimeTranscriptEvent",
    "diagnostics",
    "create_realtime_client_secret",
    "persist_realtime_transcript",
]
