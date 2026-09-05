from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .credit_wallet import store as coin_wallet

router = APIRouter(tags=["Shared Skies LIVE Network"])

PRODUCT_NAME = "Shared Skies Streaming Studio"
MAX_COHOSTS = 8
CREATIVE_SURFACES = {
    "music": "/studio/daw",
    "video": "/studio/video",
    "image": "/studio/image",
    "game-forge": "/game-forge",
    "book": "/studio/writing",
    "voice": "/voice-house",
    "streaming": "/shared-skies",
}
PRIVATE_SURFACE_PREFIXES = ("/owner", "/agent", "/creator", "/command-center/level-up")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Active membership required")
    return str(user_id)


def _db_path() -> Path:
    configured = os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS shared_skies_live_sessions (
                id TEXT PRIMARY KEY,
                host_user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                creative_surface TEXT NOT NULL DEFAULT 'streaming',
                project_id TEXT,
                thumbnail_url TEXT NOT NULL DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'public',
                state TEXT NOT NULL DEFAULT 'live',
                viewer_count INTEGER NOT NULL DEFAULT 0,
                like_count INTEGER NOT NULL DEFAULT 0,
                share_count INTEGER NOT NULL DEFAULT 0,
                gift_coins INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY(host_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shared_skies_live_now
                ON shared_skies_live_sessions(state,visibility,started_at DESC);

            CREATE TABLE IF NOT EXISTS shared_skies_gifts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sender_user_id TEXT NOT NULL,
                recipient_user_id TEXT NOT NULL,
                gift_key TEXT NOT NULL,
                coins INTEGER NOT NULL CHECK(coins > 0),
                message TEXT NOT NULL DEFAULT '',
                reference TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES shared_skies_live_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(sender_user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(recipient_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shared_skies_gifts_session
                ON shared_skies_gifts(session_id,created_at DESC);

            CREATE TABLE IF NOT EXISTS shared_skies_battles (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                host_session_id TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'solo',
                scoring TEXT NOT NULL DEFAULT 'likes_and_gifts',
                state TEXT NOT NULL DEFAULT 'lobby',
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(host_session_id) REFERENCES shared_skies_live_sessions(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS shared_skies_battle_participants (
                battle_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                team INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL,
                PRIMARY KEY(battle_id,session_id),
                FOREIGN KEY(battle_id) REFERENCES shared_skies_battles(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES shared_skies_live_sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS shared_skies_chat_links (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                platform_id TEXT NOT NULL,
                label TEXT NOT NULL,
                chat_url TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'link',
                provider_authorised INTEGER NOT NULL DEFAULT 0,
                created_by_user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES shared_skies_live_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shared_skies_chat_links_session
                ON shared_skies_chat_links(session_id,created_at ASC);
            """
        )


_init_schema()


class StartLiveRequest(BaseModel):
    title: str = Field(default="Live Now", min_length=1, max_length=180)
    description: str = Field(default="", max_length=1500)
    creative_surface: Literal["music", "video", "image", "game-forge", "book", "voice", "streaming"] = "streaming"
    project_id: str | None = Field(default=None, max_length=160)
    thumbnail_url: str = Field(default="", max_length=1200)
    visibility: Literal["public", "unlisted"] = "public"


class EngagementRequest(BaseModel):
    action: Literal["like", "share", "viewer_join", "viewer_leave"]
    amount: int = Field(default=1, ge=1, le=1000)


class GiftRequest(BaseModel):
    gift_key: str = Field(default="star", min_length=1, max_length=80)
    coins: int = Field(gt=0, le=1_000_000)
    message: str = Field(default="", max_length=240)
    reference: str | None = Field(default=None, max_length=180)


class BattleCreateRequest(BaseModel):
    host_session_id: str = Field(min_length=1, max_length=80)
    title: str = Field(default="Shared Skies Battle", min_length=1, max_length=160)
    mode: Literal["solo", "teams"] = "solo"
    scoring: Literal["likes", "gifts", "likes_and_gifts"] = "likes_and_gifts"


class BattleJoinRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    team: int = Field(default=0, ge=0, le=2)


class BattleStateRequest(BaseModel):
    state: Literal["live", "ended"]


class ChatLinkRequest(BaseModel):
    platform_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    chat_url: str = Field(min_length=8, max_length=1200)
    mode: Literal["link", "read", "read_reply", "relay"] = "link"
    provider_authorised: bool = False


def _session(con: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM shared_skies_live_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "LIVE session not found")
    return row


def _owned_session(con: sqlite3.Connection, session_id: str, user_id: str) -> sqlite3.Row:
    row = _session(con, session_id)
    if row["host_user_id"] != user_id:
        raise HTTPException(403, "Only the LIVE host can change this session")
    return row


@router.get("/api/live-now")
def live_now_api(limit: int = 50):
    limit = max(1, min(int(limit), 100))
    with _connect() as con:
        rows = con.execute(
            """SELECT id,host_user_id,title,description,creative_surface,project_id,thumbnail_url,
                      viewer_count,like_count,share_count,gift_coins,started_at
               FROM shared_skies_live_sessions
               WHERE state='live' AND visibility='public'
               ORDER BY viewer_count DESC,gift_coins DESC,started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return {"product": PRODUCT_NAME, "live": [dict(row) for row in rows]}


@router.get("/live-now", response_class=HTMLResponse, include_in_schema=False)
def live_now_page():
    payload = live_now_api(50)
    cards = "".join(
        "<article class='live-card'>"
        + (f"<div class='thumb'><img src='{escape(row['thumbnail_url'], quote=True)}' alt=''></div>" if row["thumbnail_url"] else "<div class='thumb'><span>LIVE</span></div>")
        + f"<h3>{escape(row['title'])}</h3>"
        + f"<p>{escape(row['creative_surface'].replace('-', ' ').title())} · {row['viewer_count']} watching</p>"
        + f"<p>❤️ {row['like_count']} · ↗ {row['share_count']} · ✨ {row['gift_coins']} coins</p>"
        + f"<a href='/live/{escape(row['id'], quote=True)}'>Watch LIVE</a>"
        + "</article>"
        for row in payload["live"]
    ) or "<div class='empty'>Nobody is live right now. Start creating and be the first.</div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Live Now — {PRODUCT_NAME}</title><style>body{{margin:0;background:#061513;color:#effff9;font-family:Inter,system-ui,sans-serif}}"
        ".wrap{max-width:1200px;margin:auto;padding:24px}.hero{padding:16px 0 24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}"
        ".live-card{background:#0c2824;border:1px solid #ffffff20;border-radius:18px;padding:14px}.thumb{height:140px;border-radius:12px;background:linear-gradient(135deg,#0c614e,#7b3e2c);display:grid;place-items:center;overflow:hidden}.thumb img{width:100%;height:100%;object-fit:cover}.thumb span{font-weight:900;font-size:1.4rem}.live-card a{color:#9fffe1}.empty{padding:32px;background:#0c2824;border-radius:16px}</style></head>"
        f"<body><main class='wrap'><section class='hero'><h1>Live Now</h1><p>Watch creators building, playing, recording and producing across Shared Skies.</p></section><section class='grid'>{cards}</section></main></body></html>"
    )


@router.post("/api/live/start")
def start_live(payload: StartLiveRequest, request: Request):
    user_id = _member_user_id(request)
    session_id = uuid4().hex
    with _connect() as con:
        active = con.execute(
            "SELECT id FROM shared_skies_live_sessions WHERE host_user_id=? AND state='live' LIMIT 1",
            (user_id,),
        ).fetchone()
        if active:
            raise HTTPException(409, "This account already has an active Shared Skies LIVE session")
        con.execute(
            """INSERT INTO shared_skies_live_sessions
               (id,host_user_id,title,description,creative_surface,project_id,thumbnail_url,visibility,state,started_at)
               VALUES (?,?,?,?,?,?,?,?, 'live',?)""",
            (session_id,user_id,payload.title.strip(),payload.description.strip(),payload.creative_surface,payload.project_id,payload.thumbnail_url.strip(),payload.visibility,_now()),
        )
    return {"started": True, "session_id": session_id, "watch_url": f"/live/{session_id}", "live_now_url": "/live-now"}


@router.post("/api/live/{session_id}/stop")
def stop_live(session_id: str, request: Request):
    user_id = _member_user_id(request)
    with _connect() as con:
        _owned_session(con, session_id, user_id)
        con.execute("UPDATE shared_skies_live_sessions SET state='ended',ended_at=? WHERE id=?", (_now(), session_id))
    return {"stopped": True, "session_id": session_id}


@router.post("/api/live/{session_id}/engagement")
def live_engagement(session_id: str, payload: EngagementRequest):
    columns = {
        "like": ("like_count", payload.amount),
        "share": ("share_count", payload.amount),
        "viewer_join": ("viewer_count", payload.amount),
        "viewer_leave": ("viewer_count", -payload.amount),
    }
    column, delta = columns[payload.action]
    with _connect() as con:
        _session(con, session_id)
        con.execute(
            f"UPDATE shared_skies_live_sessions SET {column}=MAX(0,{column}+?) WHERE id=?",
            (delta, session_id),
        )
        row = con.execute(
            "SELECT viewer_count,like_count,share_count,gift_coins FROM shared_skies_live_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    return dict(row)


@router.post("/api/live/{session_id}/gifts")
def send_live_gift(session_id: str, payload: GiftRequest, request: Request):
    sender = _member_user_id(request)
    with _connect() as con:
        target = _session(con, session_id)
        recipient = str(target["host_user_id"])
    if sender == recipient:
        raise HTTPException(400, "You cannot send a LIVE gift to yourself")

    gift_id = uuid4().hex
    reference = (payload.reference or f"shared-skies-gift:{gift_id}").strip()
    try:
        debit = coin_wallet.spend(sender, payload.coins, reason=f"Shared Skies LIVE gift: {payload.gift_key}", reference=f"{reference}:debit", actor="member")
        try:
            credit = coin_wallet.grant(recipient, payload.coins, reason=f"Shared Skies LIVE gift received: {payload.gift_key}", actor="shared-skies-live", reference=f"{reference}:credit")
        except Exception:
            coin_wallet.adjust(sender, payload.coins, kind="refund", reason="Shared Skies LIVE gift rollback", actor="shared-skies-live", reference=f"{reference}:rollback")
            raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with _connect() as con:
        con.execute(
            """INSERT INTO shared_skies_gifts
               (id,session_id,sender_user_id,recipient_user_id,gift_key,coins,message,reference,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (gift_id,session_id,sender,recipient,payload.gift_key.strip(),payload.coins,payload.message.strip(),reference,_now()),
        )
        con.execute("UPDATE shared_skies_live_sessions SET gift_coins=gift_coins+? WHERE id=?", (payload.coins,session_id))
    return {"sent": True, "gift_id": gift_id, "coins": payload.coins, "sender_balance": debit["balance_after"], "recipient_balance": credit["balance_after"]}


@router.post("/api/live/battles")
def create_battle(payload: BattleCreateRequest, request: Request):
    user_id = _member_user_id(request)
    battle_id = uuid4().hex
    with _connect() as con:
        host = _owned_session(con, payload.host_session_id, user_id)
        if host["state"] != "live":
            raise HTTPException(409, "Host session must be live")
        now = _now()
        con.execute(
            "INSERT INTO shared_skies_battles (id,title,host_session_id,mode,scoring,state,created_at) VALUES (?,?,?,?,?,'lobby',?)",
            (battle_id,payload.title.strip(),payload.host_session_id,payload.mode,payload.scoring,now),
        )
        con.execute(
            "INSERT INTO shared_skies_battle_participants (battle_id,session_id,team,score,joined_at) VALUES (?,?,0,0,?)",
            (battle_id,payload.host_session_id,now),
        )
    return {"created": True, "battle_id": battle_id, "max_cohosts": MAX_COHOSTS}


@router.post("/api/live/battles/{battle_id}/join")
def join_battle(battle_id: str, payload: BattleJoinRequest, request: Request):
    user_id = _member_user_id(request)
    with _connect() as con:
        _owned_session(con, payload.session_id, user_id)
        battle = con.execute("SELECT * FROM shared_skies_battles WHERE id=?", (battle_id,)).fetchone()
        if not battle:
            raise HTTPException(404, "Battle not found")
        if battle["state"] != "lobby":
            raise HTTPException(409, "Battle is no longer accepting co-hosts")
        count = int(con.execute("SELECT COUNT(*) FROM shared_skies_battle_participants WHERE battle_id=?", (battle_id,)).fetchone()[0])
        if count >= MAX_COHOSTS:
            raise HTTPException(409, "Battle already has eight co-hosts")
        con.execute(
            "INSERT OR IGNORE INTO shared_skies_battle_participants (battle_id,session_id,team,score,joined_at) VALUES (?,?,?,?,?)",
            (battle_id,payload.session_id,payload.team,0,_now()),
        )
    return {"joined": True, "battle_id": battle_id, "session_id": payload.session_id}


@router.post("/api/live/battles/{battle_id}/state")
def set_battle_state(battle_id: str, payload: BattleStateRequest, request: Request):
    user_id = _member_user_id(request)
    with _connect() as con:
        battle = con.execute("SELECT * FROM shared_skies_battles WHERE id=?", (battle_id,)).fetchone()
        if not battle:
            raise HTTPException(404, "Battle not found")
        _owned_session(con, battle["host_session_id"], user_id)
        stamp = _now()
        if payload.state == "live":
            con.execute("UPDATE shared_skies_battles SET state='live',started_at=? WHERE id=?", (stamp,battle_id))
        else:
            con.execute("UPDATE shared_skies_battles SET state='ended',ended_at=? WHERE id=?", (stamp,battle_id))
    return {"updated": True, "battle_id": battle_id, "state": payload.state}


@router.get("/api/live/battles/{battle_id}")
def battle_state(battle_id: str):
    with _connect() as con:
        battle = con.execute("SELECT * FROM shared_skies_battles WHERE id=?", (battle_id,)).fetchone()
        if not battle:
            raise HTTPException(404, "Battle not found")
        participants = con.execute(
            """SELECT p.session_id,p.team,p.score,s.title,s.host_user_id,s.like_count,s.gift_coins
               FROM shared_skies_battle_participants p JOIN shared_skies_live_sessions s ON s.id=p.session_id
               WHERE p.battle_id=? ORDER BY p.team,p.score DESC,p.joined_at""",
            (battle_id,),
        ).fetchall()
        result = []
        for row in participants:
            item = dict(row)
            if battle["scoring"] == "likes":
                item["score"] = int(row["like_count"])
            elif battle["scoring"] == "gifts":
                item["score"] = int(row["gift_coins"])
            else:
                item["score"] = int(row["like_count"]) + int(row["gift_coins"])
            result.append(item)
    return {"battle": dict(battle), "participants": result, "max_cohosts": MAX_COHOSTS}


@router.post("/api/live/{session_id}/chat-links")
def add_chat_link(session_id: str, payload: ChatLinkRequest, request: Request):
    user_id = _member_user_id(request)
    url = payload.chat_url.strip()
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Chat link must use HTTP or HTTPS")
    if payload.mode != "link" and not payload.provider_authorised:
        raise HTTPException(400, "Read/reply/relay modes require an authorised provider integration")
    with _connect() as con:
        _owned_session(con, session_id, user_id)
        link_id = uuid4().hex
        con.execute(
            """INSERT INTO shared_skies_chat_links
               (id,session_id,platform_id,label,chat_url,mode,provider_authorised,created_by_user_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (link_id,session_id,payload.platform_id.strip(),payload.label.strip(),url,payload.mode,1 if payload.provider_authorised else 0,user_id,_now()),
        )
    return {"created": True, "link_id": link_id}


@router.get("/api/live/{session_id}/chat-links")
def list_chat_links(session_id: str):
    with _connect() as con:
        _session(con, session_id)
        rows = con.execute(
            "SELECT id,platform_id,label,chat_url,mode,provider_authorised,created_at FROM shared_skies_chat_links WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return {"session_id": session_id, "links": [dict(row) for row in rows]}


@router.get("/api/shared-skies/go-live-and-create/{surface}")
def go_live_and_create(surface: str):
    key = surface.strip().lower()
    studio_url = CREATIVE_SURFACES.get(key)
    if not studio_url:
        raise HTTPException(404, "This creative surface is not enabled for Go Live & Create")
    if studio_url.startswith(PRIVATE_SURFACE_PREFIXES):
        raise HTTPException(403, "Private ESP/Owner surfaces cannot be broadcast through Go Live & Create")
    return {
        "surface": key,
        "studio_url": studio_url,
        "start_live_api": "/api/live/start",
        "live_now_url": "/live-now",
        "capabilities": ["create_while_live", "multistream", "cohost_up_to_8", "battles", "likes", "shares", "cosmic_creation_coin_gifts", "authorised_cross_platform_chat"],
        "provider_boundary": "External destinations and chat actions only activate when the provider officially authorises the connection.",
    }


def install_shared_skies_live_network(app) -> None:
    existing = {
        (getattr(route, "path", ""), frozenset(getattr(route, "methods", set()) or set()))
        for route in app.router.routes
    }
    for route in router.routes:
        signature = (
            getattr(route, "path", ""),
            frozenset(getattr(route, "methods", set()) or set()),
        )
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = ["PRODUCT_NAME", "MAX_COHOSTS", "CREATIVE_SURFACES", "install_shared_skies_live_network", "router"]
