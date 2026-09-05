from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .aura_live_show_control import emergency_mode_from_connection
from .membership import MembershipService

router = APIRouter(tags=["Aura LIVE Overlay Event Engine"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
EVENT_LIMIT = 1000
SOURCE_EVENT_DEDUP_LIMIT = 5000
CONNECTOR_RECEIPT_LIMIT = 5000
CONNECTOR_SOURCE = "esp_approved_normalized_relay"
RELAY_TOKEN_BYTES = 32
MAX_EVENTS_PER_MINUTE = max(1, min(int(os.getenv("AURA_LIVE_CONNECTOR_EVENTS_PER_MINUTE", "1200")), 10000))
MAX_PAYLOAD_BYTES = max(1024, min(int(os.getenv("AURA_LIVE_CONNECTOR_MAX_PAYLOAD_BYTES", "16384")), 65536))
MAX_PAYLOAD_KEYS = 64
PROCESSING_LEASE_SECONDS = max(10, min(int(os.getenv("AURA_LIVE_CONNECTOR_PROCESSING_LEASE_SECONDS", "120")), 900))
EVENT_TYPES = {
    "viewer_joined", "follow", "subscribe", "gift", "share", "like", "like_milestone", "comment",
    "battle_start", "battle_progress", "battle_end", "poll", "treasure_chest", "question", "pinned_message",
    "live_shopping", "intro", "super_fan", "shared_stream", "chat_deleted", "custom",
}
SAFE_ACTIONS = {
    "show_widget", "hide_widget", "play_media", "play_sound", "speak", "set_text", "increment_goal",
    "start_timer", "add_timer_seconds", "spin_wheel", "spotlight_viewer", "switch_scene", "set_theme",
}
ALLOWED_PAYLOAD_KEYS = {
    "username", "display_name", "gift_name", "gift_id", "gift_count", "coins", "diamonds", "message",
    "likes", "viewer_count", "followers", "subscribers", "shares", "team", "result", "progress", "target",
    "avatar_url", "is_follower", "is_subscriber", "is_moderator", "is_team", "is_top_gifter", "title", "label",
    "poll_option", "battle_score", "source", "synthetic",
}
CONNECTOR_PAYLOAD_KEYS = ALLOWED_PAYLOAD_KEYS - {"source", "synthetic"}
_rule_last_fired: dict[tuple[str, str], float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_events_user_id_id
                ON live_overlay_events(user_id, id);
            CREATE TABLE IF NOT EXISTS live_overlay_rules (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                condition_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '[]',
                cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_goals (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                metric TEXT NOT NULL,
                target REAL NOT NULL,
                current REAL NOT NULL DEFAULT 0,
                reset_mode TEXT NOT NULL DEFAULT 'manual',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_session_stats (
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                gift_count INTEGER NOT NULL DEFAULT 0,
                gift_value REAL NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                follows INTEGER NOT NULL DEFAULT 0,
                subscriptions INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, username)
            );
            CREATE TABLE IF NOT EXISTS live_overlay_source_event_dedup (
                source TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                result_json TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY(source,user_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_source_event_dedup_user_time
                ON live_overlay_source_event_dedup(source,user_id,processed_at);
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
        cols = {r[1] for r in con.execute("PRAGMA table_info(live_overlay_session_stats)").fetchall()}
        if "subscriptions" not in cols:
            con.execute("ALTER TABLE live_overlay_session_stats ADD COLUMN subscriptions INTEGER NOT NULL DEFAULT 0")


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _clean_payload(payload: dict) -> dict:
    out: dict = {}
    for key, value in payload.items():
        if key not in ALLOWED_PAYLOAD_KEYS:
            continue
        if isinstance(value, str):
            out[key] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
    return out


def _matches(conditions: dict, payload: dict) -> bool:
    if conditions.get("gift_name") and str(payload.get("gift_name", "")).casefold() != str(conditions["gift_name"]).casefold():
        return False
    if conditions.get("username") and str(payload.get("username", "")).casefold() != str(conditions["username"]).casefold():
        return False
    if "min_coins" in conditions and float(payload.get("coins") or 0) < float(conditions["min_coins"]):
        return False
    if "min_gift_count" in conditions and int(payload.get("gift_count") or 0) < int(conditions["min_gift_count"]):
        return False
    if conditions.get("message_contains") and str(conditions["message_contains"]).casefold() not in str(payload.get("message", "")).casefold():
        return False
    for key in ("is_follower", "is_subscriber"):
        if key in conditions and bool(payload.get(key)) is not bool(conditions[key]):
            return False
    return True


def _goal_delta(metric: str, event_type: str, payload: dict) -> float:
    if metric == "gifts" and event_type == "gift":
        return float(payload.get("gift_count") or 1)
    if metric == "gift_value" and event_type == "gift":
        return float(payload.get("coins") or payload.get("diamonds") or 0)
    if metric == "likes" and event_type in {"like", "like_milestone"}:
        return float(payload.get("likes") or 1)
    if metric == "shares" and event_type == "share":
        return float(payload.get("shares") or 1)
    if metric == "follows" and event_type == "follow":
        return 1.0
    if metric == "subscribers" and event_type == "subscribe":
        return 1.0
    return 0.0


def _update_stats(con: sqlite3.Connection, user_id: str, event_type: str, payload: dict) -> None:
    username = str(payload.get("username") or payload.get("display_name") or "").strip()[:80]
    if not username:
        return
    gift_count = int(payload.get("gift_count") or 1) if event_type == "gift" else 0
    gift_value = float(payload.get("coins") or payload.get("diamonds") or 0) if event_type == "gift" else 0.0
    likes = int(payload.get("likes") or 1) if event_type in {"like", "like_milestone"} else 0
    shares = int(payload.get("shares") or 1) if event_type == "share" else 0
    comments = 1 if event_type == "comment" else 0
    follows = 1 if event_type == "follow" else 0
    subscriptions = 1 if event_type == "subscribe" else 0
    con.execute(
        """
        INSERT INTO live_overlay_session_stats(user_id,username,gift_count,gift_value,likes,shares,comments,follows,subscriptions,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,username) DO UPDATE SET
          gift_count=gift_count+excluded.gift_count,
          gift_value=gift_value+excluded.gift_value,
          likes=likes+excluded.likes,
          shares=shares+excluded.shares,
          comments=comments+excluded.comments,
          follows=follows+excluded.follows,
          subscriptions=subscriptions+excluded.subscriptions,
          updated_at=excluded.updated_at
        """,
        (user_id, username, gift_count, gift_value, likes, shares, comments, follows, subscriptions, _now()),
    )


def _source_identity(source: str | None, event_id: str | None, payload_sha256: str | None) -> tuple[str, str, str] | None:
    supplied = [source is not None, event_id is not None, payload_sha256 is not None]
    if any(supplied) and not all(supplied):
        raise ValueError("Source event identity requires source, event ID and payload digest")
    if not any(supplied):
        return None
    source_value = str(source or "").strip()
    event_value = str(event_id or "").strip()
    digest_value = str(payload_sha256 or "").strip().lower()
    if not source_value or len(source_value) > 80:
        raise ValueError("Invalid source event source")
    if not event_value or len(event_value) > 160:
        raise ValueError("Invalid source event ID")
    if len(digest_value) != 64 or any(c not in "0123456789abcdef" for c in digest_value):
        raise ValueError("Invalid source payload digest")
    return source_value, event_value, digest_value


def process_overlay_event(
    user_id: str,
    event_type: str,
    payload: dict,
    *,
    synthetic: bool = False,
    source: str | None = None,
    source_event_id: str | None = None,
    source_payload_sha256: str | None = None,
) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported LIVE event type")
    identity = _source_identity(source, source_event_id, source_payload_sha256)
    clean = _clean_payload(payload)
    clean["synthetic"] = bool(synthetic)
    if identity:
        clean["source"] = identity[0]
    fired: list[dict] = []
    goal_updates: list[dict] = []
    fired_keys: list[tuple[str, str]] = []
    now_mono = time.monotonic()
    emergency_mode = "normal"
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if identity:
            source_value, event_value, digest_value = identity
            existing = con.execute(
                """SELECT event_type,payload_sha256,result_json
                   FROM live_overlay_source_event_dedup
                   WHERE source=? AND user_id=? AND event_id=?""",
                (source_value, user_id, event_value),
            ).fetchone()
            if existing:
                if str(existing["event_type"]) != event_type or str(existing["payload_sha256"]) != digest_value:
                    raise ValueError("Source event ID was already used for different event data")
                try:
                    prior = json.loads(str(existing["result_json"]))
                except Exception:
                    prior = {"accepted": True, "event_type": event_type, "synthetic": bool(synthetic)}
                if not isinstance(prior, dict):
                    prior = {"accepted": True, "event_type": event_type, "synthetic": bool(synthetic)}
                prior["duplicate"] = True
                prior["source_deduplicated"] = True
                return prior

        emergency_mode = emergency_mode_from_connection(con, user_id)
        con.execute(
            "INSERT INTO live_overlay_events(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (user_id, event_type, json.dumps(clean, separators=(",", ":")), _now()),
        )
        _update_stats(con, user_id, event_type, clean)
        goals = con.execute("SELECT * FROM live_overlay_goals WHERE user_id=? AND enabled=1", (user_id,)).fetchall()
        for goal in goals:
            delta = _goal_delta(str(goal["metric"]), event_type, clean)
            if delta <= 0:
                continue
            new_value = float(goal["current"]) + delta
            con.execute("UPDATE live_overlay_goals SET current=?,updated_at=? WHERE id=? AND user_id=?", (new_value, _now(), goal["id"], user_id))
            goal_updates.append({"goal_id": goal["id"], "current": new_value, "target": float(goal["target"])})

        if emergency_mode == "normal":
            rules = con.execute("SELECT * FROM live_overlay_rules WHERE user_id=? AND event_type=? AND enabled=1 ORDER BY updated_at DESC", (user_id, event_type)).fetchall()
            for row in rules:
                try:
                    conditions = json.loads(row["condition_json"])
                    actions = json.loads(row["actions_json"])
                except Exception:
                    continue
                if not _matches(conditions if isinstance(conditions, dict) else {}, clean):
                    continue
                cooldown = max(0, int(row["cooldown_seconds"] or 0))
                key = (user_id, str(row["id"]))
                if cooldown and now_mono - _rule_last_fired.get(key, -1e12) < cooldown:
                    continue
                safe = []
                for action in actions if isinstance(actions, list) else []:
                    if not isinstance(action, dict) or action.get("action") not in SAFE_ACTIONS:
                        continue
                    params = action.get("params") if isinstance(action.get("params"), dict) else {}
                    safe.append({"action": action["action"], "params": params})
                if not safe:
                    continue
                fired_keys.append(key)
                automation_payload = {
                    "title": str(row["name"])[:100],
                    "label": "automation",
                    "message": json.dumps(safe, separators=(",", ":"))[:500],
                    "source": "aura_rule_engine",
                    "synthetic": bool(synthetic),
                }
                con.execute(
                    "INSERT INTO live_overlay_events(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (user_id, "custom", json.dumps(automation_payload, separators=(",", ":")), _now()),
                )
                fired.append({"rule_id": row["id"], "name": row["name"], "actions": safe})

        con.execute(
            "DELETE FROM live_overlay_events WHERE user_id=? AND id NOT IN (SELECT id FROM live_overlay_events WHERE user_id=? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, EVENT_LIMIT),
        )
        result = {
            "accepted": True,
            "event_type": event_type,
            "synthetic": bool(synthetic),
            "rules_fired": fired,
            "goals_updated": goal_updates,
            "duplicate": False,
            "emergency_mode": emergency_mode,
            "automations_suppressed": emergency_mode in {"automation_pause", "safe_hold"},
            "overlay_safe_hold": emergency_mode == "safe_hold",
        }
        if identity:
            source_value, event_value, digest_value = identity
            con.execute(
                """INSERT INTO live_overlay_source_event_dedup(
                       source,user_id,event_id,event_type,payload_sha256,result_json,processed_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    source_value,
                    user_id,
                    event_value,
                    event_type,
                    digest_value,
                    json.dumps(result, separators=(",", ":")),
                    _now(),
                ),
            )
            con.execute(
                """DELETE FROM live_overlay_source_event_dedup
                   WHERE source=? AND user_id=? AND rowid NOT IN (
                       SELECT rowid FROM live_overlay_source_event_dedup
                       WHERE source=? AND user_id=? ORDER BY processed_at DESC LIMIT ?
                   )""",
                (source_value, user_id, source_value, user_id, SOURCE_EVENT_DEDUP_LIMIT),
            )
    for key in fired_keys:
        _rule_last_fired[key] = now_mono
    return result


class EngineEvent(BaseModel):
    event_type: str
    payload: dict = Field(default_factory=dict)


class ConnectorRotate(BaseModel):
    label: str = Field(default="Trusted LIVE relay", min_length=1, max_length=80)


class ConnectorEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


def _hash_relay_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _relay_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "LIVE relay bearer token required")
    token = auth[7:].strip()
    if len(token) < 32 or len(token) > 256:
        raise HTTPException(401, "LIVE relay bearer token invalid")
    return token


def _resolve_connector(token: str) -> sqlite3.Row:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM live_overlay_connectors WHERE token_hash=? AND enabled=1",
            (_hash_relay_token(token),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "LIVE relay bearer token invalid")
    return row


def _require_active_member(user_id: str) -> dict:
    store = AccountStore()
    user = store.get_user(user_id)
    if not user:
        raise HTTPException(403, "LIVE relay owner account is unavailable")
    current = MembershipService(store).subscriptions.enforce(user)
    if current.get("status") != "active":
        raise HTTPException(403, "Active membership is required for LIVE relay processing")
    return current


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
            raise HTTPException(429, "LIVE relay event rate exceeded")
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


def _validated_connector_payload(payload: dict) -> tuple[dict, str]:
    if len(payload) > MAX_PAYLOAD_KEYS:
        raise HTTPException(413, "LIVE relay payload has too many fields")
    normalized: dict = {}
    for key, value in payload.items():
        if not isinstance(key, str) or key not in CONNECTOR_PAYLOAD_KEYS:
            raise HTTPException(422, f"Unsupported normalized LIVE field: {str(key)[:80]}")
        if isinstance(value, str):
            if len(value) > 500:
                raise HTTPException(413, f"LIVE relay field is too large: {key}")
            normalized[key] = value
        elif isinstance(value, bool) or value is None:
            normalized[key] = value
        elif isinstance(value, (int, float)):
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0 or numeric > 1_000_000_000_000:
                raise HTTPException(422, f"LIVE relay numeric field is out of range: {key}")
            normalized[key] = value
        else:
            raise HTTPException(422, "LIVE relay payload values must be normalized scalar values")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, "LIVE relay payload is too large")
    return normalized, hashlib.sha256(encoded).hexdigest()


def _processing_is_stale(received_at: str) -> bool:
    try:
        started = datetime.fromisoformat(received_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
        return age >= PROCESSING_LEASE_SECONDS
    except Exception:
        return True


def _connector_status_payload(user_id: str) -> dict:
    with _connect() as con:
        row = con.execute(
            "SELECT enabled,label,created_at,updated_at,last_event_at FROM live_overlay_connectors WHERE user_id=?",
            (user_id,),
        ).fetchone()
        receipts = con.execute(
            """SELECT event_id,event_type,state,received_at,processed_at,last_error_code
               FROM live_overlay_connector_receipts WHERE user_id=?
               ORDER BY received_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()
    return {
        "configured": bool(row),
        "connector": dict(row) if row else None,
        "recent_deliveries": [dict(item) for item in receipts],
        "supported_events": sorted(EVENT_TYPES),
        "ingest_path": "/live-overlay/source/relay/events",
        "authorization_scheme": "Bearer",
        "connection_mode": CONNECTOR_SOURCE,
        "provider_connected": False,
        "direct_tiktok_connection_claimed": False,
        "provider_write_authority": False,
        "external_dependency": "An ESP-approved provider/relay with documented LIVE event access must supply real normalized events.",
    }


@router.post("/api/live-overlays/simulate")
def simulate_event(body: EngineEvent, request: Request):
    member = _member(request)
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, "Unsupported LIVE event type")
    try:
        result = process_overlay_event(member.user_id, body.event_type, body.payload, synthetic=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result["provider_event"] = False
    return result


@router.get("/api/live-overlays/event-contract")
def event_contract(request: Request):
    _member(request)
    return {
        "schema_version": 2,
        "accepted_event_types": sorted(EVENT_TYPES),
        "accepted_payload_fields": sorted(CONNECTOR_PAYLOAD_KEYS),
        "safe_actions": sorted(SAFE_ACTIONS),
        "arbitrary_javascript": False,
        "shell_commands": False,
        "normalized_relay_available": True,
        "relay_ingest_path": "/live-overlay/source/relay/events",
        "relay_authentication": "Bearer token returned only at rotation",
        "provider_connected": False,
        "provider_write_authority": False,
        "production_connector_gate": "A trusted, maintainable TikTok LIVE event adapter or other ESP-approved documented provider adapter must authenticate with its Aura relay token and normalize documented provider events into this bounded contract.",
    }


@router.get("/api/live-overlays/connector")
def connector_status(request: Request):
    member = _member(request)
    return JSONResponse(
        _connector_status_payload(member.user_id),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-overlays/connector/rotate")
def rotate_connector(body: ConnectorRotate, request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(RELAY_TOKEN_BYTES)
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
            (member.user_id, _hash_relay_token(raw), body.label.strip(), now, now),
        )
        con.execute("DELETE FROM live_overlay_connector_rate WHERE token_hash<>? AND token_hash NOT IN (SELECT token_hash FROM live_overlay_connectors)", (_hash_relay_token(raw),))
    return JSONResponse(
        {
            "ingest_path": "/live-overlay/source/relay/events",
            "relay_token": raw,
            "authorization_scheme": "Bearer",
            "token_returned_once": True,
            "provider_connected": False,
            "provider_write_authority": False,
            "warning": "Store this token only in the approved relay secret store. Rotation invalidates the previous credential immediately.",
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
        {"disabled": True, "provider_connected": False, "provider_write_authority": False},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/live-overlay/source/relay/events", include_in_schema=False)
def connector_ingest(body: ConnectorEvent, request: Request):
    token = _relay_token(request)
    connector = _resolve_connector(token)
    user_id = str(connector["user_id"])
    _require_active_member(user_id)
    token_hash = _hash_relay_token(token)
    _rate_limit(token_hash)
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, "Unsupported normalized LIVE event type")
    payload, payload_sha = _validated_connector_payload(body.payload)
    now = _now()
    retrying = False

    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """SELECT event_type,payload_sha256,state,received_at
               FROM live_overlay_connector_receipts WHERE user_id=? AND event_id=?""",
            (user_id, body.event_id),
        ).fetchone()
        if existing:
            if str(existing["event_type"]) != body.event_type or str(existing["payload_sha256"]) != payload_sha:
                raise HTTPException(409, "LIVE event ID was already used for different event data")
            state = str(existing["state"])
            if state == "processed":
                return JSONResponse(
                    {
                        "accepted": True,
                        "duplicate": True,
                        "event_id": body.event_id,
                        "state": "processed",
                        "normalized_relay": True,
                        "provider_connected": False,
                        "provider_write_authority": False,
                    },
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
            if state == "processing" and not _processing_is_stale(str(existing["received_at"])):
                return JSONResponse(
                    {
                        "accepted": False,
                        "duplicate": True,
                        "retryable": True,
                        "event_id": body.event_id,
                        "state": "processing",
                    },
                    status_code=409,
                    headers={
                        "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer",
                        "Retry-After": str(PROCESSING_LEASE_SECONDS),
                    },
                )
            retrying = True
            con.execute(
                """UPDATE live_overlay_connector_receipts
                   SET state='processing',received_at=?,processed_at=NULL,last_error_code=NULL
                   WHERE user_id=? AND event_id=?""",
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
        result = process_overlay_event(
            user_id,
            body.event_type,
            payload,
            synthetic=False,
            source=CONNECTOR_SOURCE,
            source_event_id=body.event_id,
            source_payload_sha256=payload_sha,
        )
    except ValueError as exc:
        with _connect() as con:
            con.execute(
                "UPDATE live_overlay_connector_receipts SET state='failed',last_error_code=? WHERE user_id=? AND event_id=?",
                ("SourceEventConflict", user_id, body.event_id),
            )
        raise HTTPException(409, str(exc)) from exc
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
            """UPDATE live_overlay_connector_receipts
               SET state='processed',processed_at=?,last_error_code=NULL
               WHERE user_id=? AND event_id=?""",
            (processed_at, user_id, body.event_id),
        )
        con.execute(
            "UPDATE live_overlay_connectors SET last_event_at=?,updated_at=? WHERE user_id=?",
            (processed_at, processed_at, user_id),
        )
        con.execute(
            """DELETE FROM live_overlay_connector_receipts
               WHERE user_id=? AND rowid NOT IN (
                   SELECT rowid FROM live_overlay_connector_receipts
                   WHERE user_id=? ORDER BY received_at DESC LIMIT ?
               )""",
            (user_id, user_id, CONNECTOR_RECEIPT_LIMIT),
        )
    engine_duplicate = bool(result.get("duplicate"))
    result.update(
        {
            "accepted": True,
            "duplicate": engine_duplicate,
            "event_id": body.event_id,
            "normalized_relay": True,
            "recovered_retry": bool(retrying and engine_duplicate),
            "provider_connected": False,
            "direct_tiktok_connection_claimed": False,
            "provider_write_authority": False,
        }
    )
    return JSONResponse(result, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/live-overlay-studio/connector", response_class=HTMLResponse, include_in_schema=False)
def connector_setup(request: Request):
    member = _member(request)
    product = escape(str(member.plan.name))
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Event Relay</title><style>
:root{{--bg:#080a13;--card:#121827;--line:#ffffff1f;--muted:#b8c2d7;--gold:#efc96b;--violet:#9b72ff;--danger:#ff8ca6}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}}main{{max-width:980px;margin:auto;padding:32px 18px 70px}}a{{color:#fff}}h1{{font-size:clamp(2.2rem,6vw,4.2rem);line-height:1;margin:.2em 0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}}button,input{{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#171d2e;color:#fff;padding:11px 13px}}button{{cursor:pointer;font-weight:850}}.primary{{background:linear-gradient(120deg,var(--gold),var(--violet));color:#130b20;border:0}}.danger{{border-color:var(--danger);color:#ffdce4}}.muted{{color:var(--muted);line-height:1.5}}code{{display:block;white-space:pre-wrap;word-break:break-all;background:#05070d;border-radius:10px;padding:12px;color:var(--gold)}}.status{{display:inline-block;border:1px solid #ffffff25;border-radius:999px;padding:6px 10px}}table{{width:100%;border-collapse:collapse;font-size:.9rem}}td,th{{text-align:left;border-bottom:1px solid #ffffff16;padding:8px 5px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}table{{font-size:.78rem}}}}
</style></head><body><main><p><a href='/live-overlay-studio'>← Aura LIVE Overlay Studio</a></p><div class='status'>{product} tier</div><h1>Trusted LIVE event relay</h1><p class='muted'>This page configures Aura's real normalized event-ingress boundary. It does not claim TikTok or any other provider is connected. A documented, ESP-approved provider adapter remains an external dependency until authorization and credentials exist.</p><div class='grid'><section class='card'><h2>Connection state</h2><p id='state' class='muted'>Loading…</p><label>Relay label</label><input id='label' value='Trusted LIVE relay' maxlength='80' style='width:100%'><p><button id='rotate' class='primary'>Generate / rotate relay credential</button> <button id='disable' class='danger'>Disable relay</button></p><p class='muted'>Rotation invalidates the previous relay credential immediately.</p></section><section class='card'><h2>One-time credential</h2><p class='muted'>The bearer token is shown only after rotation. Aura stores only its SHA-256 digest.</p><b>Endpoint</b><code id='endpoint'>Generate a credential first.</code><b>Authorization</b><code id='auth'>Bearer token is not available.</code><button id='copy' disabled>Copy relay configuration</button><p id='copyStatus' class='muted'></p></section></div><section class='card'><h2>Provider truth</h2><p class='muted'><b>Provider connected:</b> <span id='provider'>No</span><br><b>Direct TikTok connection claimed:</b> No<br><b>Provider moderation/write authority:</b> None<br><b>Required external dependency:</b> approved provider LIVE event access that can send documented events into this endpoint.</p></section><section class='card'><h2>Recent delivery state</h2><p class='muted'>No raw chat/gift payload is duplicated into the receipt log. Only event identity, type, state and bounded error code are retained for recovery/audit.</p><div style='overflow:auto'><table><thead><tr><th>Event ID</th><th>Type</th><th>State</th><th>Received</th><th>Error</th></tr></thead><tbody id='deliveries'><tr><td colspan='5'>No deliveries yet.</td></tr></tbody></table></div></section><section class='card'><h2>Relay contract</h2><p class='muted'>Send JSON with <code>{{"event_id":"provider-unique-id","event_type":"gift","payload":{{"username":"viewer","gift_name":"Rose","gift_count":1,"coins":1}}}}</code> and HTTP header <code>Authorization: Bearer YOUR_ONE_TIME_TOKEN</code>. Duplicate event IDs are applied once only; conflicting reuse fails closed; in-flight crashes can be retried safely.</p><p><a href='/api/live-overlays/event-contract'>View the bounded event contract</a></p></section></main><script>
let lastToken='';let lastEndpoint='';const $=id=>document.getElementById(id);function esc(v){{return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}}async function load(){{const r=await fetch('/api/live-overlays/connector',{{cache:'no-store'}});const d=await r.json();if(!r.ok){{$('state').textContent=d.detail||'Unable to load relay state';return}}$('state').textContent=d.configured?(d.connector.enabled?'Configured and enabled':'Configured but disabled'):'Not configured';$('provider').textContent=d.provider_connected?'Yes':'No';if(d.connector?.label)$('label').value=d.connector.label;const rows=d.recent_deliveries||[];$('deliveries').innerHTML=rows.length?rows.map(x=>`<tr><td>${{esc(x.event_id)}}</td><td>${{esc(x.event_type)}}</td><td>${{esc(x.state)}}</td><td>${{esc(x.received_at)}}</td><td>${{esc(x.last_error_code||'')}}</td></tr>`).join(''):`<tr><td colspan='5'>No deliveries yet.</td></tr>`}}$('rotate').onclick=async()=>{{const r=await fetch('/api/live-overlays/connector/rotate',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{label:$('label').value}})}});const d=await r.json();if(!r.ok){{alert(d.detail||'Unable to rotate relay');return}}lastToken=d.relay_token;lastEndpoint=location.origin+d.ingest_path;$('endpoint').textContent=lastEndpoint;$('auth').textContent='Bearer '+lastToken;$('copy').disabled=false;await load()}};$('disable').onclick=async()=>{{if(!confirm('Disable this LIVE relay credential?'))return;const r=await fetch('/api/live-overlays/connector/disable',{{method:'POST'}});const d=await r.json();if(!r.ok){{alert(d.detail||'Unable to disable relay');return}}lastToken='';$('auth').textContent='Bearer token is not available.';$('copy').disabled=true;await load()}};$('copy').onclick=async()=>{{if(!lastToken)return;try{{await navigator.clipboard.writeText('Endpoint: '+lastEndpoint+'\nAuthorization: Bearer '+lastToken);$('copyStatus').textContent='Relay configuration copied.'}}catch{{$('copyStatus').textContent='Clipboard access was blocked. Copy the endpoint and Authorization value manually.'}}}};load();
</script></body></html>""",
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.post("/api/live-overlays/session/reset")
def reset_session_stats(request: Request):
    member = _member(request)
    with _connect() as con:
        con.execute("DELETE FROM live_overlay_session_stats WHERE user_id=?", (member.user_id,))
        rows = con.execute("SELECT id,reset_mode FROM live_overlay_goals WHERE user_id=?", (member.user_id,)).fetchall()
        for row in rows:
            if row["reset_mode"] == "per_live":
                con.execute("UPDATE live_overlay_goals SET current=0,updated_at=? WHERE id=? AND user_id=?", (_now(), row["id"], member.user_id))
    return {"reset": True, "session_id": secrets.token_urlsafe(10)}
