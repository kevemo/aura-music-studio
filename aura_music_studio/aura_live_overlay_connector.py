from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["Aura LIVE Event Connector"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
TOKEN_BYTES = 32
MAX_EVENTS_PER_MINUTE = max(1, min(int(os.getenv("AURA_LIVE_CONNECTOR_EVENTS_PER_MINUTE", "1200")), 100_000))
MAX_PAYLOAD_BYTES = max(1024, min(int(os.getenv("AURA_LIVE_CONNECTOR_MAX_PAYLOAD_BYTES", "32768")), 262_144))
MAX_PAYLOAD_KEYS = 64
MAX_STRING_CHARS = 2000
TRUST_LEVEL = "authenticated_normalized_relay"
ZERO_SHA256 = "0" * 64
_PAYLOAD_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
_RESERVED_PAYLOAD_KEYS = {
    "source",
    "provider",
    "session_id",
    "event_id",
    "trust_level",
    "provider_timestamp",
    "synthetic",
}


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
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS live_overlay_connector_receipts (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('processing','completed','failed')),
            provider_timestamp TEXT,
            received_at TEXT NOT NULL,
            completed_at TEXT,
            last_error_code TEXT,
            PRIMARY KEY(user_id,provider,session_id,event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_live_overlay_connector_receipts_user_state
            ON live_overlay_connector_receipts(user_id,state,received_at);
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
        required = {
            "user_id",
            "provider",
            "session_id",
            "event_id",
            "event_type",
            "payload_sha256",
            "state",
            "provider_timestamp",
            "received_at",
            "completed_at",
            "last_error_code",
        }
        if required.issubset(columns):
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_overlay_connector_receipts_user_state ON live_overlay_connector_receipts(user_id,state,received_at)"
            )
            return

        legacy_rows = [dict(row) for row in con.execute("SELECT * FROM live_overlay_connector_receipts").fetchall()]
        con.execute("ALTER TABLE live_overlay_connector_receipts RENAME TO live_overlay_connector_receipts_legacy")
        _create_receipts_table(con)
        for row in legacy_rows:
            received_at = str(row.get("received_at") or _now())
            completed_at = str(row.get("completed_at") or row.get("processed_at") or received_at)
            payload_sha = str(row.get("payload_sha256") or ZERO_SHA256)
            if len(payload_sha) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in payload_sha):
                payload_sha = ZERO_SHA256
            con.execute(
                """
                INSERT OR IGNORE INTO live_overlay_connector_receipts(
                    user_id,provider,session_id,event_id,event_type,payload_sha256,state,
                    provider_timestamp,received_at,completed_at,last_error_code
                ) VALUES(?,?,?,?,?,?,'completed',?,?,?,NULL)
                """,
                (
                    str(row.get("user_id") or "legacy"),
                    str(row.get("provider") or "legacy"),
                    str(row.get("session_id") or "legacy"),
                    str(row.get("event_id") or "legacy-event"),
                    str(row.get("event_type") or "custom"),
                    payload_sha.lower(),
                    row.get("provider_timestamp"),
                    received_at,
                    completed_at,
                ),
            )
        con.execute("DROP TABLE live_overlay_connector_receipts_legacy")


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _connector_row(user_id: str) -> sqlite3.Row | None:
    with _connect() as con:
        return con.execute(
            "SELECT enabled,label,created_at,updated_at,last_event_at FROM live_overlay_connectors WHERE user_id=?",
            (user_id,),
        ).fetchone()


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


def _validated_payload(payload: dict) -> tuple[dict, str]:
    if len(payload) > MAX_PAYLOAD_KEYS:
        raise HTTPException(413, "LIVE connector payload has too many fields")
    normalized: dict = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not _PAYLOAD_KEY.fullmatch(key):
            raise HTTPException(422, "LIVE connector payload contains an invalid field name")
        if key in _RESERVED_PAYLOAD_KEYS:
            raise HTTPException(422, f"LIVE connector payload field is reserved: {key}")
        if isinstance(value, str):
            if len(value) > MAX_STRING_CHARS:
                raise HTTPException(413, "LIVE connector payload string is too large")
        elif isinstance(value, bool) or value is None or isinstance(value, int):
            pass
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise HTTPException(422, "LIVE connector payload numbers must be finite")
        else:
            raise HTTPException(422, "LIVE connector payload values must be normalized scalar values")
        normalized[key] = value
    try:
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "LIVE connector payload is not valid normalized JSON") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, "LIVE connector payload too large")
    return normalized, hashlib.sha256(encoded).hexdigest()


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
    payload_sha256: str,
    provider_timestamp: str | None,
) -> str:
    received_at = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """
            SELECT event_type,payload_sha256,state,provider_timestamp
            FROM live_overlay_connector_receipts
            WHERE user_id=? AND provider=? AND session_id=? AND event_id=?
            """,
            (user_id, provider, session_id, event_id),
        ).fetchone()
        if existing:
            if str(existing["event_type"]) != event_type or str(existing["payload_sha256"]) != payload_sha256:
                raise HTTPException(409, "LIVE event ID was already used for different event data")
            if existing["provider_timestamp"] != provider_timestamp:
                raise HTTPException(409, "LIVE event ID was already used with a different provider timestamp")
            state = str(existing["state"])
            if state == "failed":
                con.execute(
                    """
                    UPDATE live_overlay_connector_receipts
                    SET state='processing',received_at=?,completed_at=NULL,last_error_code=NULL
                    WHERE user_id=? AND provider=? AND session_id=? AND event_id=? AND state='failed'
                    """,
                    (received_at, user_id, provider, session_id, event_id),
                )
                return "claimed"
            return state
        con.execute(
            """
            INSERT INTO live_overlay_connector_receipts(
                user_id,provider,session_id,event_id,event_type,payload_sha256,state,
                provider_timestamp,received_at,completed_at,last_error_code
            ) VALUES(?,?,?,?,?,?,'processing',?,?,NULL,NULL)
            """,
            (
                user_id,
                provider,
                session_id,
                event_id,
                event_type,
                payload_sha256,
                provider_timestamp,
                received_at,
            ),
        )
    return "claimed"


def _mark_failed(user_id: str, provider: str, session_id: str, event_id: str, exc: Exception) -> None:
    with _connect() as con:
        con.execute(
            """
            UPDATE live_overlay_connector_receipts
            SET state='failed',last_error_code=?
            WHERE user_id=? AND provider=? AND session_id=? AND event_id=? AND state='processing'
            """,
            (type(exc).__name__[:80], user_id, provider, session_id, event_id),
        )


def _complete_event(user_id: str, provider: str, session_id: str, event_id: str) -> None:
    completed_at = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        updated = con.execute(
            """
            UPDATE live_overlay_connector_receipts
            SET state='completed',completed_at=?,last_error_code=NULL
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


@router.get("/live-overlay-studio/provider-relay", response_class=HTMLResponse, include_in_schema=False)
def connector_setup_page(request: Request):
    _member(request)
    page = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Provider Relay</title><style>
body{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(900px,calc(100% - 28px));margin:auto;padding:32px 0}.card{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:20px;margin:14px 0}.muted{color:#bdc6d8;line-height:1.55}.status{display:inline-flex;align-items:center;gap:8px;border:1px solid #ffffff24;border-radius:999px;padding:7px 10px;font-weight:800}.dot{width:9px;height:9px;border-radius:50%;background:#f5bf57}.danger{border-color:#ff8ca6;color:#ffdce4}.good{color:#baf7cb}.warn{color:#ffd68a}button,input{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px}button{cursor:pointer;font-weight:850}.primary{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}input{min-width:260px;flex:1}code{display:block;white-space:pre-wrap;word-break:break-all;background:#080b16;border:1px solid #ffffff18;border-radius:12px;padding:12px;color:#efc96b}.hidden{display:none}a{color:#efc96b}@media(max-width:640px){.row{align-items:stretch}.row>*{width:100%;box-sizing:border-box}}</style></head><body><main class='wrap'><a href='/live-overlay-studio'>← Aura LIVE control room</a><h1>Trusted LIVE Provider Relay</h1><p class='muted'>This page configures Aura's secure normalized-event ingress. It does not connect TikTok by itself and it does not grant moderation-write authority. A real ESP-approved provider adapter must still authenticate to this relay and obtain provider capability separately.</p><div class='card'><div class='status'><span class='dot'></span><span id='providerState'>External dependency</span></div><h2>Relay status</h2><p id='configured' class='muted'>Loading current state…</p><p id='lastEvent' class='muted'></p></div><div class='card'><h2>Create or rotate credential</h2><p class='muted'>The Bearer credential is shown once. Store it only in the approved provider adapter or secure secret store. Rotation invalidates the previous credential immediately.</p><div class='row'><input id='label' maxlength='80' value='Trusted LIVE relay' aria-label='Relay label'><button id='rotate' class='primary'>Create / rotate relay</button></div><div id='secretBox' class='hidden'><h3>One-time credential</h3><code id='secret'></code><p class='muted'>Send requests to <code id='path'></code> using <b>Authorization: Bearer &lt;credential&gt;</b>. Do not place the credential in the URL.</p><button id='copy'>Copy credential</button></div></div><div class='card'><h2>Disable relay</h2><p class='muted'>Disable immediately blocks the active credential. Stored historical event receipts remain for replay protection and operational evidence.</p><button id='disable' class='danger'>Disable active relay</button><p id='actionStatus' class='muted' aria-live='polite'></p></div><div class='card'><h2>Provider contract</h2><p class='muted'>Each event must include a provider ID, LIVE session ID, provider event ID, supported event type, optional timezone-aware occurrence time, and normalized scalar payload. Creator identity is derived from the Bearer credential, never accepted from the provider payload.</p><p class='warn'>Provider state remains <b>External dependency</b> until a real approved adapter is configured and authorized.</p></div></main><script>
'use strict';const $=id=>document.getElementById(id);async function readJson(r){try{return await r.json()}catch{return {detail:'Unexpected server response'}}}async function load(){const r=await fetch('/api/live-overlays/connector',{cache:'no-store'}),d=await readJson(r);if(!r.ok){$('configured').textContent=d.detail||'Unable to load relay state';return}$('providerState').textContent=d.provider_connection_state==='external_dependency'?'External dependency':d.provider_connection_state;const c=d.connector;if(!c){$('configured').textContent='Relay not configured.';$('lastEvent').textContent='No provider events received.';return}$('configured').textContent=(c.enabled?'Relay enabled':'Relay disabled')+' · '+c.label;$('lastEvent').textContent=c.last_event_at?'Last accepted event: '+c.last_event_at:'No provider events received yet.'}function status(t,ok=false){$('actionStatus').textContent=t;$('actionStatus').className=ok?'good':'muted'}$('rotate').onclick=async()=>{status('Creating a new relay credential…');const r=await fetch('/api/live-overlays/connector/rotate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({label:$('label').value||'Trusted LIVE relay'})}),d=await readJson(r);if(!r.ok){status(d.detail||'Unable to rotate relay');return}$('secret').textContent=d.token;$('path').textContent=d.ingest_path;$('secretBox').classList.remove('hidden');status('New credential created. The previous relay credential is now invalid.',true);await load()};$('disable').onclick=async()=>{status('Disabling relay…');const r=await fetch('/api/live-overlays/connector/disable',{method:'POST'}),d=await readJson(r);if(!r.ok){status(d.detail||'Unable to disable relay');return}$('secretBox').classList.add('hidden');$('secret').textContent='';status('Relay disabled.',true);await load()};$('copy').onclick=async()=>{const value=$('secret').textContent;if(!value)return;try{await navigator.clipboard.writeText(value);status('Credential copied. Store it securely.',true)}catch{status('Clipboard access was unavailable. Copy the credential manually.')}};load();
</script></body></html>"""
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/live-overlays/connector")
def connector_status(request: Request):
    member = _member(request)
    row = _connector_row(member.user_id)
    return JSONResponse(
        {
            "configured": bool(row),
            "connector": dict(row) if row else None,
            "setup_path": "/live-overlay-studio/provider-relay",
            "schema_version": 2,
            "supported_events": sorted(_event_types()),
            "provider_connection_state": "external_dependency",
            "direct_tiktok_connection_claimed": False,
            "provider_moderation_authority_claimed": False,
            "transport": "bearer_token_normalized_relay",
            "trust_level": TRUST_LEVEL,
            "trust_note": "Aura authenticates the approved relay credential; provider-event authenticity remains the adapter's responsibility.",
            "purpose": "Use only with an ESP-approved, policy-compliant provider adapter that normalizes LIVE events into Aura's bounded event contract.",
        },
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-overlays/connector/rotate")
def rotate_connector(body: ConnectorRotate, request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
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
    return JSONResponse(
        {"disabled": True, "provider_moderation_authority_claimed": False},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/live-overlay/source/relay/ingest", include_in_schema=False)
def connector_ingest(body: ConnectorEvent, request: Request):
    token = _bearer_token(request)
    row = _resolve(token)
    token_hash = str(row["token_hash"])
    _rate_limit(token_hash)
    if body.event_type not in _event_types():
        raise HTTPException(400, "Unsupported normalized LIVE event type")
    payload, payload_sha256 = _validated_payload(body.payload)
    provider_timestamp = _provider_timestamp(body.occurred_at)

    user_id = str(row["user_id"])
    claim = _claim_event(
        user_id,
        body.provider,
        body.session_id,
        body.event_id,
        body.event_type,
        payload_sha256,
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
                "state": "completed",
            },
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    if claim == "processing":
        return JSONResponse(
            {
                "accepted": False,
                "duplicate": True,
                "processing": True,
                "retryable": True,
                "provider": body.provider,
                "session_id": body.session_id,
                "event_id": body.event_id,
                "state": "processing",
            },
            status_code=409,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Retry-After": "1"},
        )

    normalized_payload = dict(payload)
    normalized_payload["source"] = body.provider
    try:
        result = process_overlay_event(user_id, body.event_type, normalized_payload, synthetic=False)
    except Exception as exc:
        _mark_failed(user_id, body.provider, body.session_id, body.event_id, exc)
        raise HTTPException(503, "LIVE event processing failed; retry with the same normalized event") from exc

    _complete_event(user_id, body.provider, body.session_id, body.event_id)

    result.update(
        {
            "accepted": True,
            "duplicate": False,
            "provider": body.provider,
            "session_id": body.session_id,
            "event_id": body.event_id,
            "normalized_relay": True,
            "trust_level": TRUST_LEVEL,
            "provider_timestamp": provider_timestamp,
            "direct_tiktok_connection_claimed": False,
            "provider_moderation_authority_claimed": False,
        }
    )
    return JSONResponse(
        result,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
