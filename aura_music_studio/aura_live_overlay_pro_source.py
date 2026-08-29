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

from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Advanced Source"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
MEDIA_ROOT = Path(os.getenv("AURA_LIVE_OVERLAY_MEDIA_ROOT", "data/aura_live_overlay_media"))
TOKEN_BYTES = 32


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
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
        row = con.execute("SELECT user_id FROM live_overlay_profiles WHERE source_token_hash=?", (_hash(token),)).fetchone()
    if not row:
        raise HTTPException(404, "Overlay source not found")
    return str(row["user_id"])


def _safe_json(value: str, default):
    try:
        out = json.loads(value)
        return out
    except Exception:
        return default


def _profile(user_id: str) -> dict:
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Overlay profile not found")
    d = dict(row)
    for key in ("gift_sound_muted", "all_audio_muted", "tts_chat_enabled", "tts_gifts_enabled", "welcome_enabled", "welcome_once", "profanity_filter"):
        d[key] = bool(d.get(key))
    d["active_widgets"] = _safe_json(d.pop("active_widgets_json", "[]"), [])
    d.pop("source_token_hash", None)
    return d


def _state(user_id: str) -> dict:
    with _connect() as con:
        scene = con.execute("SELECT * FROM live_overlay_scenes WHERE user_id=? ORDER BY active DESC,updated_at DESC LIMIT 1", (user_id,)).fetchone()
        goals = con.execute("SELECT id,name,metric,target,current,enabled FROM live_overlay_goals WHERE user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 12", (user_id,)).fetchall()
        leaders = con.execute("SELECT username,gift_count,gift_value,likes,shares,comments FROM live_overlay_session_stats WHERE user_id=? ORDER BY gift_value DESC,likes DESC LIMIT 20", (user_id,)).fetchall()
        timer = con.execute("SELECT * FROM live_overlay_timers WHERE user_id=?", (user_id,)).fetchone()
        announcements = con.execute("SELECT id,text,priority FROM live_overlay_announcements WHERE user_id=? AND enabled=1 ORDER BY priority DESC,created_at LIMIT 20", (user_id,)).fetchall()
        wheels = con.execute("SELECT id,name,items_json FROM live_overlay_wheels WHERE user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 10", (user_id,)).fetchall()
        auction = con.execute("SELECT * FROM live_overlay_auction WHERE user_id=?", (user_id,)).fetchone()
        challenges = con.execute("SELECT id,name,event_type,gift_name,target,current,reward_text FROM live_overlay_challenges WHERE user_id=? AND enabled=1 ORDER BY updated_at DESC LIMIT 10", (user_id,)).fetchall()
    layout = _safe_json(scene["layout_json"], []) if scene else []
    return {
        "scene": ({"id": scene["id"], "name": scene["name"], "width": scene["width"], "height": scene["height"], "layout": layout} if scene else None),
        "goals": [dict(r) for r in goals],
        "leaderboards": [dict(r) for r in leaders],
        "timer": dict(timer) if timer else None,
        "announcements": [dict(r) for r in announcements],
        "wheels": [{"id": r["id"], "name": r["name"], "items": _safe_json(r["items_json"], [])} for r in wheels],
        "auction": dict(auction) if auction else None,
        "challenges": [dict(r) for r in challenges],
    }


@router.get("/live-overlay-studio/advanced-source", response_class=HTMLResponse, include_in_schema=False)
def source_setup(request: Request):
    member = _member(request)
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Aura LIVE Advanced Source</title><style>body{{margin:0;background:#080a13;color:#fff;font-family:Inter,system-ui}}main{{max-width:900px;margin:auto;padding:38px}}.card{{background:#141929;border:1px solid #ffffff20;border-radius:18px;padding:20px;margin:14px 0}}button{{background:linear-gradient(120deg,#efc96b,#9b72ff);border:0;border-radius:10px;padding:12px 16px;font-weight:900;cursor:pointer}}code{{word-break:break-all;color:#efc96b}}.muted{{color:#b5bfd3}}</style></head><body><main><a href='/live-overlay-studio' style='color:white'>← LIVE control room</a><h1>Aura LIVE Advanced Single Source</h1><p class='muted'>One private URL renders your active scene, alerts, effects, goals, leaderboards, timer, ticker, challenges and safe automation actions. Paste it as a Link source in TikTok LIVE Studio or a Browser Source in OBS/Streamlabs.</p><div class='card'><b>{escape(member.plan.name)} tier</b><p class='muted'>Rotating this URL immediately invalidates every old Aura LIVE source URL for your account.</p><button id='rotate'>Generate / rotate advanced source</button><p id='url'><code>Press the button to reveal a new private URL.</code></p></div><div class='card'><h2>Built-in visual reactions</h2><p class='muted'>Gift Cannon, Like Fountain, supporter alert cards, goal bars, ticker, leaderboard, battle/poll cards and scene widgets are rendered inside the same source. Audio obeys your gift-sound and all-audio mute controls.</p><p><a style='color:white' href='/live-overlay-studio/editor'>Open visual designer</a></p></div></main><script>document.getElementById('rotate').onclick=async()=>{{let r=await fetch('/api/live-overlays/advanced-source/rotate',{{method:'POST'}}),d=await r.json();if(!r.ok)return alert(d.detail||'Unable to rotate');document.getElementById('url').innerHTML='<code>'+d.source_url.replaceAll('&','&amp;').replaceAll('<','&lt;')+'</code>'}}</script></body></html>"", headers={"Cache-Control": "private, no-store"})


@router.post("/api/live-overlays/advanced-source/rotate")
def rotate_advanced_source(request: Request):
    member = _member(request)
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    with _connect() as con:
        row = con.execute("SELECT user_id FROM live_overlay_profiles WHERE user_id=?", (member.user_id,)).fetchone()
        if not row:
            raise HTTPException(409, "Open Aura LIVE Overlay Studio once before generating a source URL")
        con.execute("UPDATE live_overlay_profiles SET source_token_hash=? WHERE user_id=?", (_hash(raw), member.user_id))
    base = str(request.base_url).rstrip("/")
    return {"source_url": f"{base}/live-overlay/advanced/{quote(raw, safe='')}", "rotated": True}


@router.get("/live-overlay/advanced/{token}/state", include_in_schema=False)
def source_state(token: str):
    user_id = _user_for_token(token)
    return JSONResponse({"profile": _profile(user_id), **_state(user_id)}, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/live-overlay/advanced/{token}/events", include_in_schema=False)
def source_events(token: str, after: int = 0):
    user_id = _user_for_token(token)
    after = max(0, int(after))
    with _connect() as con:
        rows = con.execute("SELECT id,event_type,payload_json,created_at FROM live_overlay_events WHERE user_id=? AND id>? ORDER BY id ASC LIMIT 100", (user_id, after)).fetchall()
    return JSONResponse({"events": [{"id": r["id"], "event_type": r["event_type"], "payload": _safe_json(r["payload_json"], {}), "created_at": r["created_at"]} for r in rows]}, headers={"Cache-Control": "no-store"})


@router.get("/live-overlay/advanced/{token}/media/{media_id}", include_in_schema=False)
def source_media(token: str, media_id: str):
    user_id = _user_for_token(token)
    with _connect() as con:
        row = con.execute("SELECT stored_name,mime_type FROM live_overlay_media WHERE id=? AND user_id=?", (media_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Media not found")
    root = (MEDIA_ROOT / hashlib.sha256(user_id.encode()).hexdigest()[:24]).resolve()
    target = (root / str(row["stored_name"])).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "Media not found")
    return FileResponse(target, media_type=row["mime_type"], headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


@router.get("/live-overlay/advanced/{token}", response_class=HTMLResponse, include_in_schema=False)
def advanced_source(token: str):
    _user_for_token(token)
    token_js = json.dumps(token)
    html = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><style>
html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:Inter,Arial,sans-serif;color:#fff}#scene{position:absolute;inset:0}.w{position:absolute;box-sizing:border-box}.alert{left:50%;top:12%;transform:translate(-50%,-20px) scale(.95);opacity:0;padding:18px 25px;border-radius:22px;background:linear-gradient(135deg,#180f2ded,#0b2838ed);border:1px solid #ffffff35;box-shadow:0 15px 60px #0009;text-align:center;transition:.3s;max-width:82%}.alert.show{opacity:1;transform:translate(-50%,0) scale(1)}.goal{background:#080a13dd;border:1px solid #ffffff30;border-radius:16px;overflow:hidden;min-height:34px}.goal i{display:block;position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,#efc96b,#9b72ff);z-index:-1}.board,.ticker,.battle,.poll{background:#080a13df;border:1px solid #ffffff2b;border-radius:14px;padding:12px}.ticker{white-space:nowrap;overflow:hidden}.ticker span{display:inline-block;animation:scroll 18s linear infinite}.particle{position:absolute;pointer-events:none;animation:fly 2.4s ease-out forwards;font-weight:900;text-shadow:0 2px 8px #000}.heart{color:#ff5a8c;font-size:28px}.giftp{font-size:16px;background:#160f2ddd;border:1px solid #ffffff28;border-radius:999px;padding:6px 10px}.mediafx{position:absolute;left:50%;top:50%;max-width:70%;max-height:70%;transform:translate(-50%,-50%);animation:pop 3s ease forwards}.watermark{position:absolute;left:1.5%;bottom:1.5%;font-size:10px;opacity:.55}@keyframes fly{0%{opacity:0;transform:translateY(40px) scale(.7)}20%{opacity:1}100%{opacity:0;transform:translate(var(--dx),-420px) rotate(var(--rot)) scale(1.2)}}@keyframes pop{0%,100%{opacity:0;transform:translate(-50%,-50%) scale(.7)}15%,80%{opacity:1;transform:translate(-50%,-50%) scale(1)}}@keyframes scroll{from{transform:translateX(100vw)}to{transform:translateX(-100%)}}
</style></head><body><div id='scene'></div><div id='alert' class='w alert'></div><div class='watermark'>Aura LIVE · Powered by Aura AI</div><script>
'use strict';const TOKEN=__TOKEN__;let after=0,state={},welcomed=new Set(),speechBusy=false,speechQueue=[];const scene=document.getElementById('scene'),alertBox=document.getElementById('alert');const clean=v=>String(v??'').slice(0,500);function show(t){alertBox.textContent=clean(t);alertBox.classList.add('show');setTimeout(()=>alertBox.classList.remove('show'),3500)}
function applyLayout(){scene.innerHTML='';let layout=state.scene?.layout||[];for(const w of layout){if(w.visible===false)continue;let e=document.createElement('div');e.id='widget-'+w.id;e.dataset.widget=w.widget;e.className='w '+(['like_goal','gift_goal','subscriber_goal'].includes(w.widget)?'goal':w.widget==='leaderboard'?'board':w.widget==='announcement'?'ticker':'');e.style.left=w.x+'%';e.style.top=w.y+'%';e.style.width=w.w+'%';e.style.height=w.h+'%';e.style.zIndex=w.z||1;e.textContent=clean(w.settings?.label||w.widget.replaceAll('_',' '));scene.appendChild(e)}renderState()}
function renderState(){let goals=state.goals||[],gEls=[...document.querySelectorAll('.goal')];gEls.forEach((e,i)=>{let g=goals[i];if(!g)return;e.textContent='';let fill=document.createElement('i');fill.style.width=Math.min(100,(Number(g.current||0)/Math.max(1,Number(g.target||1))*100))+'%';e.appendChild(fill);let s=document.createElement('span');s.textContent=`${clean(g.name)} · ${clean(g.current)} / ${clean(g.target)}`;e.appendChild(s)});let b=document.querySelector('.board');if(b){b.innerHTML='<b>Top Supporters</b><br>'+((state.leaderboards||[]).slice(0,5).map((x,i)=>`${i+1}. ${clean(x.username)} · ${clean(x.gift_value)}`).join('<br>')||'Waiting for LIVE activity')}let t=document.querySelector('.ticker');if(t){t.innerHTML='';let s=document.createElement('span');s.textContent=(state.announcements||[]).map(x=>x.text).join('   ✦   ')||'Powered by Aura AI';t.appendChild(s)}}
function giftCannon(p){for(let i=0;i<Math.min(10,Math.max(2,Number(p.gift_count||1)+1));i++){let e=document.createElement('div');e.className='particle giftp';e.textContent=`${clean(p.username||'Viewer')} · ${clean(p.gift_name||'Gift')}`;e.style.left=(15+Math.random()*70)+'%';e.style.bottom='5%';e.style.setProperty('--dx',(Math.random()*300-150)+'px');e.style.setProperty('--rot',(Math.random()*100-50)+'deg');document.body.appendChild(e);setTimeout(()=>e.remove(),2600)}}
function likeFountain(p){for(let i=0;i<8;i++){let e=document.createElement('div');e.className='particle heart';e.textContent='♥';e.style.left=(5+Math.random()*90)+'%';e.style.bottom='0';e.style.setProperty('--dx',(Math.random()*240-120)+'px');e.style.setProperty('--rot',(Math.random()*80-40)+'deg');document.body.appendChild(e);setTimeout(()=>e.remove(),2600)}}
function beep(){let c=state.profile||{};if(c.all_audio_muted||c.gift_sound_muted)return;try{let A=window.AudioContext||window.webkitAudioContext,a=new A(),o=a.createOscillator(),g=a.createGain();o.frequency.value=660;g.gain.value=.08;o.connect(g);g.connect(a.destination);o.start();o.stop(a.currentTime+.25)}catch{}}
function speak(text){let c=state.profile||{};if(c.all_audio_muted||!text)return;speechQueue.push(clean(text).slice(0,c.max_tts_chars||220));drainSpeech()}
async function drainSpeech(){if(speechBusy||!speechQueue.length)return;speechBusy=true;let text=speechQueue.shift(),c=state.profile||{};try{if(c.voice_mode==='browser'||!c.voice_mode){await new Promise(res=>{let u=new SpeechSynthesisUtterance(text);u.volume=Number(c.tts_volume??.85);u.onend=u.onerror=res;speechSynthesis.speak(u)})}else{let r=await fetch('/live-overlay/source/'+encodeURIComponent(TOKEN)+'/speech',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text})});if(r.ok){let blob=await r.blob(),url=URL.createObjectURL(blob),a=new Audio(url);a.volume=Number(c.tts_volume??.85);await a.play();await new Promise(res=>{a.onended=a.onerror=res});URL.revokeObjectURL(url)}}}catch{}finally{speechBusy=false;drainSpeech()}}
function media(id,soundOnly=false){if(!id)return;let url='/live-overlay/advanced/'+encodeURIComponent(TOKEN)+'/media/'+encodeURIComponent(id);if(soundOnly){if((state.profile||{}).all_audio_muted)return;let a=new Audio(url);a.volume=Number((state.profile||{}).tts_volume??.85);a.play().catch(()=>{});return}let e=document.createElement('video');e.src=url;e.autoplay=true;e.muted=false;e.className='mediafx';e.playsInline=true;document.body.appendChild(e);e.play().catch(()=>{e.remove();let img=document.createElement('img');img.src=url;img.className='mediafx';document.body.appendChild(img);setTimeout(()=>img.remove(),3200)});setTimeout(()=>e.remove(),8000)}
function automation(p){if(p.label!=='automation'||!p.message)return;let actions=[];try{actions=JSON.parse(p.message)}catch{return}for(const x of actions){let a=x.action,q=x.params||{};if(a==='show_widget'){let e=document.getElementById('widget-'+q.widget_id);if(e)e.style.display='block'}else if(a==='hide_widget'){let e=document.getElementById('widget-'+q.widget_id);if(e)e.style.display='none'}else if(a==='set_text'){let e=document.getElementById('widget-'+q.widget_id);if(e)e.textContent=clean(q.text)}else if(a==='speak')speak(q.text||'');else if(a==='play_media')media(q.media_id,false);else if(a==='play_sound')media(q.media_id,true);else if(a==='switch_scene'||a==='set_theme'||a==='increment_goal'||a==='start_timer'||a==='add_timer_seconds')refresh();else if(a==='spotlight_viewer')show(clean(q.text||'Supporter spotlight'));else if(a==='spin_wheel')show('Aura wheel triggered')}}}
function handle(e){let p=e.payload||{},type=e.event_type,c=state.profile||{};if(type==='gift'){giftCannon(p);beep();show(`${clean(p.username||'Viewer')} sent ${clean(p.gift_name||'a gift')} ×${clean(p.gift_count||1)}`);if(c.tts_gifts_enabled&&Number(p.coins||0)>=Number(c.min_gift_coins||0))speak(clean(c.gift_template||'Thank you {username} for the {gift_name}!').replaceAll('{username}',clean(p.username||'friend')).replaceAll('{gift_name}',clean(p.gift_name||'gift')));refresh()}else if(type==='like'||type==='like_milestone'){likeFountain(p);if(type==='like_milestone')show('Like milestone reached!');refresh()}else if(type==='viewer_joined'&&c.welcome_enabled){let k=clean(p.username||p.display_name);let ok=c.welcome_audience==='all'||(c.welcome_audience==='followers'&&(p.is_follower||p.is_subscriber))||(c.welcome_audience==='subscribers'&&p.is_subscriber)||(c.welcome_audience==='vip'&&(p.is_subscriber||p.is_moderator||p.is_team||p.is_top_gifter));if(ok&&(!c.welcome_once||!welcomed.has(k))){welcomed.add(k);show('Welcome '+k+'!');speak(clean(c.welcome_template||'Welcome {username}!').replaceAll('{username}',k))}}else if(type==='comment'&&c.tts_chat_enabled)speak(`${clean(p.username)} says ${clean(p.message)}`);else if(type==='custom')automation(p);else if(type.startsWith('battle_'))show('LIVE Match · '+type.replaceAll('_',' '));else if(type==='poll')show('Poll update · '+clean(p.title||p.message||''));else if(type==='follow'||type==='subscribe'||type==='share'||type==='super_fan')show(`${clean(p.username||'Viewer')} · ${type.replaceAll('_',' ')}`)}
async function refresh(){try{let r=await fetch('/live-overlay/advanced/'+encodeURIComponent(TOKEN)+'/state',{cache:'no-store'});if(r.ok){state=await r.json();applyLayout()}}catch{}}
async function poll(){try{let r=await fetch('/live-overlay/advanced/'+encodeURIComponent(TOKEN)+'/events?after='+after,{cache:'no-store'});if(r.ok){let d=await r.json();for(const e of d.events){after=Math.max(after,e.id);handle(e)}}}catch{}finally{setTimeout(poll,600)}}refresh().then(poll);setInterval(refresh,5000);
</script></body></html>""".replace("__TOKEN__", token_js)
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow", "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"})
