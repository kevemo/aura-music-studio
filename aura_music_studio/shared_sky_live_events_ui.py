from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized
from .shared_sky_live_events import events_store

router = APIRouter(tags=["Shared Sky Upcoming LIVE Events UI"])

CSS = """
:root{--bg:#061411;--panel:#0b2520;--panel2:#102f29;--line:#ffffff20;--text:#f3fff9;--muted:#b6cbc5;--emerald:#53e3af;--sardonyx:#d58462;--gold:#f2cf79;--danger:#ff9cae}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#0b5e4955,transparent 30%),radial-gradient(circle at 93% 0,#b85f4050,transparent 25%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh}a{color:inherit}.wrap{width:min(1240px,calc(100% - 28px));margin:auto;padding:20px 0 60px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.brand{font-weight:950}.muted{color:var(--muted);line-height:1.5}.btn,button{border:1px solid var(--line);background:#ffffff09;color:var(--text);padding:9px 12px;border-radius:999px;text-decoration:none;font:inherit;font-weight:800;cursor:pointer}.primary{background:linear-gradient(115deg,var(--emerald),var(--gold));color:#052016;border:0}.filters{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.filters a{border:1px solid var(--line);background:#ffffff08;padding:8px 10px;border-radius:999px;text-decoration:none}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}.card{background:#0b2520e8;border:1px solid var(--line);border-radius:18px;overflow:hidden}.thumb{aspect-ratio:16/9;display:grid;place-items:center;background:linear-gradient(135deg,#0c6e55,#7d4432);overflow:hidden}.thumb img{width:100%;height:100%;object-fit:cover}.thumb .date{font-size:1.1rem;font-weight:950;padding:12px;text-align:center}.body{padding:14px}.meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:.82rem}.tag{border:1px solid var(--line);border-radius:999px;padding:3px 7px}.detail{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,.7fr);gap:18px}.hero{border:1px solid var(--line);border-radius:22px;overflow:hidden;background:var(--panel)}.hero-media{aspect-ratio:16/9;background:linear-gradient(135deg,#0c6e55,#7d4432);display:grid;place-items:center}.hero-media img{width:100%;height:100%;object-fit:cover}.panel{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:16px}.status{padding:10px 12px;border-radius:12px;background:#ffffff08}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}input,select{background:#061411;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:9px;font:inherit}.empty{padding:36px;border:1px solid var(--line);border-radius:18px;background:var(--panel)}
@media(max-width:760px){.detail{grid-template-columns:1fr}.wrap{width:min(100% - 18px,1240px)}.grid{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
"""


def _member_id(request: Request) -> str | None:
    member = live.optional_member(request)
    return member.user_id if member else None


def _card(item: dict) -> str:
    title = escape(str(item.get("title") or "Scheduled LIVE"))
    creator = escape(str(item.get("creator_display_name") or "Creator"))
    category = escape(str(item.get("category") or "events").replace("-", " ").title())
    language = escape(str(item.get("language") or "en").upper())
    schedule_id = escape(str(item.get("schedule_id") or ""), quote=True)
    start_at = escape(str(item.get("start_at") or ""), quote=True)
    thumbnail = str(item.get("thumbnail_url") or "")
    if thumbnail:
        media = f"<img src='{escape(thumbnail, quote=True)}' alt=''>"
    else:
        media = f"<div class='date'><time datetime='{start_at}' data-countdown='{start_at}'>Scheduled LIVE</time></div>"
    tags = "".join(f"<span class='tag'>{escape(str(tag))}</span>" for tag in item.get("tags", [])[:4])
    return f"""<article class='card'><a href='/live-events/{schedule_id}' style='text-decoration:none'>
    <div class='thumb'>{media}</div><div class='body'><div class='meta'><span>{category}</span><span>·</span><span>{language}</span></div>
    <h2 style='font-size:1.05rem'>{title}</h2><p class='muted'>{creator}</p><div class='meta'>{tags}</div>
    <p><time datetime='{start_at}' data-date='{start_at}'>{escape(str(item.get('start_at') or ''))}</time></p></div></a></article>"""


@router.get("/live-events", response_class=HTMLResponse, include_in_schema=False)
def upcoming_events_page(request: Request, q: str = "", category: str = "all", following: bool = False):
    user_id = _member_id(request)
    rows = events_store.list_events(
        user_id,
        q=q,
        category=category,
        following_only=following,
        limit=100,
        owner=owner_session_authorized(request),
    )
    cards = "".join(_card(item) for item in rows) or (
        "<div class='empty'><h2>No published upcoming Shared Sky LIVE events match this view.</h2>"
        "<p class='muted'>Private creator schedules never appear here unless the creator explicitly publishes them.</p></div>"
    )
    cats = ["all", "music", "gaming", "video-film", "art-image", "education", "talk-community", "battles", "events"]
    filters = "".join(
        f"<a href='/live-events?category={escape(cat, quote=True)}'>{escape(cat.replace('-', ' ').title())}</a>"
        for cat in cats
    )
    following_link = "<a href='/live-events?following=true'>Following</a>" if user_id else ""
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Upcoming LIVE · Shared Sky</title><style>{CSS}</style></head><body><main class='wrap'>
        <header class='top'><div><div class='brand'>Shared Sky Streaming Studios</div><div class='muted'>Elevate Souls Productions</div></div>
        <nav class='top' aria-label='Shared Sky viewer navigation'><a class='btn' href='/live-now'>Live Now</a><a class='btn' href='/'>Command Center</a></nav></header>
        <h1>Upcoming LIVE</h1><p class='muted'>Published Shared Sky events from creators you can access. Operational studio schedules stay private until explicitly published.</p>
        <form class='top' action='/live-events' method='get'><input type='hidden' name='category' value='{escape(category, quote=True)}'><label>Search events <input name='q' value='{escape(q, quote=True)}' maxlength='120'></label><button class='btn'>Search</button></form>
        <nav class='filters' aria-label='Upcoming LIVE categories'>{filters}{following_link}</nav><section class='grid'>{cards}</section></main>
        <script>(()=>{{const fmt=new Intl.DateTimeFormat(undefined,{{dateStyle:'medium',timeStyle:'short'}});document.querySelectorAll('[data-date]').forEach(el=>{{const d=new Date(el.dataset.date);if(!Number.isNaN(d.valueOf()))el.textContent=fmt.format(d)}})}})();</script>
        </body></html>""",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/live-events/{schedule_id}", response_class=HTMLResponse, include_in_schema=False)
def upcoming_event_page(schedule_id: str, request: Request):
    user_id = _member_id(request)
    try:
        item = events_store.event(
            schedule_id,
            user_id,
            direct=True,
            owner=owner_session_authorized(request),
        )
    except Exception:
        return HTMLResponse(
            f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Shared Sky event unavailable</title><style>{CSS}</style></head><body><main class='wrap'><a class='btn' href='/live-events'>← Upcoming LIVE</a><div class='empty'><h1>Shared Sky event unavailable</h1><p class='muted'>This event is private, no longer published, restricted, or unavailable.</p></div></main></body></html>",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    title = escape(str(item["title"]))
    creator = escape(str(item["creator_display_name"]))
    description = escape(str(item.get("description") or ""))
    category = escape(str(item.get("category") or "events").replace("-", " ").title())
    start_at = escape(str(item.get("start_at") or ""), quote=True)
    thumbnail = str(item.get("thumbnail_url") or "")
    media = f"<img src='{escape(thumbnail, quote=True)}' alt=''>" if thumbnail else "<div class='date'>Upcoming Shared Sky LIVE</div>"
    reminder = item.get("reminder") or {"enabled": False, "lead_minutes": 15}
    reminder_label = "Update reminder" if reminder.get("enabled") else "Remind me"
    sign_in_hint = "" if user_id else "<p class='muted'>Sign in to set a reminder.</p>"
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>{title} · Upcoming Shared Sky LIVE</title><style>{CSS}</style></head><body><main class='wrap'>
        <header class='top'><a class='btn' href='/live-events'>← Upcoming LIVE</a><a class='btn' href='/live-now'>Live Now</a></header>
        <section class='detail'><article class='hero'><div class='hero-media'>{media}</div><div class='body'><div class='meta'><span class='tag'>{category}</span><span>{escape(str(item.get('language') or 'en').upper())}</span></div>
        <h1>{title}</h1><p><b>{creator}</b></p><p class='muted'>{description}</p><p><time id='start' datetime='{start_at}'>{escape(str(item.get('start_at') or ''))}</time></p><div id='countdown' class='status' aria-live='polite'>Scheduled LIVE</div></div></article>
        <aside class='panel'><h2>LIVE reminder</h2><p class='muted'>Choose when you want an in-app Aura reminder. Email/push delivery is not assumed.</p>{sign_in_hint}
        <label for='lead'>Remind me before LIVE</label><select id='lead'><option value='5'>5 minutes</option><option value='15'>15 minutes</option><option value='30'>30 minutes</option><option value='60'>1 hour</option><option value='1440'>1 day</option></select>
        <div class='top' style='margin-top:12px'><button class='primary' id='remind'>{escape(reminder_label)}</button><button id='disable'>Turn off</button></div><div id='announce' class='sr' aria-live='polite'></div></aside></section></main>
        <script>(()=>{{const id={schedule_id!r},start=new Date({str(item.get('start_at') or '')!r}),announce=t=>document.getElementById('announce').textContent=t;const time=document.getElementById('start');if(!Number.isNaN(start.valueOf()))time.textContent=new Intl.DateTimeFormat(undefined,{{dateStyle:'full',timeStyle:'short'}}).format(start);const c=document.getElementById('countdown');function tick(){{const ms=start-Date.now();if(ms<=0){{c.textContent='Scheduled start time reached';return}}const mins=Math.ceil(ms/60000);c.textContent=mins<60?`Starts in about ${{mins}} minute${{mins===1?'':'s'}}`:mins<1440?`Starts in about ${{Math.ceil(mins/60)}} hour${{Math.ceil(mins/60)===1?'':'s'}}`:`Starts in about ${{Math.ceil(mins/1440)}} day${{Math.ceil(mins/1440)===1?'':'s'}}`}}tick();setInterval(tick,60000);
        async function setReminder(enabled){{try{{const lead=Number(document.getElementById('lead').value||15);const r=await fetch(`/shared-sky/live/api/events/${{encodeURIComponent(id)}}/reminder`,{{method:'PUT',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{enabled,lead_minutes:lead}})}});let d={{}};try{{d=await r.json()}}catch(e){{}}if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Sign in or event access is required');announce(enabled?'Reminder saved.':'Reminder turned off.')}}catch(e){{announce(e.message)}}}}document.getElementById('remind').onclick=()=>setReminder(true);document.getElementById('disable').onclick=()=>setReminder(false);document.getElementById('lead').value={int(reminder.get('lead_minutes') or 15)!r};}})();</script>
        </body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
