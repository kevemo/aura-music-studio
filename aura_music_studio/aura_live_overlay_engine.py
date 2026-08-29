from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["Aura LIVE Overlay Event Engine"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
EVENT_LIMIT = 1000
EVENT_TYPES = {
    "viewer_joined", "follow", "subscribe", "gift", "share", "like", "like_milestone", "comment",
    "battle_start", "battle_progress", "battle_end", "poll", "treasure_chest", "question", "pinned_message",
    "live_shopping", "intro", "super_fan", "shared_stream", "chat_deleted", "custom",
}
SAFE_ACTIONS = {
    "show_widget", "hide_widget", "play_media", "play_sound", "speak", "set_text", "increment_goal",
    "start_timer", "add_timer_seconds", "spin_wheel", "spotlight_viewer", "switch_scene", "set_theme",
}
_rule_last_fired: dict[tuple[str, str], float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
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
    allowed = {
        "username", "display_name", "gift_name", "gift_id", "gift_count", "coins", "diamonds", "message",
        "likes", "viewer_count", "followers", "subscribers", "shares", "team", "result", "progress", "target",
        "avatar_url", "is_follower", "is_subscriber", "is_moderator", "is_team", "is_top_gifter", "title", "label",
        "poll_option", "battle_score", "source", "synthetic",
    }
    out: dict = {}
    for key, value in payload.items():
        if key not in allowed:
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


def process_overlay_event(user_id: str, event_type: str, payload: dict, *, synthetic: bool = False) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported LIVE event type")
    clean = _clean_payload(payload)
    clean["synthetic"] = bool(synthetic)
    fired: list[dict] = []
    goal_updates: list[dict] = []
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
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
        rules = con.execute("SELECT * FROM live_overlay_rules WHERE user_id=? AND event_type=? AND enabled=1 ORDER BY updated_at DESC", (user_id, event_type)).fetchall()
        now_mono = time.monotonic()
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
            _rule_last_fired[key] = now_mono
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
    return {"accepted": True, "event_type": event_type, "synthetic": bool(synthetic), "rules_fired": fired, "goals_updated": goal_updates}


class EngineEvent(BaseModel):
    event_type: str
    payload: dict = Field(default_factory=dict)


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
        "schema_version": 1,
        "accepted_event_types": sorted(EVENT_TYPES),
        "safe_actions": sorted(SAFE_ACTIONS),
        "arbitrary_javascript": False,
        "shell_commands": False,
        "provider_connected": False,
        "production_connector_gate": "A trusted, maintainable TikTok LIVE event adapter must authenticate and normalize provider events into this bounded contract before production use.",
    }


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
