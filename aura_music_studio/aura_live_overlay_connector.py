from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .aura_live_overlay_engine import EVENT_TYPES, process_overlay_event
from .aura_live_overlay_personalization import router as personalization_router
from .aura_live_overlay_wizard import router as setup_wizard_router

router = APIRouter(tags=["Aura LIVE Event Connector"])
router.include_router(setup_wizard_router)
router.include_router(personalization_router)
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
TOKEN_BYTES = 32
MAX_EVENTS_PER_MINUTE = int(os.getenv("AURA_LIVE_CONNECTOR_EVENTS_PER_MINUTE", "1200"))
_rate: dict[str, list[float]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
            CREATE TABLE IF NOT EXISTS live_overlay_connector_receipts (
                user_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY(user_id,event_id)
            );
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _resolve(token: str) -> sqlite3.Row:
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_connectors WHERE token_hash=? AND enabled=1", (_hash(token),)).fetchone()
    if not row:
        raise HTTPException(404, "LIVE connector not found")
    return row


def _rate_limit(token_hash: str) -> None:
    now = time.monotonic()
    rows = [x for x in _rate.get(token_hash, []) if now - x < 60]
    if len(rows) >= MAX_EVENTS_PER_MINUTE:
        raise HTTPException(429, "LIVE connector event rate exceeded")
    rows.append(now)
    _rate[token_hash] = rows


class ConnectorRotate(BaseModel):
    label: str = Field(default="Trusted LIVE relay", min_length=1, max_length=80)


class ConnectorEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=160)
    event_type: str
    payload: dict = Field(default_factory=dict)


@router.get("/api/live-overlays/connector")
def connector_status(request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute("SELECT enabled,label,created_at,updated_at,last_event_at FROM live_overlay_connectors WHERE user_id=?", (member.user_id,)).fetchone()
    return {
        "configured": bool(row),
        "connector": dict(row) if row else None,
        "supported_events": sorted(EVENT_TYPES),
        "direct_tiktok_connection_claimed": False,
        "purpose": "Use this only with an ESP-approved, policy-compliant relay that normalizes LIVE events into the bounded Aura contract.",
    }


@router.post("/api/live-overlays/connector/rotate")
def rotate_connector(body: ConnectorRotate, request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    with _connect() as con:
        con.execute(
            """
            INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at)
            VALUES(?,?,1,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash,enabled=1,label=excluded.label,updated_at=excluded.updated_at
            """,
            (member.user_id, _hash(raw), body.label, _now(), _now()),
        )
    base = str(request.base_url).rstrip("/")
    return {
        "ingest_url": f"{base}/live-overlay/connector/{raw}",
        "token_returned_once": True,
        "warning": "Treat this URL like a password. Rotating it invalidates the previous relay immediately.",
    }


@router.post("/api/live-overlays/connector/disable")
def disable_connector(request: Request):
    member = _member(request)
    with _connect() as con:
        con.execute("UPDATE live_overlay_connectors SET enabled=0,updated_at=? WHERE user_id=?", (_now(), member.user_id))
    return {"disabled": True}


@router.post("/live-overlay/connector/{token}", include_in_schema=False)
def connector_ingest(token: str, body: ConnectorEvent):
    row = _resolve(token)
    token_hash = _hash(token)
    _rate_limit(token_hash)
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, "Unsupported normalized LIVE event type")
    user_id = str(row["user_id"])
    with _connect() as con:
        try:
            con.execute("INSERT INTO live_overlay_connector_receipts(user_id,event_id,event_type,received_at) VALUES(?,?,?,?)", (user_id, body.event_id, body.event_type, _now()))
        except sqlite3.IntegrityError:
            return JSONResponse({"accepted": True, "duplicate": True, "event_id": body.event_id}, headers={"Cache-Control": "no-store"})
        con.execute("UPDATE live_overlay_connectors SET last_event_at=?,updated_at=? WHERE user_id=?", (_now(), _now(), user_id))
    result = process_overlay_event(user_id, body.event_type, body.payload, synthetic=False)
    result.update({"duplicate": False, "event_id": body.event_id, "normalized_relay": True})
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
