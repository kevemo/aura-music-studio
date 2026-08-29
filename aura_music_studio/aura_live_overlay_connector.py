from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["Aura LIVE Event Connector"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
TOKEN_BYTES = 32
MAX_EVENTS_PER_MINUTE = max(1, min(int(os.getenv("AURA_LIVE_CONNECTOR_EVENTS_PER_MINUTE", "1200")), 100_000))
MAX_PAYLOAD_BYTES = max(1024, min(int(os.getenv("AURA_LIVE_CONNECTOR_MAX_PAYLOAD_BYTES", "32768")), 262_144))
TRUST_LEVEL = "authenticated_normalized_relay"


def _event_types() -> set[str]:
    from .aura_live_overlay_engine import EVENT_TYPES

    return EVENT_TYPES


def process_overlay_event(user_id: str, event_type: str, payload: dict, *, synthetic: bool = False) -> dict:
    from .aura_live_overlay_engine import process_overlay_event as engine_process_overlay_event

    return engine_process_overlay_event(user_id, event_type, payload, synthetic=synthetic)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_receipts_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS live_overlay_connector_receipts (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('processing','completed')),
            provider_timestamp TEXT,
            received_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY(user_id,provider,session_id,event_id)
        )
        """
    )


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_connectors (
                user_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                label TEXT NOT NULL DEFAULT 'Trusted LIVE relay',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT
            );
            CREATE TABLE IF NOT EXISTS live_overlay_connector_rate (
                token_hash TEXT NOT NULL,
                minute_bucket INTEGER NOT NULL,
                event_count INTEGER NOT NULL,
                PRIMARY KEY(token_hash,minute_bucket)
            );
            """
        )
        table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_overlay_connector_receipts'"
        ).fetchone()
        if not table:
            _create_receipts_table(con)
            return

        columns = {row[1] for row in con.execute("PRAGMA table_info(live_overlay_connector_receipts)").fetchall()}
        required = {"user_id", "provider", "session_id", "event_id", "event_type", "state", "provider_timestamp", "received_at", "completed_at"}
        if required.issubset(columns):
            return

        # Development databases may contain the stale #226 or an earlier Chat 2 relay schema.
        # Rebuild the receipt table transactionally and mark historical rows completed so schema
        # evolution can never replay a provider event that may already have affected LIVE state.
        con.execute("ALTER TABLE live_overlay_connector_receipts RENAME TO live_overlay_connector_receipts_legacy")
        _create_receipts_table(con)
        legacy_columns = {row[1] for row in con.execute("PRAGMA table_info(live_overlay_connector_receipts_legacy)").fetchall()}
        state_expr = "state" if "state" in legacy_columns else "'completed'"
        completed_expr = "completed_at" if "completed_at" in legacy_columns else "NULL"
        con.execute(
            f"""
            INSERT OR IGNORE INTO live_overlay_connector_receipts(
                user_id,provider,session_id,event_id,event_type,state,provider_timestamp,received_at,completed_at
            )
            SELECT user_id,'legacy','legacy',event_id,event_type,{state_expr},NULL,received_at,{completed_expr}
            FROM live_overlay_connector_receipts_legacy
            """
        )
        con.execute("DROP TABLE live_overlay_connector_receipts_legacy")


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _bearer_token(request: Request) -> str:
    authorization = (request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "LIVE connector bearer token required")
    token = authorization[7:].strip()
    if len(token) < 32 or len(token) > 256:
        raise HTTPException(401, "LIVE connector bearer token required")
    return token


def _resolve(token: str) -> sqlite3.Row:
    with _connect() as con:
        row = con.execute(
            "SELECT user_id,token_hash,enabled,label,created_at,updated_at,last_event_at FROM live_overlay_connectors WHERE token_hash=? AND enabled=1",
            (_hash(token),),
        ).fetchone()
    if not row:
        raise HTTPException(404, "LIVE connector not found")
    return row


def _rate_limit(token_hash: str) -> None:
    bucket = int(time.time() // 60)
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT event_count FROM live_overlay_connector_rate WHERE token_hash=? AND minute_bucket=?",
            (token_hash, bucket),
        ).fetchone()
        count = int(row["event_count"]) if row else 0
        if count >= MAX_EVENTS_PER_MINUTE:
            raise HTTPException(429, "LIVE connector event rate exceeded")
        con.execute(
            """
            INSERT INTO live_overlay_connector_rate(token_hash,minute_bucket,event_count)
            VALUES(?,?,1)
            ON CONFLICT(token_hash,minute_bucket)
            DO UPDATE SET event_count=event_count+1
            """,
            (token_hash, bucket),
        )
        con.execute("DELETE FROM live_overlay_connector_rate WHERE minute_bucket<?", (bucket - 2,))


def _payload_size(payload: dict) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _provider_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(400, "LIVE provider timestamp must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _claim_event(
    user_id: str,
    provider: str,
    session_id: str,
    event_id: str,
    event_type: str,
    provider_timestamp: str | None,
) -> str:
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """
            SELECT state FROM live_overlay_connector_receipts
            WHERE user_id=? AND provider=? AND session_id=? AND event_id=?
            """,
            (user_id, provider, session_id, event_id),
        ).fetchone()
        if existing:
            return str(existing["state"])
        con.execute(
            """
            INSERT INTO live_overlay_connector_receipts(
                user_id,provider,session_id,event_id,event_type,state,provider_timestamp,received_at
            ) VALUES(?,?,?,?,?,'processing',?,?)
            """,
            (user_id, provider, session_id, event_id, event_type, provider_timestamp, _now()),
        )
    return "claimed"


def _release_failed_claim(user_id: str, provider: str, session_id: str, event_id: str) -> None:
    with _connect() as con:
        con.execute(
            """
            DELETE FROM live_overlay_connector_receipts
            WHERE user_id=? AND provider=? AND session_id=? AND event_id=? AND state='processing'
            """,
            (user_id, provider, session_id, event_id),
        )


def _complete_event(user_id: str, provider: str, session_id: str, event_id: str) -> None:
    completed_at = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        updated = con.execute(
            """
            UPDATE live_overlay_connector_receipts
            SET state='completed',completed_at=?
            WHERE user_id=? AND provider=? AND session_id=? AND event_id=? AND state='processing'
            """,
            (completed_at, user_id, provider, session_id, event_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError("LIVE connector receipt state changed before completion")
        con.execute(
            "UPDATE live_overlay_connectors SET last_event_at=?,updated_at=? WHERE user_id=? AND enabled=1",
            (completed_at, completed_at, user_id),
        )


class ConnectorRotate(BaseModel):
    label: str = Field(default="Trusted LIVE relay", min_length=1, max_length=80)


class ConnectorEvent(BaseModel):
    provider: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    session_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    event_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: str = Field(min_length=1, max_length=80)
    occurred_at: datetime | None = None
    payload: dict = Field(default_factory=dict)


@router.get("/api/live-overlays/connector")
def connector_status(request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute(
            "SELECT enabled,label,created_at,updated_at,last_event_at FROM live_overlay_connectors WHERE user_id=?",
            (member.user_id,),
        ).fetchone()
    return {
        "configured": bool(row),
        "connector": dict(row) if row else None,
        "schema_version": 2,
        "supported_events": sorted(_event_types()),
        "provider_connection_state": "external_dependency",
        "direct_tiktok_connection_claimed": False,
        "provider_moderation_authority_claimed": False,
        "transport": "bearer_token_normalized_relay",
        "trust_level": TRUST_LEVEL,
        "trust_note": "Aura authenticates the approved relay credential; provider-event authenticity remains the adapter's responsibility.",
        "purpose": "Use only with an ESP-approved, policy-compliant provider adapter that normalizes LIVE events into Aura's bounded event contract.",
    }


@router.post("/api/live-overlays/connector/rotate")
def rotate_connector(body: ConnectorRotate, request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            """
            INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at)
            VALUES(?,?,1,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              token_hash=excluded.token_hash,
              enabled=1,
              label=excluded.label,
              updated_at=excluded.updated_at
            """,
            (member.user_id, _hash(raw), body.label, now, now),
        )
    return JSONResponse(
        {
            "ingest_path": "/live-overlay/source/relay/ingest",
            "authorization_scheme": "Bearer",
            "token": raw,
            "token_returned_once": True,
            "token_in_url": False,
            "schema_version": 2,
            "warning": "Treat this bearer token like a password. Rotating it invalidates the previous relay immediately.",
        },
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-overlays/connector/disable")
def disable_connector(request: Request):
    member = _member(request)
    with _connect() as con:
        con.execute(
            "UPDATE live_overlay_connectors SET enabled=0,updated_at=? WHERE user_id=?",
            (_now(), member.user_id),
        )
    return {"disabled": True}


@router.post("/live-overlay/source/relay/ingest", include_in_schema=False)
def connector_ingest(body: ConnectorEvent, request: Request):
    token = _bearer_token(request)
    row = _resolve(token)
    token_hash = str(row["token_hash"])
    _rate_limit(token_hash)
    if body.event_type not in _event_types():
        raise HTTPException(400, "Unsupported normalized LIVE event type")
    if _payload_size(body.payload) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, "LIVE connector payload too large")

    user_id = str(row["user_id"])
    provider_timestamp = _provider_timestamp(body.occurred_at)
    claim = _claim_event(
        user_id,
        body.provider,
        body.session_id,
        body.event_id,
        body.event_type,
        provider_timestamp,
    )
    if claim == "completed":
        return JSONResponse(
            {
                "accepted": True,
                "duplicate": True,
                "provider": body.provider,
                "session_id": body.session_id,
                "event_id": body.event_id,
            },
            headers={"Cache-Control": "no-store"},
        )
    if claim == "processing":
        return JSONResponse(
            {
                "accepted": False,
                "duplicate": False,
                "processing": True,
                "provider": body.provider,
                "session_id": body.session_id,
                "event_id": body.event_id,
            },
            status_code=409,
            headers={"Cache-Control": "no-store", "Retry-After": "1"},
        )

    normalized_payload = dict(body.payload)
    normalized_payload["source"] = body.provider
    try:
        result = process_overlay_event(user_id, body.event_type, normalized_payload, synthetic=False)
    except Exception:
        # The engine did not complete, so the same provider event ID may be retried safely.
        _release_failed_claim(user_id, body.provider, body.session_id, body.event_id)
        raise

    # Once the engine has returned success, never release this claim. If completion bookkeeping
    # fails, leaving the receipt in `processing` fails closed and prevents a retry from applying
    # the same gift/like/follow/goal mutation twice.
    _complete_event(user_id, body.provider, body.session_id, body.event_id)

    result.update(
        {
            "duplicate": False,
            "provider": body.provider,
            "session_id": body.session_id,
            "event_id": body.event_id,
            "normalized_relay": True,
            "trust_level": TRUST_LEVEL,
            "provider_timestamp": provider_timestamp,
            "direct_tiktok_connection_claimed": False,
        }
    )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
