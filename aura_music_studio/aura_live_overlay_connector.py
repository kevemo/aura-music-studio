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

from .aura_live_overlay_engine import EVENT_TYPES, process_overlay_event

router = APIRouter(tags=["Aura LIVE Event Connector"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
TOKEN_BYTES = 32
MAX_EVENTS_PER_MINUTE = max(1, min(int(os.getenv("AURA_LIVE_CONNECTOR_EVENTS_PER_MINUTE", "1200")), 10000))
MAX_PAYLOAD_BYTES = max(1024, min(int(os.getenv("AURA_LIVE_CONNECTOR_MAX_PAYLOAD_BYTES", "16384")), 65536))
MAX_PAYLOAD_KEYS = 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
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
                payload_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                last_error_code TEXT,
                PRIMARY KEY(user_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_connector_receipts_user_state
                ON live_overlay_connector_receipts(user_id,state,received_at);
            CREATE TABLE IF NOT EXISTS live_overlay_connector_rate (
                token_hash TEXT NOT NULL,
                minute_bucket INTEGER NOT NULL,
                event_count INTEGER NOT NULL,
                PRIMARY KEY(token_hash,minute_bucket)
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
    if len(token) < 32 or len(token) > 256:
        raise HTTPException(404, "LIVE connector not found")
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM live_overlay_connectors WHERE token_hash=? AND enabled=1",
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
        if row:
            con.execute(
                "UPDATE live_overlay_connector_rate SET event_count=event_count+1 WHERE token_hash=? AND minute_bucket=?",
                (token_hash, bucket),
            )
        else:
            con.execute(
                "INSERT INTO live_overlay_connector_rate(token_hash,minute_bucket,event_count) VALUES(?,?,1)",
                (token_hash, bucket),
            )
        con.execute("DELETE FROM live_overlay_connector_rate WHERE minute_bucket<?", (bucket - 2,))


def _validated_payload(payload: dict) -> tuple[dict, str]:
    if len(payload) > MAX_PAYLOAD_KEYS:
        raise HTTPException(413, "LIVE connector payload has too many fields")
    normalized: dict = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key or len(key) > 80:
            raise HTTPException(422, "LIVE connector payload contains an invalid field name")
        if isinstance(value, str):
            if len(value) > 2000:
                raise HTTPException(413, "LIVE connector payload string is too large")
        elif not (isinstance(value, (int, float, bool)) or value is None):
            raise HTTPException(422, "LIVE connector payload values must be normalized scalar values")
        normalized[key] = value
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, "LIVE connector payload is too large")
    return normalized, hashlib.sha256(encoded).hexdigest()


class ConnectorRotate(BaseModel):
    label: str = Field(default="Trusted LIVE relay", min_length=1, max_length=80)


class ConnectorEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


@router.get("/api/live-overlays/connector")
def connector_status(request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute(
            "SELECT enabled,label,created_at,updated_at,last_event_at FROM live_overlay_connectors WHERE user_id=?",
            (member.user_id,),
        ).fetchone()
    return JSONResponse(
        {
            "configured": bool(row),
            "connector": dict(row) if row else None,
            "supported_events": sorted(EVENT_TYPES),
            "connection_mode": "esp_approved_normalized_relay",
            "direct_tiktok_connection_claimed": False,
            "provider_write_authority": False,
            "purpose": "Use only with an ESP-approved, policy-compliant relay that normalizes LIVE events into Aura's bounded event contract.",
        },
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-overlays/connector/rotate")
def rotate_connector(body: ConnectorRotate, request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO live_overlay_connectors(user_id,token_hash,enabled,label,created_at,updated_at,last_event_at)
            VALUES(?,?,1,?,?,?,NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                token_hash=excluded.token_hash,
                enabled=1,
                label=excluded.label,
                updated_at=excluded.updated_at,
                last_event_at=NULL
            """,
            (member.user_id, _hash(raw), body.label, now, now),
        )
    base = str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "ingest_url": f"{base}/live-overlay/connector/{raw}",
            "token_returned_once": True,
            "direct_tiktok_connection_claimed": False,
            "warning": "Treat this URL like a password. Rotation invalidates the previous relay credential immediately.",
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
    return JSONResponse(
        {"disabled": True, "provider_write_authority": False},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/live-overlay/connector/{token}", include_in_schema=False)
def connector_ingest(token: str, body: ConnectorEvent):
    row = _resolve(token)
    token_hash = _hash(token)
    _rate_limit(token_hash)
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, "Unsupported normalized LIVE event type")
    payload, payload_sha = _validated_payload(body.payload)
    user_id = str(row["user_id"])
    now = _now()

    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT event_type,payload_sha256,state FROM live_overlay_connector_receipts WHERE user_id=? AND event_id=?",
            (user_id, body.event_id),
        ).fetchone()
        if existing:
            if str(existing["event_type"]) != body.event_type or str(existing["payload_sha256"]) != payload_sha:
                raise HTTPException(409, "LIVE event ID was already used for different event data")
            state = str(existing["state"])
            if state == "processed":
                return JSONResponse(
                    {"accepted": True, "duplicate": True, "event_id": body.event_id, "state": "processed"},
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
            if state == "processing":
                return JSONResponse(
                    {"accepted": False, "duplicate": True, "retryable": True, "event_id": body.event_id, "state": "processing"},
                    status_code=409,
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
            con.execute(
                "UPDATE live_overlay_connector_receipts SET state='processing',received_at=?,processed_at=NULL,last_error_code=NULL WHERE user_id=? AND event_id=?",
                (now, user_id, body.event_id),
            )
        else:
            con.execute(
                """
                INSERT INTO live_overlay_connector_receipts(
                    user_id,event_id,event_type,payload_sha256,state,received_at,processed_at,last_error_code
                ) VALUES(?,?,?,?, 'processing', ?,NULL,NULL)
                """,
                (user_id, body.event_id, body.event_type, payload_sha, now),
            )

    try:
        result = process_overlay_event(user_id, body.event_type, payload, synthetic=False)
    except Exception as exc:
        with _connect() as con:
            con.execute(
                "UPDATE live_overlay_connector_receipts SET state='failed',last_error_code=? WHERE user_id=? AND event_id=?",
                (type(exc).__name__[:80], user_id, body.event_id),
            )
        raise HTTPException(503, "LIVE event processing failed; retry with the same event ID") from exc

    processed_at = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE live_overlay_connector_receipts SET state='processed',processed_at=?,last_error_code=NULL WHERE user_id=? AND event_id=?",
            (processed_at, user_id, body.event_id),
        )
        con.execute(
            "UPDATE live_overlay_connectors SET last_event_at=?,updated_at=? WHERE user_id=?",
            (processed_at, processed_at, user_id),
        )
    result.update(
        {
            "accepted": True,
            "duplicate": False,
            "event_id": body.event_id,
            "normalized_relay": True,
            "direct_tiktok_connection_claimed": False,
            "provider_write_authority": False,
        }
    )
    return JSONResponse(result, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


__all__ = ["router", "ConnectorEvent", "ConnectorRotate"]
