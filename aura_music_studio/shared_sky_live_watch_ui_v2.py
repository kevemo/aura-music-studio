from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized

router = APIRouter(tags=["Shared Sky Watch Player Wave 2"])


def _safe_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or any(ch in raw for ch in ("\n", "\r", "\x00")):
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    parsed = urlparse(raw)
    if (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.netloc
        and not parsed.username
        and not parsed.password
    ):
        return raw
    return ""


def _rendition_url(raw: dict[str, Any]) -> str:
    direct = _safe_url(raw.get("manifest_url") or raw.get("url"))
    if direct:
        return direct
    profile = raw.get("profile")
    if isinstance(profile, dict):
        return _safe_url(profile.get("manifest_url") or profile.get("url"))
    return ""


def _browser_descriptor(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else {}
    manifest = _safe_url(
        source.get("manifest_url") or source.get("replay_url") or source.get("url")
    )
    renditions: list[dict[str, Any]] = []
    for raw in source.get("renditions") or []:
        if not isinstance(raw, dict):
            continue
        rendition = {
            "name": str(raw.get("name") or raw.get("label") or "Auto")[:80],
            "profile": raw.get("profile"),
        }
        rendition_url = _rendition_url(raw)
        if rendition_url:
            rendition["manifest_url"] = rendition_url
        renditions.append(rendition)
    captions: list[dict[str, Any]] = []
    for raw in source.get("captions") or []:
        if not isinstance(raw, dict):
            continue
        src = _safe_url(raw.get("src") or raw.get("url"))
        if not src:
            continue
        captions.append(
            {
                "src": src,
                "srclang": str(raw.get("srclang") or raw.get("language") or "en")[:20],
                "label": str(raw.get("label") or raw.get("language") or "Captions")[:80],
                "default": bool(raw.get("default", False)),
            }
        )
    available = bool(source.get("available") and manifest)
    return {
        "available": available,
        "state": str(
            source.get("state") or ("ready" if available else "unavailable")
        )[:80],
        "reason": str(source.get("reason") or "")[:160],
        "manifest_url": manifest if available else None,
        "renditions": renditions,
        "captions": captions,
        "dvr": bool(source.get("dvr", False)),
        "browser_authorization_mode": str(
            source.get("browser_authorization_mode") or ""
        )[:120],
        "token_expires_at": source.get("token_expires_at"),
    }


def _member_id(request: Request) -> str | None:
    member = live.optional_member(request)
    return member.user_id if member else None


def _access_or_raise(broadcast_id: str, request: Request) -> str | None:
    user_id = _member_id(request)
    try:
        decision = live.community.access(
            broadcast_id,
            user_id,
            direct=True,
            owner=owner_session_authorized(request),
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky LIVE not found") from exc
    if not decision.allowed:
        raise HTTPException(403, decision.reason)
    return user_id


def _interactive_state(broadcast_id: str, viewer_key: str | None) -> dict[str, Any]:
    polls: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    with live.community._connect() as con:
        poll_rows = con.execute(
            """SELECT id FROM shared_sky_polls WHERE broadcast_id=?
               ORDER BY CASE WHEN state='live' THEN 0 ELSE 1 END,starts_at DESC LIMIT 10""",
            (broadcast_id,),
        ).fetchall()
        qa_rows = con.execute(
            """SELECT id,question,state,selected,created_at,updated_at
               FROM shared_sky_qa
               WHERE broadcast_id=? AND state IN ('approved','answered')
               ORDER BY selected DESC,updated_at DESC LIMIT 40""",
            (broadcast_id,),
        ).fetchall()
    for row in poll_rows:
        try:
            polls.append(live.community.poll(str(row["id"]), viewer_key))
        except KeyError:
            continue
    for row in qa_rows:
        questions.append(
            {
                "id": row["id"],
                "question": row["question"],
                "state": row["state"],
                "selected": bool(row["selected"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return {"polls": polls, "questions": questions}


@router.get("/shared-sky/live/api/watch/{broadcast_id}/interactives")
def watch_interactive_state(broadcast_id: str, request: Request):
    user_id = _access_or_raise(broadcast_id, request)
    presence_token = request.headers.get("x-shared-sky-presence")
    viewer_key = (
        f"user:{user_id}"
        if user_id
        else live.community.presence_actor(broadcast_id, presence_token)
    )
    return _interactive_state(broadcast_id, viewer_key)


WATCH_CSS = """
:root{--bg:#061411;--panel:#0a231f;--line:#ffffff22;--text:#f4fff9;--muted:#b8cec7;--emerald:#5de0b2;--sardonyx:#d47f60;--gold:#f1ce79}
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;background:radial-gradient(circle at 5% 0,#0c6c5255,transparent 30%),radial-gradient(circle at 94% 0,#a85d4355,transparent 25%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}button,input,select{font:inherit}button,.btn{border:1px solid var(--line);background:#ffffff0a;color:var(--text);border-radius:11px;padding:9px 11px;font-weight:800;cursor:pointer}button:disabled,input:disabled{opacity:.52;cursor:not-allowed}.btn{text-decoration:none;display:inline-flex;align-items:center}.wrap{width:min(1480px,calc(100% - 24px));margin:auto;padding:12px 0 44px}.top{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.watch{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(320px,.55fr);gap:12px;margin-top:12px}.stage{min-width:0}.player-shell{position:relative;background:#000;border:1px solid var(--line);border-radius:18px;overflow:hidden;min-height:300px;display:grid;place-items:center;outline:none}.player-shell:focus-visible{box-shadow:0 0 0 3px var(--gold)}video{width:100%;max-height:76vh;display:block;background:#000;object-fit:contain}.player-shell.portrait video{max-height:82vh;width:auto;max-width:100%}.player-status{padding:34px;max-width:720px;text-align:center}.player-bar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:9px;background:#071411ee;border-top:1px solid var(--line)}.player-bar button,.player-bar select{min-height:40px}.player-bar input[type=range]{width:110px}.live-dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#e83f58;margin-right:6px}.live-dot.off{background:#788c86}.info{border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:14px;margin-top:10px}.muted{color:var(--muted);line-height:1.45}.actions{display:flex;gap:7px;flex-wrap:wrap}.sidebar{border:1px solid var(--line);border-radius:18px;background:var(--panel);min-height:0;overflow:hidden;display:flex;flex-direction:column}.tabs{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid var(--line)}.tabs button{border:0;border-radius:0;background:transparent}.tabs button[aria-selected=true]{background:#ffffff0d;color:var(--emerald)}.pane{display:none;padding:12px;min-height:0}.pane.active{display:flex;flex-direction:column}.chat{height:min(52vh,610px);overflow:auto;display:flex;flex-direction:column;gap:7px}.msg{padding:8px 9px;border-radius:10px;background:#ffffff08;overflow-wrap:anywhere}.msg.pinned{border:1px solid var(--gold)}.composer{display:flex;gap:7px;margin-top:8px}.composer input{flex:1;min-width:0;background:#061411;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px}.poll,.question,.gift{padding:10px;border-radius:12px;background:#ffffff08;margin-bottom:8px}.poll-options{display:grid;gap:6px}.poll-options button{text-align:left}.selected-q{border:1px solid var(--gold)}.gift-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;max-height:280px;overflow:auto}.badge{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.78rem}.sr{position:absolute;width:1px;height:1px;clip:rect(0,0,0,0);overflow:hidden;white-space:nowrap}
@media(max-width:900px){.watch{grid-template-columns:1fr}.sidebar{max-height:55vh}.chat{height:38vh}.player-shell{min-height:220px}}@media(max-width:520px){.wrap{width:min(100% - 12px,1480px)}.player-bar{gap:4px}.player-bar button{padding:8px}.gift-grid{grid-template-columns:1fr}.info h1{font-size:1.45rem}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
"""


def _gift_html(state: Any) -> str:
    if not isinstance(state, dict) or not state.get("available"):
        return "<p class='muted'>LIVE Gifts are not available for this session.</p>"
    catalogue = state.get("catalogue") if isinstance(state.get("catalogue"), list) else []
    cards = []
    for item in catalogue[:20]:
        if not isinstance(item, dict):
            continue
        name = escape(str(item.get("display_name") or "Gift"))
        cost = int(item.get("coin_cost") or 0)
        cards.append(
            f"<div class='gift'><b>{name}</b><div class='muted'>{cost} Cosmic Creation Coins</div></div>"
        )
    status = (
        "Gift sending is enabled by the authoritative economy service."
        if state.get("send_enabled")
        else escape(str(state.get("reason") or "Gift sending is currently unavailable."))
    )
    return f"<p class='muted'>{status}</p><div class='gift-grid'>{''.join(cards)}</div>"


def _battle_html(state: Any) -> str:
    if not isinstance(state, dict) or not state.get("available"):
        return ""
    status = escape(str(state.get("status") or state.get("state") or "Battle"))
    participants = state.get("participants") if isinstance(state.get("participants"), list) else []
    names = ", ".join(
        escape(str(item.get("display_name") or item.get("name") or "Creator"))
        for item in participants
        if isinstance(item, dict)
    )
    detail = f"<p class='muted'>{names}</p>" if names else ""
    return f"<div class='info'><span class='badge'>Battle</span><b> {status}</b>{detail}</div>"


@router.get("/watch/{broadcast_id}", response_class=HTMLResponse, include_in_schema=False)
def watch_page_v2(broadcast_id: str, request: Request):
    user_id = _member_id(request)
    try:
        detail = live.community.detail(broadcast_id, request, user_id)
    except Exception:
        return HTMLResponse(
            f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Shared Sky LIVE unavailable</title><style>{WATCH_CSS}</style></head><body><main class='wrap'><a class='btn' href='/live-now'>← Live Now</a><div class='info'><h1>Shared Sky LIVE unavailable</h1><p class='muted'>This session is private, restricted, removed, ended without a replay, or otherwise unavailable to this viewer.</p></div></main></body></html>",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )

    broadcast = detail["broadcast"]
    playback = _browser_descriptor(detail.get("playback"))
    replay = (
        _browser_descriptor(detail.get("replay"))
        if isinstance(detail.get("replay"), dict)
        else None
    )
    descriptor = (
        replay
        if broadcast.get("authoritative_state") == "ended"
        and replay
        and replay.get("available")
        else playback
    )
    manifest = descriptor.get("manifest_url") if descriptor else None
    tracks = "".join(
        f"<track kind='captions' src='{escape(track['src'], quote=True)}' srclang='{escape(track['srclang'], quote=True)}' label='{escape(track['label'], quote=True)}'{' default' if track['default'] else ''}>"
        for track in (descriptor.get("captions") if descriptor else []) or []
    )
    if manifest:
        player = (
            f"<video id='video' controls playsinline preload='metadata' src='{escape(str(manifest), quote=True)}'>{tracks}</video>"
            "<div id='playerStatus' class='player-status' hidden></div>"
        )
    else:
        reason = escape(str((descriptor or {}).get("reason") or "playback_not_ready"))
        label = (
            "Replay is not ready"
            if broadcast.get("authoritative_state") == "ended"
            else "LIVE playback is temporarily unavailable"
        )
        player = (
            f"<video id='video' controls playsinline preload='metadata' hidden>{tracks}</video>"
            f"<div id='playerStatus' class='player-status'><h2>{label}</h2>"
            f"<p class='muted' id='playerReason'>{reason}</p><button id='retryPlayback'>Retry playback</button></div>"
        )

    title = escape(str(broadcast.get("title") or "Shared Sky LIVE"))
    creator = escape(str(broadcast.get("creator_display_name") or "Creator"))
    description = escape(str(broadcast.get("description") or ""))
    creator_id = str(broadcast.get("creator_user_id") or "")
    viewers = int(detail.get("viewer_count") or 0)
    is_live = str(broadcast.get("authoritative_state") or "") == "live"
    signed_in = user_id is not None
    chat_enabled = bool((detail.get("chat_settings") or {}).get("enabled", True))
    member_disabled = "" if signed_in else " disabled"
    chat_disabled = "" if signed_in and chat_enabled else " disabled"
    chat_placeholder = (
        "Message Shared Sky chat"
        if signed_in and chat_enabled
        else ("Chat is disabled" if signed_in else "Sign in to chat")
    )
    qa_placeholder = "Ask the creator" if signed_in else "Sign in to ask a question"
    gift = _gift_html(detail.get("gift_display"))
    battle = _battle_html(detail.get("battle_display"))
    state_json = json.dumps(
        {
            "broadcast": broadcast,
            "playback": playback,
            "replay": replay,
            "viewer_count": viewers,
            "following": bool(detail.get("following")),
            "chat_settings": detail.get("chat_settings") or {},
            "reactions": detail.get("reactions") or {},
            "signed_in": signed_in,
        },
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} · Shared Sky</title><style>{WATCH_CSS}</style></head><body>
        <main class='wrap'><header class='top'><div><b>Shared Sky Streaming Studios</b><div class='muted'>First-party LIVE viewer network</div></div><nav class='actions' aria-label='Shared Sky navigation'><a class='btn' href='/live-now'>Live Now</a><a class='btn' href='/live-events'>Upcoming</a><a class='btn' href='/'>Command Center</a></nav></header>
        <section class='watch'><div class='stage'><div id='playerShell' class='player-shell' tabindex='0' aria-label='Shared Sky video player'>{player}<div class='player-bar'>
        <button id='playToggle' aria-label='Play or pause'>Play</button><button id='muteToggle' aria-label='Mute or unmute'>Mute</button><label class='sr' for='volume'>Volume</label><input id='volume' type='range' min='0' max='1' value='1' step='0.05'>
        <button id='jumpLive' hidden><span class='live-dot'></span>Live</button><label class='sr' for='quality'>Quality</label><select id='quality'><option value='auto'>Auto quality</option></select><button id='pip' hidden>Picture in Picture</button><button id='fullscreen'>Fullscreen</button>
        </div></div>
        <div class='info'><div class='top'><div><span class='badge'><span class='live-dot{' ' if is_live else ' off'}'></span><span id='liveLabel'>{'LIVE' if is_live else 'ENDED'}</span></span> <span id='viewers' class='muted'>{viewers} watching</span></div><div class='actions'><button id='follow'{member_disabled}>{'Following' if detail.get('following') else ('Follow' if signed_in else 'Sign in to follow')}</button><button id='like'>♥ Like</button><button id='share'>Share</button><button id='report'>Report</button></div></div>
        <h1>{title}</h1><p><b>{creator}</b></p><p class='muted'>{description}</p><div id='announce' class='sr' aria-live='polite'></div></div>{battle}
        <div class='info'><h2>Cosmic Creation Coin Gifts</h2>{gift}</div></div>
        <aside class='sidebar'><div class='tabs' role='tablist'><button role='tab' aria-selected='true' aria-controls='chatPane' id='chatTab'>Chat</button><button role='tab' aria-selected='false' aria-controls='pollPane' id='pollTab'>Polls</button><button role='tab' aria-selected='false' aria-controls='qaPane' id='qaTab'>Q&amp;A</button></div>
        <section class='pane active' id='chatPane' role='tabpanel' aria-labelledby='chatTab'><div id='chat' class='chat' role='log' aria-live='polite' aria-relevant='additions'></div><form id='chatForm' class='composer'><label class='sr' for='chatInput'>Message</label><input id='chatInput' maxlength='1000' autocomplete='off' placeholder='{escape(chat_placeholder, quote=True)}'{chat_disabled}><button{chat_disabled}>Send</button></form></section>
        <section class='pane' id='pollPane' role='tabpanel' aria-labelledby='pollTab'><div id='polls'></div></section>
        <section class='pane' id='qaPane' role='tabpanel' aria-labelledby='qaTab'><div id='questions'></div><form id='qaForm' class='composer'><label class='sr' for='qaInput'>Ask a question</label><input id='qaInput' maxlength='600' autocomplete='off' placeholder='{escape(qa_placeholder, quote=True)}'{member_disabled}><button{member_disabled}>Ask</button></form></section></aside></section></main>
        <script id='initialState' type='application/json'>{state_json}</script>
        <script>(()=>{{
        const id={json.dumps(broadcast_id)},creatorId={json.dumps(creator_id)},initial=JSON.parse(document.getElementById('initialState').textContent);let presence=null,cursor=0,retryDelay=1200,descriptor=initial.playback||{{}},ended=initial.broadcast.authoritative_state==='ended';
        const $=x=>document.getElementById(x),announce=t=>{{$('announce').textContent=t}};
        async function req(path,opt={{}}){{const headers={{...(opt.body?{{'Content-Type':'application/json'}}:{{}}),...(opt.headers||{{}})}};const r=await fetch(path,{{credentials:'same-origin',...opt,headers}});let d={{}};try{{d=await r.json()}}catch(e){{}}if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Request failed');return d}}
        function safeMedia(u){{try{{const x=new URL(u,location.origin);if(x.username||x.password)return '';return x.origin===location.origin||x.protocol==='https:'||x.protocol==='http:'?x.href:''}}catch(e){{return ''}}}}
        function setStatus(title,reason){{const v=$('video'),s=$('playerStatus');v.pause();v.hidden=true;s.hidden=false;s.innerHTML='';const h=document.createElement('h2');h.textContent=title;const p=document.createElement('p');p.className='muted';p.textContent=reason||'Playback is not ready.';const b=document.createElement('button');b.textContent='Retry playback';b.onclick=ended?refreshState:refreshPlayback;s.append(h,p,b)}}
        function populateQuality(d){{const q=$('quality'),previous=q.value;q.replaceChildren(new Option('Auto quality','auto'));for(const r of d.renditions||[]){{if(!r||!r.manifest_url)continue;const u=safeMedia(r.manifest_url);if(u)q.add(new Option(r.name||'Rendition',u))}}q.disabled=q.options.length===1;if([...q.options].some(o=>o.value===previous))q.value=previous}}
        function nativeHlsSupported(v){{return Boolean(v.canPlayType('application/vnd.apple.mpegurl')||v.canPlayType('application/x-mpegURL'))}}
        function applyDescriptor(d){{descriptor=d||{{}};populateQuality(descriptor);$('jumpLive').hidden=!descriptor.dvr;const src=descriptor.available?safeMedia(descriptor.manifest_url):'';if(!src){{setStatus(ended?'Replay is not ready':'LIVE playback is temporarily unavailable',descriptor.reason);return}}const v=$('video'),s=$('playerStatus');let path='';try{{path=new URL(src,location.origin).pathname.toLowerCase()}}catch(_){{}}if(path.endsWith('.m3u8')&&!nativeHlsSupported(v)){{setStatus('This browser needs the Shared Sky HLS runtime','Native HLS playback is unavailable in this browser; no unsupported playback is being claimed.');return}}s.hidden=true;v.hidden=false;if(v.src!==src){{v.src=src;v.load()}}retryDelay=1200}}
        async function refreshPlayback(){{try{{const d=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/playback`);applyDescriptor(d)}}catch(e){{setStatus(ended?'Replay is not ready':'Playback refresh failed',e.message)}}}}
        async function refreshState(){{try{{const d=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}`);ended=d.broadcast.authoritative_state==='ended';$('liveLabel').textContent=ended?'ENDED':'LIVE';$('viewers').textContent=(d.viewer_count||0)+' watching';if(ended&&d.replay&&d.replay.available)applyDescriptor(d.replay);else if(!ended)await refreshPlayback()}}catch(e){{announce(e.message)}}}}
        const v=$('video'),shell=$('playerShell');v.addEventListener('loadedmetadata',()=>{{shell.classList.toggle('portrait',v.videoHeight>v.videoWidth);$('playToggle').textContent=v.paused?'Play':'Pause'}});v.addEventListener('play',()=>$('playToggle').textContent='Pause');v.addEventListener('pause',()=>$('playToggle').textContent='Play');v.addEventListener('error',()=>{{setTimeout(ended?refreshState:refreshPlayback,retryDelay);retryDelay=Math.min(12000,retryDelay*1.8)}});
        $('playToggle').onclick=()=>v.paused?v.play().catch(e=>announce(e.message)):v.pause();$('muteToggle').onclick=()=>{{v.muted=!v.muted;$('muteToggle').textContent=v.muted?'Unmute':'Mute'}};$('volume').oninput=e=>{{v.volume=Number(e.target.value);if(v.volume>0)v.muted=false}};
        $('jumpLive').onclick=()=>{{if(v.seekable&&v.seekable.length){{v.currentTime=Math.max(0,v.seekable.end(v.seekable.length-1)-0.3);v.play().catch(()=>{{}})}}}};$('quality').onchange=e=>{{if(e.target.value==='auto'){{refreshPlayback();return}}const u=safeMedia(e.target.value);if(u){{const wasPaused=v.paused;v.src=u;v.load();if(!wasPaused)v.play().catch(()=>{{}})}}}};
        if(document.pictureInPictureEnabled&&v.requestPictureInPicture)$('pip').hidden=false;$('pip').onclick=async()=>{{try{{if(document.pictureInPictureElement)await document.exitPictureInPicture();else await v.requestPictureInPicture()}}catch(e){{announce('Picture in Picture is unavailable.')}}}};$('fullscreen').onclick=async()=>{{try{{if(document.fullscreenElement)await document.exitFullscreen();else await shell.requestFullscreen()}}catch(e){{announce('Fullscreen is unavailable.')}}}};
        shell.addEventListener('keydown',e=>{{if(e.target.matches('input,select,button'))return;if(e.key===' '||e.key==='k'){{e.preventDefault();$('playToggle').click()}}else if(e.key==='m')$('muteToggle').click();else if(e.key==='f')$('fullscreen').click();else if(e.key==='l'&&!$('jumpLive').hidden)$('jumpLive').click()}});
        async function join(){{if(ended)return;try{{presence=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/presence`,{{method:'POST',body:JSON.stringify({{resume_token:localStorage.getItem('sharedSkyPresence:'+id)}})}});localStorage.setItem('sharedSkyPresence:'+id,presence.resume_token);$('viewers').textContent=presence.viewer_count+' watching';refreshInteractives();setTimeout(heartbeat,20000)}}catch(e){{announce(e.message)}}}}
        async function heartbeat(){{if(!presence||ended)return;try{{const d=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/presence/heartbeat`,{{method:'POST',body:JSON.stringify({{presence_id:presence.presence_id,resume_token:presence.resume_token}})}});$('viewers').textContent=d.viewer_count+' watching';setTimeout(heartbeat,20000)}}catch(e){{presence=null;setTimeout(join,1200)}}}}
        function addMsg(m){{if(!m||document.querySelector(`[data-msg="${{CSS.escape(String(m.id))}}"]`))return;const el=document.createElement('div');el.className='msg'+(m.pinned_at?' pinned':'');el.dataset.msg=m.id;const who=document.createElement('b');who.textContent=String(m.sender_user_id||'viewer').slice(0,12);const body=document.createElement('span');body.textContent=m.deleted?' [removed] ':' '+(m.body||'');el.append(who,body);$('chat').append(el);$('chat').scrollTop=$('chat').scrollHeight}}
        async function history(){{try{{const d=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/chat`);$('chat').replaceChildren();d.messages.forEach(addMsg)}}catch(e){{}}}}
        function renderPolls(data){{const root=$('polls');root.replaceChildren();for(const p of data.polls||[]){{const box=document.createElement('article');box.className='poll';const h=document.createElement('b');h.textContent=p.question;box.append(h);const opts=document.createElement('div');opts.className='poll-options';for(const o of p.options||[]){{const b=document.createElement('button');b.textContent=o.label+(o.votes===null||o.votes===undefined?'':` · ${{o.votes}}`);b.disabled=p.state!=='live'||p.voted;b.onclick=()=>vote(p.id,o.id);opts.append(b)}}box.append(opts);root.append(box)}}if(!root.children.length)root.innerHTML='<p class="muted">No active or recent LIVE polls.</p>'}}
        function renderQuestions(data){{const root=$('questions');root.replaceChildren();for(const q of data.questions||[]){{const box=document.createElement('article');box.className='question'+(q.selected?' selected-q':'');const p=document.createElement('p');p.textContent=q.question;box.append(p);if(q.selected){{const s=document.createElement('span');s.className='badge';s.textContent='Current question';box.prepend(s)}}root.append(box)}}if(!root.children.length)root.innerHTML='<p class="muted">No approved questions yet.</p>'}}
        async function refreshInteractives(){{try{{const headers=presence?.resume_token?{{'X-Shared-Sky-Presence':presence.resume_token}}:{{}};const d=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/interactives`,{{headers}});renderPolls(d);renderQuestions(d)}}catch(e){{}}}}
        async function vote(pollId,optionId){{if(!presence)return announce('Join the LIVE before voting.');try{{await req(`/shared-sky/live/api/polls/${{encodeURIComponent(pollId)}}/vote`,{{method:'POST',body:JSON.stringify({{option_ids:[optionId],idempotency_key:crypto.randomUUID(),presence_token:presence.resume_token}})}});refreshInteractives()}}catch(e){{announce(e.message)}}}}
        function events(){{const es=new EventSource(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/events?after=${{cursor}}`,{{withCredentials:true}});const advance=e=>{{cursor=Number(e.lastEventId||cursor)}};es.addEventListener('chat.message',e=>{{advance(e);try{{addMsg(JSON.parse(e.data).payload.message)}}catch(_){{}}}});es.addEventListener('reaction.aggregate',advance);for(const name of ['poll.created','poll.voted','poll.closed','poll.cancelled','qa.approved','qa.answered','qa.removed'])es.addEventListener(name,e=>{{advance(e);refreshInteractives()}});es.onerror=()=>{{es.close();setTimeout(events,1600)}}}}
        $('chatForm').onsubmit=async e=>{{e.preventDefault();if(!initial.signed_in)return;const x=$('chatInput'),value=x.value.trim();if(!value)return;try{{const m=await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/chat`,{{method:'POST',body:JSON.stringify({{message:value,idempotency_key:crypto.randomUUID()}})}});x.value='';addMsg(m)}}catch(err){{announce(err.message)}}}};
        $('qaForm').onsubmit=async e=>{{e.preventDefault();if(!initial.signed_in)return;const x=$('qaInput'),value=x.value.trim();if(!value)return;try{{await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/qa`,{{method:'POST',body:JSON.stringify({{question:value,idempotency_key:crypto.randomUUID()}})}});x.value='';announce('Question submitted for LIVE Q&A review.')}}catch(err){{announce(err.message)}}}};
        $('like').onclick=async()=>{{if(!presence)return announce('Join the LIVE before reacting.');try{{await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/reactions`,{{method:'POST',body:JSON.stringify({{reaction:'like',amount:1,idempotency_key:crypto.randomUUID(),presence_token:presence.resume_token}})}})}}catch(e){{announce(e.message)}}}};
        $('share').onclick=async()=>{{const url=location.href;try{{if(navigator.share){{await navigator.share({{title:document.title,url}});await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/share`,{{method:'POST',body:JSON.stringify({{action:'share_sheet_open',idempotency_key:crypto.randomUUID()}})}})}}else{{await navigator.clipboard.writeText(url);await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/share`,{{method:'POST',body:JSON.stringify({{action:'copy_link',idempotency_key:crypto.randomUUID()}})}})}}announce('Share action recorded; an external post is not assumed.')}}catch(e){{}}}};
        $('report').onclick=async()=>{{const reason=prompt('What would you like to report?');if(!reason)return;try{{await req(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/report`,{{method:'POST',body:JSON.stringify({{category:'viewer_report',reason}})}});announce('Report submitted.')}}catch(e){{announce(e.message)}}}};
        $('follow').onclick=async()=>{{if(!initial.signed_in)return;const want=$('follow').textContent!=='Following';try{{await req(`/shared-sky/live/api/creators/${{encodeURIComponent(creatorId)}}/follow`,{{method:'PUT',body:JSON.stringify({{following:want,notify_live:want,idempotency_key:crypto.randomUUID()}})}});$('follow').textContent=want?'Following':'Follow';announce(want?'Following creator.':'Creator unfollowed.')}}catch(e){{announce(e.message)}}}};
        for(const [tab,pane] of [['chatTab','chatPane'],['pollTab','pollPane'],['qaTab','qaPane']])$(tab).onclick=()=>{{for(const t of ['chatTab','pollTab','qaTab'])$(t).setAttribute('aria-selected',String(t===tab));for(const p of ['chatPane','pollPane','qaPane'])$(p).classList.toggle('active',p===pane);if(pane!=='chatPane')refreshInteractives()}};
        applyDescriptor(descriptor);history();join();events();refreshInteractives();setInterval(refreshState,30000);window.addEventListener('pagehide',()=>{{if(presence)fetch(`/shared-sky/live/api/watch/${{encodeURIComponent(id)}}/presence/${{encodeURIComponent(presence.presence_id)}}?resume_token=${{encodeURIComponent(presence.resume_token)}}`,{{method:'DELETE',credentials:'same-origin',keepalive:true}}).catch(()=>{{}})}})
        }})();</script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "watch_page_v2", "watch_interactive_state", "_browser_descriptor", "_safe_url"]
