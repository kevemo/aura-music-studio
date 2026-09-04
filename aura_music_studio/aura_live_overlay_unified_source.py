from __future__ import annotations

import json
import secrets
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .aura_live_overlay_effects import effect_state_for_user
from .aura_live_overlay_pro_source import _state as advanced_state
from .aura_live_overlay_studio import (
    SOURCE_TOKEN_BYTES,
    _connect as studio_connect,
    _ensure_profile,
    _hash_token,
    _user_for_source,
)

router = APIRouter(tags=["Aura LIVE Unified Overlay Source"])
EVENT_LIMIT = 100


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _combined_state(user_id: str) -> dict:
    return {
        "advanced": advanced_state(user_id),
        "effects": effect_state_for_user(user_id),
        "single_source": True,
        "event_authority": "normalized_live_overlay_events",
        "provider_authority_widened": False,
    }


@router.get("/api/live-overlays/unified-source")
def unified_source_status(request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    state = _combined_state(member.user_id)
    return {
        "configured": True,
        "effect_count": state["effects"]["effect_count"],
        "enabled_effect_count": len(state["effects"]["enabled_effects"]),
        "single_source": True,
        "uses_existing_overlay_token": True,
        "uses_existing_event_stream": True,
        "tts_uses_existing_speech_endpoint": True,
    }


@router.post("/api/live-overlays/unified-source/rotate")
def rotate_unified_source(request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    raw = secrets.token_urlsafe(SOURCE_TOKEN_BYTES)
    with studio_connect() as con:
        con.execute(
            "UPDATE live_overlay_profiles SET source_token_hash=?,updated_at=datetime('now') WHERE user_id=?",
            (_hash_token(raw), member.user_id),
        )
    base = str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "source_url": f"{base}/live-overlay/source/unified/{quote(raw, safe='')}",
            "rotated": True,
            "shared_overlay_credential": True,
            "invalidates_previous_aura_live_source_urls": True,
        },
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay/source/unified/{token}/state", include_in_schema=False)
def unified_source_state(token: str):
    user_id = _user_for_source(token)
    return JSONResponse(
        _combined_state(user_id),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay/source/unified/{token}/events", include_in_schema=False)
def unified_source_events(token: str, after: int = 0):
    user_id = _user_for_source(token)
    after = max(0, min(int(after), 2_147_483_647))
    with studio_connect() as con:
        rows = con.execute(
            "SELECT id,event_type,payload_json,created_at FROM live_overlay_events WHERE user_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (user_id, after, EVENT_LIMIT),
        ).fetchall()
    events = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except Exception:
            payload = {}
        events.append(
            {
                "id": int(row["id"]),
                "event_type": str(row["event_type"]),
                "payload": payload if isinstance(payload, dict) else {},
                "created_at": row["created_at"],
            }
        )
    return JSONResponse(
        {"events": events},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay-studio/unified-source", response_class=HTMLResponse, include_in_schema=False)
def unified_source_control_room(request: Request):
    member = _member(request)
    profile, fresh_token = _ensure_profile(member.user_id)
    source = (
        f"{str(request.base_url).rstrip('/')}/live-overlay/source/unified/{quote(fresh_token, safe='')}"
        if fresh_token
        else "Rotate the private source to reveal a new URL."
    )
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Unified LIVE Source</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}main{{width:min(1060px,calc(100% - 28px));margin:auto;padding:38px 0 70px}}h1{{font-size:clamp(2.6rem,7vw,5rem);margin:.12em 0}}.card{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px;margin:14px 0}}.muted{{color:#bdc6d8;line-height:1.55}}.row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}button,a{{font:inherit;border-radius:11px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 13px;font-weight:850;text-decoration:none;cursor:pointer}}.primary{{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}}code{{display:block;color:#efc96b;word-break:break-all;padding:10px;background:#05060c;border-radius:10px}}.good{{color:#77e5a7}}
</style></head><body><main><a href='/live-overlay-studio'>← Aura LIVE Overlay Studio</a><p class='muted'>Shared Sky Streaming Studios · Aura LIVE</p><h1>One LIVE source.</h1><p class='muted'>Use one transparent Link/Browser Source for your scene widgets, alerts, goals, leaderboards, announcements, speech and the complete executable LIVE effects library. It consumes the same normalized LIVE event stream and the same private overlay credential as the existing Aura LIVE sources.</p><div class='card'><h2>Private unified browser source</h2><p class='good'>✓ TikTok LIVE Studio Link source · OBS Browser Source · Streamlabs Browser Source</p><button class='primary' id='rotate'>Generate / rotate unified source</button><p><code id='source'>{escape(source)}</code></p><p class='muted'>The URL is a bearer secret. Because Aura LIVE deliberately uses one credential authority, rotation invalidates older Basic, Advanced, Effects and Unified source URLs as well.</p></div><div class='card'><h2>What this combines</h2><div class='row'><a href='/live-overlay-studio/effect-packs'>Effect Packs</a><a href='/live-overlay-studio/effects'>Individual Effects</a><a href='/live-overlay-studio'>Alerts & Speech</a><a href='/live-overlay-studio/advanced'>Advanced Scene</a></div><p class='muted'>Effect packs change the same persisted effect preferences consumed here, so switching from Gaming Energy to Music Stage or a custom pack updates this source without replacing the URL.</p></div><div class='card'><h2>Provider truth</h2><p class='muted'>This source renders authorised normalized LIVE events. It does not bypass TikTok, YouTube, Twitch or another platform's permissions. Real provider events still require an approved/configured provider adapter or ESP-approved relay.</p></div></main><script>
const source=document.getElementById('source');document.getElementById('rotate').onclick=async()=>{{const r=await fetch('/api/live-overlays/unified-source/rotate',{{method:'POST'}});const d=await r.json();if(!r.ok)return alert(d.detail||'Unable to rotate source');source.textContent=d.source_url}};
</script></body></html>""",
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/live-overlay/source/unified/{token}", response_class=HTMLResponse, include_in_schema=False)
def unified_browser_source(token: str):
    _user_for_source(token)
    token_js = json.dumps(token)
    page = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><style>
html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:Inter,Arial,sans-serif;color:#fff}#scene,#ambient,#effects{position:absolute;inset:0;pointer-events:none}.w{position:absolute;box-sizing:border-box}.goal,.board,.ticker{background:#080a13df;border:1px solid #ffffff2b;border-radius:14px;padding:12px}.alert{position:absolute;left:50%;top:12%;transform:translate(-50%,-20px) scale(.95);opacity:0;padding:18px 25px;border-radius:22px;background:linear-gradient(135deg,#180f2ded,#0b2838ed);border:1px solid #ffffff35;box-shadow:0 15px 60px #0009;text-align:center;transition:.3s;max-width:82%;z-index:9999}.alert.show{opacity:1;transform:translate(-50%,0) scale(1)}.p{position:absolute;font-weight:900;text-shadow:0 2px 10px #0008;will-change:transform,opacity}.fountain{animation:fountain 2.5s ease-out forwards}.rain{animation:rain 2.8s linear forwards}.burst{animation:burst 1.9s ease-out forwards}.confetti{animation:confetti 2.8s ease-out forwards}.ring{position:absolute;left:50%;top:50%;width:80px;height:80px;border:5px solid currentColor;border-radius:50%;transform:translate(-50%,-50%) scale(.2);animation:ring 1.3s ease-out forwards}.flash{position:absolute;inset:0;background:radial-gradient(circle at center,#ffffff66,transparent 68%);animation:fade 750ms ease-out forwards}.frame{position:absolute;inset:2%;border:8px solid #ffffffcc;border-radius:28px;box-shadow:0 0 32px #ffffffaa inset,0 0 45px #ffffff99;animation:frame 1.25s ease-out forwards}.banner{position:absolute;left:50%;top:14%;transform:translate(-50%,-25px) scale(.96);padding:15px 22px;max-width:82%;border:1px solid #ffffff45;border-radius:18px;background:#0b1022e8;box-shadow:0 15px 55px #0008;text-align:center;font-size:clamp(18px,3vw,34px);font-weight:900;animation:banner 3.2s ease-in-out forwards}.spotlight{position:absolute;inset:-10%;background:radial-gradient(circle at 50% 45%,#ffffff55 0,transparent 35%);animation:fade 1.4s ease-out forwards}.ambient{position:absolute;animation:ambient 8s linear infinite;opacity:.6}.ambientBand{position:absolute;left:-30%;top:25%;width:160%;height:28%;background:linear-gradient(90deg,transparent,#ffffff16,#ffffff35,#ffffff12,transparent);filter:blur(18px);animation:band 9s ease-in-out infinite}.shake{animation:shake .55s ease-out}.zoom{animation:zoom .75s ease-out}.bounce{animation:bounce .8s ease-out}.glitch{animation:glitch .7s steps(2,end)}
@keyframes fountain{0%{opacity:0;transform:translateY(40px) scale(.7)}18%{opacity:1}100%{opacity:0;transform:translate(var(--dx),-440px) rotate(var(--rot)) scale(1.15)}}@keyframes rain{0%{opacity:0;transform:translateY(-70px) rotate(0)}15%{opacity:1}100%{opacity:0;transform:translate(var(--dx),110vh) rotate(var(--rot))}}@keyframes burst{0%{opacity:0;transform:translate(-50%,-50%) scale(.3)}15%{opacity:1}100%{opacity:0;transform:translate(calc(-50% + var(--dx)),calc(-50% + var(--dy))) rotate(var(--rot)) scale(1.25)}}@keyframes confetti{0%{opacity:0;transform:translateY(-50px) rotate(0)}12%{opacity:1}100%{opacity:0;transform:translate(var(--dx),105vh) rotate(var(--rot))}}@keyframes ring{0%{opacity:0;transform:translate(-50%,-50%) scale(.2)}20%{opacity:1}100%{opacity:0;transform:translate(-50%,-50%) scale(8)}}@keyframes fade{0%{opacity:0}20%{opacity:1}100%{opacity:0}}@keyframes frame{0%{opacity:0;transform:scale(.96)}25%{opacity:1}100%{opacity:0;transform:scale(1.01)}}@keyframes banner{0%{opacity:0;transform:translate(-50%,-28px) scale(.96)}12%,75%{opacity:1;transform:translate(-50%,0) scale(1)}100%{opacity:0;transform:translate(-50%,-12px) scale(.98)}}@keyframes ambient{0%{transform:translateY(105vh) translateX(0)}100%{transform:translateY(-15vh) translateX(var(--dx))}}@keyframes band{0%,100%{transform:translateX(-10%) rotate(-5deg);opacity:.2}50%{transform:translateX(10%) rotate(5deg);opacity:.6}}@keyframes shake{0%,100%{transform:translate(0)}20%{transform:translate(-10px,5px)}40%{transform:translate(9px,-5px)}60%{transform:translate(-7px,4px)}80%{transform:translate(5px,-3px)}}@keyframes zoom{0%,100%{transform:scale(1)}45%{transform:scale(1.045)}}@keyframes bounce{0%,100%{transform:translateY(0)}40%{transform:translateY(-18px)}70%{transform:translateY(8px)}}@keyframes glitch{0%,100%{filter:none;transform:translate(0)}20%{filter:hue-rotate(60deg) contrast(1.5);transform:translate(8px,-3px)}45%{filter:hue-rotate(-80deg) contrast(1.8);transform:translate(-7px,4px)}70%{filter:contrast(1.4);transform:translate(4px,2px)}}
</style></head><body><div id='scene'></div><div id='ambient'></div><div id='effects'></div><div id='alert' class='alert'></div><script>'use strict';
const TOKEN=__TOKEN__;let after=0,state={advanced:{},effects:{catalog:[],enabled_effects:[],intensity:1,max_effects_per_event:6,reduced_motion:false}},busy=false,speechQueue=[],lastSpoken=new Map(),welcomed=new Set();const scene=document.getElementById('scene'),ambient=document.getElementById('ambient'),effects=document.getElementById('effects'),alertBox=document.getElementById('alert');const clean=v=>String(v??'').slice(0,500),rnd=(a,b)=>a+Math.random()*(b-a);
function profile(){return state.advanced?.profile||{}}function show(t){alertBox.textContent=clean(t);alertBox.classList.add('show');setTimeout(()=>alertBox.classList.remove('show'),3500)}function fmt(t,p){return clean(t).replaceAll('{username}',clean(p.username||p.display_name||'friend')).replaceAll('{gift_name}',clean(p.gift_name||'gift')).replaceAll('{gift_count}',clean(p.gift_count||1)).replaceAll('{coins}',clean(p.coins||0))}
function queueSpeech(text){const cfg=profile();if(cfg.all_audio_muted||!text)return;const key=clean(text).slice(0,80),now=Date.now(),wait=Number(cfg.user_cooldown_seconds||8)*1000;if(lastSpoken.has(key)&&now-lastSpoken.get(key)<wait)return;lastSpoken.set(key,now);if(speechQueue.length>=Math.min(20,Number(cfg.max_tts_queue||6)))return;speechQueue.push(clean(text).slice(0,Math.min(500,Number(cfg.max_tts_chars||220))));drainSpeech()}
async function drainSpeech(){if(busy||!speechQueue.length)return;busy=true;const cfg=profile(),text=speechQueue.shift();try{if(cfg.voice_mode==='browser'||!cfg.voice_mode){await new Promise(res=>{const u=new SpeechSynthesisUtterance(text);u.volume=Number(cfg.tts_volume??.85);u.onend=u.onerror=res;speechSynthesis.speak(u)})}else{const r=await fetch('/live-overlay/source/'+encodeURIComponent(TOKEN)+'/speech',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text})});if(r.ok){const blob=await r.blob(),url=URL.createObjectURL(blob),a=new Audio(url);a.volume=Number(cfg.tts_volume??.85);await a.play();await new Promise(res=>{a.onended=a.onerror=res});URL.revokeObjectURL(url)}}}catch{}finally{busy=false;drainSpeech()}}
function allowedWelcome(p){const cfg=profile();if(cfg.welcome_audience==='all')return true;if(cfg.welcome_audience==='subscribers')return !!p.is_subscriber;if(cfg.welcome_audience==='vip')return !!(p.is_subscriber||p.is_moderator||p.is_team||p.is_top_gifter);return !!(p.is_follower||p.is_subscriber||p.is_moderator||p.is_team||p.is_top_gifter)}
function renderScene(){scene.innerHTML='';const adv=state.advanced||{};for(const w of(adv.scene?.layout||[])){if(w.visible===false)continue;const e=document.createElement('div');e.id='widget-'+clean(w.id);e.className='w '+(['like_goal','gift_goal','subscriber_goal','follower_goal','share_goal','custom_goal'].includes(w.widget)?'goal':w.widget==='leaderboard'||['top_gifters','top_likers'].includes(w.widget)?'board':w.widget==='announcement'?'ticker':'');e.style.left=Number(w.x||0)+'%';e.style.top=Number(w.y||0)+'%';e.style.width=Number(w.w||30)+'%';e.style.height=Number(w.h||10)+'%';e.style.zIndex=Number(w.z||1);e.textContent=clean(w.settings?.label||w.widget||'Widget');scene.appendChild(e)}const board=document.querySelector('.board');if(board)board.textContent='Top Supporters · '+((adv.leaderboards||[]).slice(0,5).map((x,i)=>`${i+1}. ${clean(x.username)} ${clean(x.gift_value)}`).join(' · ')||'Waiting for LIVE activity');const ticker=document.querySelector('.ticker');if(ticker)ticker.textContent=(adv.announcements||[]).map(x=>clean(x.text)).join(' ✦ ')||'Powered by Aura AI'}
function add(n,ms=3000){effects.appendChild(n);setTimeout(()=>n.remove(),ms)}function particle(spec,cls,x,y){const n=document.createElement('div');n.className='p '+cls;n.textContent=clean(spec.symbol||'✦');n.style.left=x+'%';n.style.top=y+'%';n.style.fontSize=(18+rnd(0,24)*Number(state.effects.intensity||1))+'px';n.style.setProperty('--dx',rnd(-260,260)+'px');n.style.setProperty('--dy',rnd(-260,260)+'px');n.style.setProperty('--rot',rnd(-240,240)+'deg');return n}
function titleFor(e,p){const u=clean(p.username||p.display_name||'Viewer');if(e==='gift')return `${u} sent ${clean(p.gift_name||'a gift')}`;if(e==='follow')return `${u} followed`;if(e==='subscribe')return `${u} subscribed`;if(e==='share'||e==='shared_stream')return `${u} shared the LIVE`;if(e==='like_milestone')return 'Like milestone reached!';if(e==='battle_start')return 'LIVE Match started';if(e==='battle_end')return 'LIVE Match complete';return `${u} · ${clean(e.replaceAll('_',' '))}`}
function runEffect(spec,e,p){const reduced=!!state.effects.reduced_motion,mult=Math.max(.25,Math.min(2,Number(state.effects.intensity||1)));let count=Math.max(2,Math.min(28,Math.round(8*mult)));if(reduced)count=Math.min(count,4);const prim=spec.primitive;if(prim==='ambient'||prim==='ambient_band')return;if(prim==='fountain'){for(let i=0;i<count;i++){const n=particle(spec,'fountain',rnd(6,94),92);n.style.top='auto';n.style.bottom='0';add(n,2800)}}else if(prim==='rain'||prim==='confetti'){for(let i=0;i<count+(prim==='confetti'?5:0);i++)add(particle(spec,prim,rnd(2,98),-8),3100)}else if(prim==='burst'){for(let i=0;i<count;i++)add(particle(spec,'burst',50,50),2200)}else if(prim==='ring'){for(let i=0;i<(reduced?1:3);i++){const n=document.createElement('div');n.className='ring';n.style.animationDelay=(i*100)+'ms';add(n,1800)}}else if(prim==='flash'){const n=document.createElement('div');n.className='flash';add(n,1000)}else if(prim==='frame'){const n=document.createElement('div');n.className='frame';add(n,1500)}else if(prim==='banner'){const n=document.createElement('div');n.className='banner';n.textContent=titleFor(e,p);add(n,3400)}else if(prim==='spotlight'){const n=document.createElement('div');n.className='spotlight';add(n,1700)}else if(['shake','zoom','bounce','glitch'].includes(prim)&&!reduced){document.body.classList.remove(prim);void document.body.offsetWidth;document.body.classList.add(prim);setTimeout(()=>document.body.classList.remove(prim),900)}}
function renderAmbient(){ambient.innerHTML='';if(state.effects.reduced_motion)return;const enabled=new Set(state.effects.enabled_effects||[]);for(const spec of state.effects.catalog||[]){if(!enabled.has(spec.id)||!spec.events.includes('ambient'))continue;if(spec.primitive==='ambient_band'){const n=document.createElement('div');n.className='ambientBand';ambient.appendChild(n);continue}for(let i=0;i<6;i++){const n=document.createElement('div');n.className='p ambient';n.textContent=clean(spec.symbol||'✦');n.style.left=rnd(2,98)+'%';n.style.bottom=rnd(-40,70)+'%';n.style.fontSize=rnd(12,28)+'px';n.style.animationDelay=(-rnd(0,8))+'s';n.style.animationDuration=rnd(7,14)+'s';n.style.setProperty('--dx',rnd(-120,120)+'px');ambient.appendChild(n)}}}
function handleAdvanced(ev){const p=ev.payload||{},type=ev.event_type,cfg=profile();if(type==='gift'){show(`${clean(p.username||'Viewer')} sent ${clean(p.gift_name||'a gift')}!`);if(cfg.tts_gifts_enabled&&Number(p.coins||0)>=Number(cfg.min_gift_coins||0))queueSpeech(fmt(cfg.gift_template||'Thank you {username} for the {gift_name}!',p))}else if(type==='viewer_joined'&&cfg.welcome_enabled&&allowedWelcome(p)){const k=clean(p.username||p.display_name);if(!cfg.welcome_once||!welcomed.has(k)){welcomed.add(k);show(`Welcome ${k}!`);queueSpeech(fmt(cfg.welcome_template||'Welcome {username}!',p))}}else if(type==='comment'){if(cfg.tts_chat_enabled)queueSpeech(`${clean(p.username)} says ${clean(p.message)}`)}else if(type==='like_milestone')show('Like milestone reached!');else if(type.startsWith('battle_'))show('LIVE Match · '+type.replaceAll('_',' '));else if(['follow','subscribe','share','super_fan','poll','question','pinned_message'].includes(type))show(`${clean(p.username||'Viewer')} · ${type.replaceAll('_',' ')}`)}
function handle(ev){handleAdvanced(ev);const enabled=new Set(state.effects.enabled_effects||[]),eligible=(state.effects.catalog||[]).filter(s=>enabled.has(s.id)&&s.events.includes(ev.event_type)),cap=Math.max(1,Math.min(12,Number(state.effects.max_effects_per_event||6)));for(const spec of eligible.slice(0,cap))runEffect(spec,ev.event_type,ev.payload||{})}
async function refresh(){try{const r=await fetch('/live-overlay/source/unified/'+encodeURIComponent(TOKEN)+'/state',{cache:'no-store'});if(r.ok){state=await r.json();renderScene();renderAmbient()}}catch{}}async function poll(){try{const r=await fetch('/live-overlay/source/unified/'+encodeURIComponent(TOKEN)+'/events?after='+after,{cache:'no-store'});if(r.ok){const d=await r.json();for(const ev of d.events){after=Math.max(after,ev.id);handle(ev)}}}catch{}finally{setTimeout(poll,650)}}refresh().then(poll);setInterval(refresh,5000);
</script></body></html>""".replace("__TOKEN__", token_js)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        },
    )


__all__ = ["router", "unified_browser_source", "unified_source_control_room", "unified_source_state"]
