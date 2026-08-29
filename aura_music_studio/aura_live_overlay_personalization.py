from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["Aura LIVE Personalisation"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            CREATE TABLE IF NOT EXISTS live_overlay_gift_reactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                gift_name TEXT NOT NULL,
                min_count INTEGER NOT NULL DEFAULT 1,
                visual TEXT NOT NULL DEFAULT 'gift_cannon',
                media_id TEXT,
                sound_media_id TEXT,
                tts_template TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id,gift_name,min_count)
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_gift_reactions_user
              ON live_overlay_gift_reactions(user_id,enabled,gift_name);
            CREATE TABLE IF NOT EXISTS live_overlay_gift_streaks (
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                last_gift_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,username)
            );
            CREATE TABLE IF NOT EXISTS live_overlay_rotators (
                user_id TEXT PRIMARY KEY,
                interval_seconds INTEGER NOT NULL DEFAULT 12,
                panels_json TEXT NOT NULL DEFAULT '["leaderboard","goals","stats","announcements"]',
                enabled INTEGER NOT NULL DEFAULT 1,
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


def _user_for_source(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as con:
        row = con.execute("SELECT user_id FROM live_overlay_profiles WHERE source_token_hash=?", (digest,)).fetchone()
    if not row:
        raise HTTPException(404, "Overlay source not found")
    return str(row["user_id"])


class GiftReactionCreate(BaseModel):
    gift_name: str = Field(min_length=1, max_length=100)
    min_count: int = Field(default=1, ge=1, le=99999)
    visual: str = Field(default="gift_cannon", pattern="^(gift_cannon|alert|confetti|spotlight|media|none)$")
    media_id: str | None = Field(default=None, max_length=80)
    sound_media_id: str | None = Field(default=None, max_length=80)
    tts_template: str | None = Field(default=None, max_length=300)
    enabled: bool = True


class RotatorUpdate(BaseModel):
    interval_seconds: int = Field(default=12, ge=4, le=120)
    panels: list[str] = Field(default_factory=lambda: ["leaderboard", "goals", "stats", "announcements"], min_length=1, max_length=10)
    enabled: bool = True


@router.get("/api/live-overlays/gift-reactions")
def list_gift_reactions(request: Request):
    member = _member(request)
    with _connect() as con:
        rows = con.execute("SELECT * FROM live_overlay_gift_reactions WHERE user_id=? ORDER BY gift_name,min_count DESC", (member.user_id,)).fetchall()
    return {"reactions": [{**dict(r), "enabled": bool(r["enabled"])} for r in rows], "gift_catalog_connected": False, "catalog_note": "Gift names are user-entered until a trusted provider supplies current TikTok gift metadata/icons/costs."}


@router.post("/api/live-overlays/gift-reactions")
def upsert_gift_reaction(body: GiftReactionCreate, request: Request):
    member = _member(request)
    if member.plan.id == "free" and (body.media_id or body.sound_media_id or body.visual == "media"):
        raise HTTPException(403, "Custom per-gift media reactions require Basic or Pro")
    if body.visual == "media" and not body.media_id:
        raise HTTPException(400, "Media visual requires an approved overlay media ID")
    rid = secrets.token_urlsafe(12)
    with _connect() as con:
        if body.media_id and not con.execute("SELECT 1 FROM live_overlay_media WHERE id=? AND user_id=?", (body.media_id, member.user_id)).fetchone():
            raise HTTPException(400, "Visual media does not belong to this member")
        if body.sound_media_id and not con.execute("SELECT 1 FROM live_overlay_media WHERE id=? AND user_id=?", (body.sound_media_id, member.user_id)).fetchone():
            raise HTTPException(400, "Sound media does not belong to this member")
        existing = con.execute("SELECT id FROM live_overlay_gift_reactions WHERE user_id=? AND lower(gift_name)=lower(?) AND min_count=?", (member.user_id, body.gift_name.strip(), body.min_count)).fetchone()
        rid = str(existing["id"]) if existing else rid
        con.execute(
            """
            INSERT INTO live_overlay_gift_reactions(id,user_id,gift_name,min_count,visual,media_id,sound_media_id,tts_template,enabled,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET gift_name=excluded.gift_name,min_count=excluded.min_count,visual=excluded.visual,media_id=excluded.media_id,sound_media_id=excluded.sound_media_id,tts_template=excluded.tts_template,enabled=excluded.enabled,updated_at=excluded.updated_at
            """,
            (rid, member.user_id, body.gift_name.strip(), body.min_count, body.visual, body.media_id, body.sound_media_id, body.tts_template, int(body.enabled), _now(), _now()),
        )
    return {"saved": True, "reaction_id": rid}


@router.delete("/api/live-overlays/gift-reactions/{reaction_id}")
def delete_gift_reaction(reaction_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        cur = con.execute("DELETE FROM live_overlay_gift_reactions WHERE id=? AND user_id=?", (reaction_id, member.user_id))
    if not cur.rowcount:
        raise HTTPException(404, "Gift reaction not found")
    return {"deleted": True}


@router.get("/api/live-overlays/top-streak")
def top_streak(request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute("SELECT username,current_streak,best_streak,last_gift_at FROM live_overlay_gift_streaks WHERE user_id=? ORDER BY current_streak DESC,best_streak DESC,updated_at DESC LIMIT 1", (member.user_id,)).fetchone()
    return {"top_streak": dict(row) if row else None}


@router.get("/api/live-overlays/rotator")
def rotator_settings(request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_rotators WHERE user_id=?", (member.user_id,)).fetchone()
    if not row:
        return {"interval_seconds": 12, "panels": ["leaderboard", "goals", "stats", "announcements"], "enabled": True}
    return {"interval_seconds": row["interval_seconds"], "panels": json.loads(row["panels_json"]), "enabled": bool(row["enabled"])}


@router.put("/api/live-overlays/rotator")
def update_rotator(body: RotatorUpdate, request: Request):
    member = _member(request)
    if member.plan.id == "free" and len(body.panels) > 2:
        raise HTTPException(403, "Free rotators support up to two panels")
    allowed = {"leaderboard", "goals", "stats", "announcements", "timer", "challenges", "auction", "streak"}
    panels = list(dict.fromkeys(x for x in body.panels if x in allowed))
    if not panels:
        raise HTTPException(400, "Choose at least one supported panel")
    with _connect() as con:
        con.execute("INSERT INTO live_overlay_rotators(user_id,interval_seconds,panels_json,enabled,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET interval_seconds=excluded.interval_seconds,panels_json=excluded.panels_json,enabled=excluded.enabled,updated_at=excluded.updated_at", (member.user_id, body.interval_seconds, json.dumps(panels), int(body.enabled), _now()))
    return {"saved": True, "interval_seconds": body.interval_seconds, "panels": panels, "enabled": body.enabled}


@router.get("/live-overlay/streak/{token}", response_class=HTMLResponse, include_in_schema=False)
def streak_source(token: str):
    _user_for_source(token)
    t = json.dumps(token)
    return HTMLResponse(r"""<!doctype html><html><head><meta charset='utf-8'><meta name='robots' content='noindex,nofollow'><style>html,body{margin:0;background:transparent;color:#fff;font:700 26px Inter,Arial}.box{display:inline-flex;gap:12px;align-items:center;background:#090b17dd;border:1px solid #ffffff33;border-radius:18px;padding:14px 18px}.fire{font-size:34px}</style></head><body><div class='box'><span class='fire'>🔥</span><span id='v'>Waiting for a gift streak…</span></div><script>const T=__TOKEN__;async function tick(){try{let s=await fetch('/live-overlay/advanced/'+encodeURIComponent(T)+'/state',{cache:'no-store'});if(s.ok){let st=await fetch('/live-overlay/streak/'+encodeURIComponent(T)+'/data',{cache:'no-store'}),d=await st.json();document.getElementById('v').textContent=d.top_streak?`${d.top_streak.username} · ${d.top_streak.current_streak} gift streak`:'Waiting for a gift streak…'}}catch{}setTimeout(tick,1500)}tick()</script></body></html>""".replace("__TOKEN__", t), headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/live-overlay/streak/{token}/data", include_in_schema=False)
def streak_source_data(token: str):
    user_id = _user_for_source(token)
    with _connect() as con:
        row = con.execute("SELECT username,current_streak,best_streak,last_gift_at FROM live_overlay_gift_streaks WHERE user_id=? ORDER BY current_streak DESC,best_streak DESC LIMIT 1", (user_id,)).fetchone()
    return {"top_streak": dict(row) if row else None}


@router.get("/live-overlay/rotator/{token}", response_class=HTMLResponse, include_in_schema=False)
def rotator_source(token: str):
    _user_for_source(token)
    t = json.dumps(token)
    return HTMLResponse(r"""<!doctype html><html><head><meta charset='utf-8'><meta name='robots' content='noindex,nofollow'><style>html,body{margin:0;background:transparent;color:#fff;font-family:Inter,Arial}.box{background:#090b17df;border:1px solid #ffffff30;border-radius:18px;padding:16px;min-height:90px}.title{color:#efc96b;font-weight:900}.muted{color:#b7c0d3}</style></head><body><div class='box'><div id='title' class='title'>Aura LIVE</div><div id='body' class='muted'>Loading…</div></div><script>const T=__TOKEN__;let i=0,cfg={interval_seconds:12,panels:['leaderboard','goals','stats','announcements']},state={};function text(x){return String(x??'').slice(0,300)}async function refresh(){try{let a=await fetch('/live-overlay/rotator/'+encodeURIComponent(T)+'/config',{cache:'no-store'}),b=await fetch('/live-overlay/advanced/'+encodeURIComponent(T)+'/state',{cache:'no-store'});if(a.ok)cfg=await a.json();if(b.ok)state=await b.json()}catch{}}function render(){let p=cfg.panels[i++%cfg.panels.length],title=document.getElementById('title'),body=document.getElementById('body');title.textContent=p.replaceAll('_',' ').toUpperCase();if(p==='leaderboard')body.textContent=(state.leaderboards||[]).slice(0,5).map((x,n)=>`${n+1}. ${text(x.username)} · ${text(x.gift_value)}`).join('   ')||'Waiting for supporters';else if(p==='goals')body.textContent=(state.goals||[]).slice(0,3).map(x=>`${text(x.name)} ${text(x.current)}/${text(x.target)}`).join(' · ')||'No active goals';else if(p==='announcements')body.textContent=(state.announcements||[]).map(x=>text(x.text)).join(' ✦ ')||'No announcements';else if(p==='timer')body.textContent=state.timer?`${text(state.timer.name)} · ${text(state.timer.remaining_seconds)} seconds`:'No active timer';else if(p==='challenges')body.textContent=(state.challenges||[]).map(x=>`${text(x.name)} ${text(x.current)}/${text(x.target)}`).join(' · ')||'No challenges';else if(p==='auction')body.textContent=state.auction?.active?`${text(state.auction.title)} · ${text(state.auction.leader_username||'No leader')} ${text(state.auction.leader_value||0)}`:'No active auction';else body.textContent='Aura LIVE overlay active'}async function loop(){await refresh();render();setTimeout(loop,Math.max(4000,Number(cfg.interval_seconds||12)*1000))}loop()</script></body></html>""".replace("__TOKEN__", t), headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/live-overlay/rotator/{token}/config", include_in_schema=False)
def rotator_source_config(token: str):
    user_id = _user_for_source(token)
    with _connect() as con:
        row = con.execute("SELECT interval_seconds,panels_json,enabled FROM live_overlay_rotators WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {"interval_seconds": 12, "panels": ["leaderboard", "goals", "stats", "announcements"], "enabled": True}
    return {"interval_seconds": row["interval_seconds"], "panels": json.loads(row["panels_json"]), "enabled": bool(row["enabled"])}
