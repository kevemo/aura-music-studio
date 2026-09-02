from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

router = APIRouter(tags=["Aura LIVE Advanced Source"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
MEDIA_ROOT = Path(os.getenv("AURA_LIVE_OVERLAY_MEDIA_ROOT", "data/aura_live_overlay_media"))
TOKEN_BYTES = 32


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_for_token(token: str) -> str:
    with _connect() as con:
        row = con.execute(
            "SELECT user_id FROM live_overlay_profiles WHERE source_token_hash=?",
            (_hash(token),),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Overlay source not found")
    return str(row["user_id"])


def _safe_json(value: str | None, default):
    try:
        parsed = json.loads(value or "")
        return parsed
    except Exception:
        return default


def _profile(user_id: str) -> dict:
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Overlay profile not found")
    data = dict(row)
    for key in (
        "gift_sound_muted", "all_audio_muted", "tts_chat_enabled", "tts_gifts_enabled",
        "welcome_enabled", "welcome_once", "profanity_filter",
    ):
        data[key] = bool(data.get(key))
    data["active_widgets"] = _safe_json(data.pop("active_widgets_json", "[]"), [])
    data.pop("source_token_hash", None)
    return data


def _optional_rows(con: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    try:
        return [dict(row) for row in con.execute(query, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def _optional_row(con: sqlite3.Connection, query: str, params: tuple = ()) -> dict | None:
    try:
        row = con.execute(query, params).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def _state(user_id: str) -> dict:
    with _connect() as con:
        scene = _optional_row(
            con,
            "SELECT * FROM live_overlay_scenes WHERE user_id=? ORDER BY active DESC,updated_at DESC LIMIT 1",
            (user_id,),
        )
        goals = _optional_rows(
            con,
            "SELECT id,name,metric,target,current,enabled FROM live_overlay_goals WHERE user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 12",
            (user_id,),
        )
        leaders = _optional_rows(
            con,
            "SELECT username,gift_count,gift_value,likes,shares,comments FROM live_overlay_session_stats WHERE user_id=? ORDER BY gift_value DESC,likes DESC LIMIT 20",
            (user_id,),
        )
        announcements = _optional_rows(
            con,
            "SELECT id,text,priority FROM live_overlay_announcements WHERE user_id=? AND enabled=1 ORDER BY priority DESC,created_at LIMIT 20",
            (user_id,),
        )
        timer = _optional_row(con, "SELECT * FROM live_overlay_timers WHERE user_id=?", (user_id,))
        auction = _optional_row(con, "SELECT * FROM live_overlay_auction WHERE user_id=?", (user_id,))
        challenges = _optional_rows(
            con,
            "SELECT id,name,event_type,gift_name,target,current,reward_text FROM live_overlay_challenges WHERE user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 10",
            (user_id,),
        )
    layout = _safe_json(scene.get("layout_json") if scene else None, [])
    if scene:
        scene = {
            "id": scene.get("id"),
            "name": scene.get("name"),
            "width": scene.get("width"),
            "height": scene.get("height"),
            "layout": layout,
        }
    return {
        "scene": scene,
        "goals": goals,
        "leaderboards": leaders,
        "announcements": announcements,
        "timer": timer,
        "auction": auction,
        "challenges": challenges,
    }


@router.get("/live-overlay-studio/advanced-source", response_class=HTMLResponse, include_in_schema=False)
def source_setup(request: Request):
    member = _member(request)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Advanced Source</title><style>body{{margin:0;background:#080a13;color:#fff;font-family:Inter,system-ui}}main{{max-width:900px;margin:auto;padding:38px}}.card{{background:#141929;border:1px solid #ffffff20;border-radius:18px;padding:20px;margin:14px 0}}button{{background:linear-gradient(120deg,#efc96b,#9b72ff);border:0;border-radius:10px;padding:12px 16px;font-weight:900;cursor:pointer}}code{{word-break:break-all;color:#efc96b}}.muted{{color:#b5bfd3}}</style></head><body><main><a href='/live-overlay-studio' style='color:white'>← LIVE control room</a><h1>Aura LIVE Advanced Single Source</h1><p class='muted'>One private browser/link source renders the active scene, goal bars, supporter leaderboard, announcements and bounded event reactions. It consumes only Aura's normalized LIVE event stream and does not provide arbitrary script execution.</p><div class='card'><b>{escape(member.plan.name)} tier</b><p class='muted'>The source URL is a bearer secret. Rotating it invalidates the previous basic and advanced source URLs for this account.</p><button id='rotate'>Generate / rotate private source</button><p id='url'><code>Press the button to reveal a new private URL.</code></p></div><div class='card'><h2>Built-in reactions</h2><p class='muted'>Gift Cannon, Like Fountain, supporter cards, scene widgets, goals and leaderboard state are rendered inside one source. Overlay audio continues to obey the existing gift-sound and global-audio mute controls.</p><p><a style='color:white' href='/live-overlay-studio/editor'>Open visual designer</a></p></div></main><script>document.getElementById('rotate').onclick=async()=>{{const r=await fetch('/api/live-overlays/advanced-source/rotate',{{method:'POST'}});const d=await r.json();if(!r.ok)return alert(d.detail||'Unable to rotate');document.getElementById('url').innerHTML='<code>'+d.source_url.replaceAll('&','&amp;').replaceAll('<','&lt;')+'</code>'}}</script></body></html>""",
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.post("/api/live-overlays/advanced-source/rotate")
def rotate_advanced_source(request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    with _connect() as con:
        row = con.execute("SELECT user_id FROM live_overlay_profiles WHERE user_id=?", (member.user_id,)).fetchone()
        if not row:
            raise HTTPException(409, "Open Aura LIVE Overlay Studio once before generating a source URL")
        con.execute(
            "UPDATE live_overlay_profiles SET source_token_hash=?,updated_at=datetime('now') WHERE user_id=?",
            (_hash(raw), member.user_id),
        )
    base = str(request.base_url).rstrip("/")
    return JSONResponse(
        {"source_url": f"{base}/live-overlay/source/advanced/{quote(raw, safe='')}", "rotated": True},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay/source/advanced/{token}/state", include_in_schema=False)
def source_state(token: str):
    user_id = _user_for_token(token)
    return JSONResponse(
        {"profile": _profile(user_id), **_state(user_id)},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay/source/advanced/{token}/events", include_in_schema=False)
def source_events(token: str, after: int = 0):
    user_id = _user_for_token(token)
    after = max(0, min(int(after), 2_147_483_647))
    with _connect() as con:
        rows = con.execute(
            "SELECT id,event_type,payload_json,created_at FROM live_overlay_events WHERE user_id=? AND id>? ORDER BY id ASC LIMIT 100",
            (user_id, after),
        ).fetchall()
    events = [
        {
            "id": int(row["id"]),
            "event_type": str(row["event_type"]),
            "payload": _safe_json(row["payload_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return JSONResponse({"events": events}, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/live-overlay/source/advanced/{token}/media/{media_id}", include_in_schema=False)
def source_media(token: str, media_id: str):
    user_id = _user_for_token(token)
    with _connect() as con:
        row = con.execute(
            "SELECT stored_name,mime_type FROM live_overlay_media WHERE id=? AND user_id=?",
            (media_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Media not found")
    root = (MEDIA_ROOT / hashlib.sha256(user_id.encode()).hexdigest()[:24]).resolve()
    target = (root / str(row["stored_name"])).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "Media not found")
    return FileResponse(
        target,
        media_type=str(row["mime_type"]),
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/live-overlay/source/advanced/{token}", response_class=HTMLResponse, include_in_schema=False)
def advanced_source(token: str):
    _user_for_token(token)
    token_js = json.dumps(token)
    html = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><style>html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:Inter,Arial,sans-serif;color:#fff}#scene{position:absolute;inset:0}.w{position:absolute;box-sizing:border-box}.alert{left:50%;top:12%;transform:translate(-50%,-20px) scale(.95);opacity:0;padding:18px 25px;border-radius:22px;background:linear-gradient(135deg,#180f2ded,#0b2838ed);border:1px solid #ffffff35;box-shadow:0 15px 60px #0009;text-align:center;transition:.3s;max-width:82%}.alert.show{opacity:1;transform:translate(-50%,0) scale(1)}.goal,.board,.ticker{background:#080a13df;border:1px solid #ffffff2b;border-radius:14px;padding:12px}.particle{position:absolute;pointer-events:none;animation:fly 2.4s ease-out forwards;font-weight:900;text-shadow:0 2px 8px #000}.heart{color:#ff5a8c;font-size:28px}.giftp{font-size:16px;background:#160f2ddd;border:1px solid #ffffff28;border-radius:999px;padding:6px 10px}@keyframes fly{0%{opacity:0;transform:translateY(40px) scale(.7)}20%{opacity:1}100%{opacity:0;transform:translate(var(--dx),-420px) rotate(var(--rot)) scale(1.2)}}</style></head><body><div id='scene'></div><div id='alert' class='w alert'></div><script>'use strict';const TOKEN=__TOKEN__;let after=0,state={};const scene=document.getElementById('scene'),alertBox=document.getElementById('alert');const clean=v=>String(v??'').slice(0,500);function show(t){alertBox.textContent=clean(t);alertBox.classList.add('show');setTimeout(()=>alertBox.classList.remove('show'),3500)}function giftCannon(p){for(let i=0;i<Math.min(10,Math.max(2,Number(p.gift_count||1)+1));i++){let e=document.createElement('div');e.className='particle giftp';e.textContent=`${clean(p.username||'Viewer')} · ${clean(p.gift_name||'Gift')}`;e.style.left=(15+Math.random()*70)+'%';e.style.bottom='5%';e.style.setProperty('--dx',(Math.random()*300-150)+'px');e.style.setProperty('--rot',(Math.random()*100-50)+'deg');document.body.appendChild(e);setTimeout(()=>e.remove(),2600)}}function likeFountain(){for(let i=0;i<8;i++){let e=document.createElement('div');e.className='particle heart';e.textContent='♥';e.style.left=(5+Math.random()*90)+'%';e.style.bottom='0';e.style.setProperty('--dx',(Math.random()*240-120)+'px');e.style.setProperty('--rot',(Math.random()*80-40)+'deg');document.body.appendChild(e);setTimeout(()=>e.remove(),2600)}}function render(){scene.innerHTML='';for(const w of(state.scene?.layout||[])){if(w.visible===false)continue;const e=document.createElement('div');e.id='widget-'+clean(w.id);e.className='w '+(['like_goal','gift_goal','subscriber_goal'].includes(w.widget)?'goal':w.widget==='leaderboard'?'board':w.widget==='announcement'?'ticker':'');e.style.left=Number(w.x||0)+'%';e.style.top=Number(w.y||0)+'%';e.style.width=Number(w.w||30)+'%';e.style.height=Number(w.h||10)+'%';e.style.zIndex=Number(w.z||1);e.textContent=clean(w.settings?.label||w.widget||'Widget');scene.appendChild(e)}const board=document.querySelector('.board');if(board)board.textContent='Top Supporters · '+((state.leaderboards||[]).slice(0,5).map((x,i)=>`${i+1}. ${clean(x.username)} ${clean(x.gift_value)}`).join(' · ')||'Waiting for LIVE activity');const ticker=document.querySelector('.ticker');if(ticker)ticker.textContent=(state.announcements||[]).map(x=>clean(x.text)).join(' ✦ ')||'Powered by Aura AI'}function boundedAutomation(p){if(p.label!=='automation'||!p.message)return;let actions=[];try{actions=JSON.parse(p.message)}catch{return}for(const x of actions){const q=x.params||{};if(x.action==='show_widget'){const e=document.getElementById('widget-'+clean(q.widget_id));if(e)e.style.display='block'}else if(x.action==='hide_widget'){const e=document.getElementById('widget-'+clean(q.widget_id));if(e)e.style.display='none'}else if(x.action==='set_text'){const e=document.getElementById('widget-'+clean(q.widget_id));if(e)e.textContent=clean(q.text)}else if(['switch_scene','set_theme','increment_goal','start_timer','add_timer_seconds'].includes(x.action)){refresh()}else if(x.action==='spotlight_viewer'){show(clean(q.text||'Supporter spotlight'))}else if(x.action==='spin_wheel'){show('Aura wheel triggered')}}}function handle(e){const p=e.payload||{};if(e.event_type==='gift'){giftCannon(p);show(`${clean(p.username||'Viewer')} sent ${clean(p.gift_name||'a gift')}`);refresh()}else if(e.event_type==='like'||e.event_type==='like_milestone'){likeFountain();if(e.event_type==='like_milestone')show('Like milestone reached!');refresh()}else if(e.event_type==='custom'){boundedAutomation(p)}else if(e.event_type.startsWith('battle_'))show('LIVE Match · '+e.event_type.replaceAll('_',' '));else if(e.event_type==='poll')show('Poll update · '+clean(p.title||p.message||''));else if(['follow','subscribe','share','super_fan'].includes(e.event_type))show(`${clean(p.username||'Viewer')} · ${e.event_type.replaceAll('_',' ')}`)}async function refresh(){try{const r=await fetch('/live-overlay/source/advanced/'+encodeURIComponent(TOKEN)+'/state',{cache:'no-store'});if(r.ok){state=await r.json();render()}}catch{}}async function poll(){try{const r=await fetch('/live-overlay/source/advanced/'+encodeURIComponent(TOKEN)+'/events?after='+after,{cache:'no-store'});if(r.ok){const d=await r.json();for(const e of d.events){after=Math.max(after,e.id);handle(e)}}}catch{}finally{setTimeout(poll,750)}}refresh().then(poll);setInterval(refresh,5000);</script></body></html>""".replace("__TOKEN__", token_js)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
        },
    )
