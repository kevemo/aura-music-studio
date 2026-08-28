from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request

from .aura_chat_store import AuraChatStore
from .plans import AURA_SPEECH

router = APIRouter(prefix="/aura-intelligence/api", tags=["Aura Realtime Voice"])

_CLIENT_SECRET_URL = "https://api.openai.com/v1/realtime/client_secrets"
_DEFAULT_MODEL = "gpt-realtime-2.1"
_WINDOW_SECONDS = 600
_MAX_MINTS_PER_WINDOW = 6


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
                """
                CREATE TABLE IF NOT EXISTS aura_realtime_session_mints (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
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
            con.execute(
                "INSERT INTO aura_realtime_session_mints(id,user_id,created_at) VALUES(?,?,?)",
                (reservation_id, user_id, now),
            )
        return reservation_id

    def cancel(self, reservation_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM aura_realtime_session_mints WHERE id=?", (reservation_id,))


gate = RealtimeSessionGate()
chat_store = AuraChatStore()


def diagnostics() -> dict:
    return {
        "configured": bool(os.getenv("OPENAI_API_KEY") and os.getenv("AURA_REALTIME_VOICE")),
        "model": os.getenv("AURA_REALTIME_MODEL", _DEFAULT_MODEL),
        "voice_configured": bool(os.getenv("AURA_REALTIME_VOICE")),
        "transport": "webrtc_client_secret",
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
            "audio": {"output": {"voice": voice}},
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
        "server_api_key_exposed": False,
        "tools_granted": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = ["router", "RealtimeSessionGate", "diagnostics", "create_realtime_client_secret"]
