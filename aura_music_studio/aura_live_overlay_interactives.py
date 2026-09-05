from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .aura_live_overlay_effect_packs import router as live_overlay_effect_packs_router
from .aura_live_overlay_effects import router as live_overlay_effects_router
from .aura_live_overlay_unified_source import router as live_overlay_unified_source_router

router = APIRouter(tags=["Aura LIVE Interactive Overlays"])
router.include_router(live_overlay_effects_router)
router.include_router(live_overlay_effect_packs_router)
router.include_router(live_overlay_unified_source_router)
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_timers (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT 'LIVE Timer',
                ends_at TEXT,
                remaining_seconds INTEGER NOT NULL DEFAULT 0,
                running INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_wheels (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                items_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_challenges (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                gift_name TEXT,
                target REAL NOT NULL,
                current REAL NOT NULL DEFAULT 0,
                reward_text TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_auction (
                user_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'LIVE Auction',
                active INTEGER NOT NULL DEFAULT 0,
                minimum_bid REAL NOT NULL DEFAULT 0,
                leader_username TEXT,
                leader_value REAL NOT NULL DEFAULT 0,
                ends_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_announcements (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_requests (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                requester TEXT NOT NULL,
                request_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _require_paid(member) -> None:
    if member.plan.id == "free":
        raise HTTPException(403, "Interactive LIVE tools require Basic or Pro")


class TimerSet(BaseModel):
    name: str = Field(default="LIVE Timer", min_length=1, max_length=80)
    seconds: int = Field(ge=0, le=60 * 60 * 24 * 30)
    running: bool = True


class TimerAdd(BaseModel):
    seconds: int = Field(ge=-86400, le=86400)


class WheelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    items: list[str] = Field(min_length=2, max_length=100)


class ChallengeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    event_type: str = Field(pattern="^(gift|like|share|follow|subscribe)$")
    gift_name: str | None = Field(default=None, max_length=80)
    target: float = Field(gt=0, le=1000000000)
    reward_text: str | None = Field(default=None, max_length=300)


class AuctionPatch(BaseModel):
    title: str = Field(default="LIVE Auction", min_length=1, max_length=100)
    active: bool = True
    minimum_bid: float = Field(default=0, ge=0, le=1000000000)
    duration_seconds: int | None = Field(default=None, ge=10, le=86400)


class AuctionBid(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    value: float = Field(gt=0, le=1000000000)


class AnnouncementCreate(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    priority: int = Field(default=0, ge=-100, le=100)


class RequestCreate(BaseModel):
    requester: str = Field(min_length=1, max_length=80)
    request_text: str = Field(min_length=1, max_length=300)


class RequestStatus(BaseModel):
    status: str = Field(pattern="^(pending|approved|playing|played|rejected)$")


@router.get("/api/live-overlays/timer")
def timer(request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_timers WHERE user_id=?", (member.user_id,)).fetchone()
    if not row:
        return {"timer": {"name": "LIVE Timer", "remaining_seconds": 0, "running": False}}
    d = dict(row)
    if d["running"] and d["ends_at"]:
        remaining = max(0, int((datetime.fromisoformat(d["ends_at"]) - _now_dt()).total_seconds()))
        d["remaining_seconds"] = remaining
        if remaining == 0:
            d["running"] = 0
    d["running"] = bool(d["running"])
    return {"timer": d}


@router.put("/api/live-overlays/timer")
def set_timer(body: TimerSet, request: Request):
    member = _member(request); _require_paid(member)
    ends = (_now_dt() + timedelta(seconds=body.seconds)).isoformat() if body.running else None
    with _connect() as con:
        con.execute("INSERT INTO live_overlay_timers(user_id,name,ends_at,remaining_seconds,running,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,ends_at=excluded.ends_at,remaining_seconds=excluded.remaining_seconds,running=excluded.running,updated_at=excluded.updated_at", (member.user_id, body.name, ends, body.seconds, int(body.running), _now()))
    return timer(request)


@router.post("/api/live-overlays/timer/add")
def add_timer(body: TimerAdd, request: Request):
    member = _member(request); _require_paid(member)
    current = timer(request)["timer"]
    seconds = max(0, int(current.get("remaining_seconds", 0)) + body.seconds)
    return set_timer(TimerSet(name=current.get("name", "LIVE Timer"), seconds=seconds, running=seconds > 0), request)


@router.get("/api/live-overlays/wheels")
def wheels(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT * FROM live_overlay_wheels WHERE user_id=? ORDER BY updated_at DESC", (member.user_id,)).fetchall()
    return {"wheels": [{**dict(r), "items": json.loads(r["items_json"]), "enabled": bool(r["enabled"])} for r in rows]}


@router.post("/api/live-overlays/wheels")
def create_wheel(body: WheelCreate, request: Request):
    member = _member(request); _require_paid(member)
    items = [x.strip()[:100] for x in body.items if x.strip()]
    if len(items) < 2: raise HTTPException(400, "A wheel needs at least two non-empty items")
    wid = secrets.token_urlsafe(12)
    with _connect() as con: con.execute("INSERT INTO live_overlay_wheels(id,user_id,name,items_json,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)", (wid, member.user_id, body.name, json.dumps(items), _now(), _now()))
    return {"created": True, "wheel_id": wid}


@router.post("/api/live-overlays/wheels/{wheel_id}/spin")
def spin_wheel(wheel_id: str, request: Request):
    member = _member(request); _require_paid(member)
    with _connect() as con: row = con.execute("SELECT items_json FROM live_overlay_wheels WHERE id=? AND user_id=? AND enabled=1", (wheel_id, member.user_id)).fetchone()
    if not row: raise HTTPException(404, "Wheel not found")
    items = json.loads(row["items_json"]); index = secrets.randbelow(len(items))
    return {"wheel_id": wheel_id, "selected_index": index, "selected": items[index], "cryptographic_random_selection": True}


@router.get("/api/live-overlays/challenges")
def challenges(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT * FROM live_overlay_challenges WHERE user_id=? ORDER BY updated_at DESC", (member.user_id,)).fetchall()
    return {"challenges": [{**dict(r), "enabled": bool(r["enabled"]), "progress": min(1.0, float(r["current"])/float(r["target"]))} for r in rows]}


@router.post("/api/live-overlays/challenges")
def create_challenge(body: ChallengeCreate, request: Request):
    member = _member(request); _require_paid(member)
    if body.event_type == "gift" and not body.gift_name: raise HTTPException(400, "Gift challenges require a gift name")
    cid = secrets.token_urlsafe(12)
    with _connect() as con: con.execute("INSERT INTO live_overlay_challenges(id,user_id,name,event_type,gift_name,target,current,reward_text,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,1,?,?)", (cid, member.user_id, body.name, body.event_type, body.gift_name, body.target, body.reward_text, _now(), _now()))
    return {"created": True, "challenge_id": cid}


@router.get("/api/live-overlays/auction")
def auction(request: Request):
    member = _member(request)
    with _connect() as con: row = con.execute("SELECT * FROM live_overlay_auction WHERE user_id=?", (member.user_id,)).fetchone()
    return {"auction": ({**dict(row), "active": bool(row["active"])} if row else {"active": False, "title": "LIVE Auction", "leader_value": 0})}


@router.put("/api/live-overlays/auction")
def set_auction(body: AuctionPatch, request: Request):
    member = _member(request); _require_paid(member)
    ends = (_now_dt()+timedelta(seconds=body.duration_seconds)).isoformat() if body.duration_seconds and body.active else None
    with _connect() as con: con.execute("INSERT INTO live_overlay_auction(user_id,title,active,minimum_bid,leader_username,leader_value,ends_at,updated_at) VALUES(?,?,?,?,NULL,0,?,?) ON CONFLICT(user_id) DO UPDATE SET title=excluded.title,active=excluded.active,minimum_bid=excluded.minimum_bid,leader_username=NULL,leader_value=0,ends_at=excluded.ends_at,updated_at=excluded.updated_at", (member.user_id, body.title, int(body.active), body.minimum_bid, ends, _now()))
    return auction(request)


@router.post("/api/live-overlays/auction/bid")
def auction_bid(body: AuctionBid, request: Request):
    member = _member(request); _require_paid(member)
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_auction WHERE user_id=?", (member.user_id,)).fetchone()
        if not row or not row["active"]: raise HTTPException(409, "Auction is not active")
        if row["ends_at"] and datetime.fromisoformat(row["ends_at"]) <= _now_dt(): raise HTTPException(409, "Auction has ended")
        needed = max(float(row["minimum_bid"]), float(row["leader_value"]) + 0.000001)
        if body.value < needed: raise HTTPException(409, "Bid must exceed the current leader and meet the minimum")
        con.execute("UPDATE live_overlay_auction SET leader_username=?,leader_value=?,updated_at=? WHERE user_id=?", (body.username, body.value, _now(), member.user_id))
    return auction(request)


@router.get("/api/live-overlays/announcements")
def announcements(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT * FROM live_overlay_announcements WHERE user_id=? AND enabled=1 ORDER BY priority DESC,created_at", (member.user_id,)).fetchall()
    return {"announcements": [dict(r) for r in rows]}


@router.post("/api/live-overlays/announcements")
def create_announcement(body: AnnouncementCreate, request: Request):
    member = _member(request); _require_paid(member); aid = secrets.token_urlsafe(12)
    with _connect() as con: con.execute("INSERT INTO live_overlay_announcements(id,user_id,text,priority,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)", (aid, member.user_id, body.text, body.priority, _now(), _now()))
    return {"created": True, "announcement_id": aid}


@router.get("/api/live-overlays/requests")
def requests_queue(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT * FROM live_overlay_requests WHERE user_id=? ORDER BY created_at", (member.user_id,)).fetchall()
    return {"requests": [dict(r) for r in rows], "spotify_connected": False, "note": "This is a safe host-managed request queue. Spotify playback requires separate authorized provider integration."}


@router.post("/api/live-overlays/requests")
def create_request(body: RequestCreate, request: Request):
    member = _member(request); _require_paid(member); rid = secrets.token_urlsafe(12)
    with _connect() as con:
        pending = con.execute("SELECT COUNT(*) FROM live_overlay_requests WHERE user_id=? AND status IN ('pending','approved','playing')", (member.user_id,)).fetchone()[0]
        if pending >= 200: raise HTTPException(429, "Request queue is full")
        con.execute("INSERT INTO live_overlay_requests(id,user_id,requester,request_text,status,created_at,updated_at) VALUES(?,?,?,?, 'pending',?,?)", (rid, member.user_id, body.requester, body.request_text, _now(), _now()))
    return {"created": True, "request_id": rid}


@router.patch("/api/live-overlays/requests/{request_id}")
def set_request_status(request_id: str, body: RequestStatus, request: Request):
    member = _member(request); _require_paid(member)
    with _connect() as con:
        cur = con.execute("UPDATE live_overlay_requests SET status=?,updated_at=? WHERE id=? AND user_id=?", (body.status, _now(), request_id, member.user_id))
        if not cur.rowcount: raise HTTPException(404, "Request not found")
    return {"updated": True, "status": body.status}
