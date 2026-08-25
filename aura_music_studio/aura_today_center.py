from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from .aura_chat_store import AuraChatStore
from .aura_notifications import NotificationStore, notification_store
from .aura_tasks import AuraTaskStore, task_store
from .aura_workspace_briefing import build_workspace_briefing

router = APIRouter(tags=["Aura Today"])
store = AuraChatStore()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def build_today_snapshot(
    user_id: str,
    *,
    thread_id: str | None = None,
    limit: int = 6,
    chat_store: AuraChatStore | None = None,
    tasks: AuraTaskStore | None = None,
    notifications: NotificationStore | None = None,
    briefing_builder=build_workspace_briefing,
) -> dict:
    chat = chat_store or store
    task_db = tasks or task_store
    inbox = notifications or notification_store
    cap = max(1, min(int(limit or 6), 12))

    thread = None
    pinned_project = None
    if thread_id:
        thread = chat.thread(user_id, thread_id)
        if not thread:
            raise KeyError(thread_id)
        pinned_project = str(thread.get("project_name") or "").strip() or None

    workspace: dict
    try:
        workspace = briefing_builder(
            user_id,
            hours=24,
            drive_query=pinned_project,
            limit=cap,
        )
        workspace["connected"] = True
    except PermissionError as exc:
        workspace = {
            "connected": False,
            "read_only": True,
            "calendar": {"available": False, "events": []},
            "gmail": {"available": False, "messages": []},
            "drive": {"available": False, "searched": False, "files": []},
            "service_errors": {},
            "message": str(exc)[:500],
            "email_bodies_opened": False,
            "drive_files_downloaded": False,
            "tokens_exposed": False,
        }

    active_tasks = [
        row
        for row in task_db.list(user_id, limit=100)
        if bool(row.get("enabled")) and str(row.get("status") or "") == "active"
    ][:cap]
    unread = inbox.list(user_id, unread_only=True, limit=cap)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id or None,
        "thread_title": (thread or {}).get("title"),
        "pinned_project": pinned_project,
        "workspace": workspace,
        "tasks": active_tasks,
        "notifications": unread,
        "unread_notification_count": inbox.unread_count(user_id),
        "privacy": {
            "read_only_connected_services": True,
            "email_bodies_opened": False,
            "drive_bulk_scan": False,
            "drive_files_downloaded": False,
            "tokens_exposed": False,
            "project_writes": False,
        },
    }


@router.get("/aura-intelligence/api/today")
def today_snapshot(request: Request, thread_id: str = "", limit: int = 6):
    member = _member(request)
    try:
        return build_today_snapshot(
            member.user_id,
            thread_id=thread_id.strip() or None,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc


TODAY_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  const $=id=>document.getElementById(id);
  function esc(v){try{return window.esc(v)}catch(_){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}}
  async function request(url){const r=await fetch(url,{credentials:'same-origin'});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function when(value){if(!value)return '—';try{return new Date(value).toLocaleString()}catch(_){return String(value)}}
  function card(title,body){return `<section style="border:1px solid #ffffff18;border-radius:14px;padding:11px;background:#ffffff04;margin:9px 0"><b>${title}</b>${body}</section>`}
  function calendarRows(rows){return rows.length?rows.map(e=>`<div class="mem" style="margin:7px 0"><b>${esc(e.summary||e.title||'(Untitled event)')}</b><div class="muted" style="font-size:.68rem">${esc(when(e.start?.dateTime||e.start?.date||e.start))}${e.location?' · '+esc(e.location):''}</div>${e.id?`<div style="display:flex;gap:5px;margin-top:6px"><button class="mini" data-aura-event-detail="${esc(e.id)}">Details</button><button class="mini" data-aura-event-prepare="${esc(e.id)}">Prepare with Aura</button></div>`:''}</div>`).join(''):'<p class="muted">Nothing upcoming in this window.</p>'}
  function mailRows(rows){return rows.length?rows.map(m=>`<div class="mem" style="margin:7px 0"><b>${esc(m.subject||'(No subject)')}</b><div class="muted" style="font-size:.68rem">${esc(m.from||m.sender||'')}</div><div style="font-size:.74rem;line-height:1.4;margin-top:4px">${esc(m.snippet||'')}</div>${m.id?`<button class="mini" data-aura-mail="${esc(m.id)}">Ask Aura about this</button>`:''}</div>`).join(''):'<p class="muted">No matching unread/recent messages.</p>'}
  function taskRows(rows){return rows.length?rows.map(t=>`<div class="mem" style="margin:7px 0"><b>${esc(t.title)}</b><div class="muted" style="font-size:.68rem">${esc(t.kind)} · next ${esc(when(t.next_run_at))}</div></div>`).join(''):'<p class="muted">No active Aura Tasks.</p>'}
  function noteRows(rows){return rows.length?rows.map(n=>`<div class="mem" style="margin:7px 0;cursor:${n.thread_id?'pointer':'default'}" ${n.thread_id?`data-aura-today-thread="${esc(n.thread_id)}"`:''}><b>● ${esc(n.title)}</b><div style="font-size:.73rem;line-height:1.4;margin-top:4px">${esc(n.body||'')}</div></div>`).join(''):'<p class="muted">No unread notifications.</p>'}
  function driveRows(drive,pinned){if(!drive?.available)return '<p class="muted">Drive is not connected.</p>';if(!pinned)return '<p class="muted">No project is pinned, so Aura did not scan Drive.</p>';const rows=drive.files||[];return rows.length?rows.map(f=>`<div class="mem" style="margin:7px 0"><b>${esc(f.name||'Drive file')}</b><div class="muted" style="font-size:.68rem">${esc(f.mime_type||'')} · ${esc(when(f.modified_time))}</div></div>`).join(''):`<p class="muted">No Drive files matched “${esc(pinned)}”.</p>`}
  function eventDetail(data){const attendees=data.attendees||[],conf=data.conference||{};return card('📅 Event detail',`<div style="margin-top:7px"><b>${esc(data.summary||'(Untitled event)')}</b><div class="muted" style="font-size:.7rem">${esc(when(data.start?.dateTime||data.start?.date||data.start))} → ${esc(when(data.end?.dateTime||data.end?.date||data.end))}</div>${data.location?`<div style="font-size:.73rem;margin-top:5px">📍 ${esc(data.location)}</div>`:''}${data.description?`<div style="font-size:.74rem;line-height:1.45;white-space:pre-wrap;margin-top:7px">${esc(data.description)}</div>`:''}<div class="muted" style="font-size:.68rem;margin-top:7px">${attendees.length} attendee${attendees.length===1?'':'s'}${conf.solution_name?' · '+esc(conf.solution_name):''}</div>${attendees.length?`<div style="font-size:.68rem;margin-top:4px">${attendees.slice(0,8).map(a=>esc(a.display_name||a.email||'attendee')+(a.response_status?' · '+esc(a.response_status):'')).join('<br>')}</div>`:''}<div class="muted" style="font-size:.64rem;margin-top:7px">Read-only detail. No Calendar changes were made.</div></div>`)}

  function ensureDrawer(){
    let d=$('auraTodayDrawer');if(d)return d;
    d=document.createElement('div');d.id='auraTodayDrawer';d.className='drawer';
    d.innerHTML=`<div style="display:flex;align-items:center;gap:8px"><div style="flex:1"><b style="font-size:1.05rem">Aura Today</b><div class="muted" style="font-size:.7rem">Your private at-a-glance workspace</div></div><button class="btn" id="auraTodayClose">✕</button></div><div style="display:flex;gap:6px;margin:12px 0"><button class="mini" id="auraTodayRefresh">Refresh</button><button class="mini" id="auraTodayBrief">Ask Aura for a full brief</button></div><div id="auraTodayEventDetail"></div><div id="auraTodayRows"></div>`;
    document.body.append(d);
    $('auraTodayClose').onclick=()=>d.classList.remove('open');
    $('auraTodayRefresh').onclick=loadToday;
    $('auraTodayBrief').onclick=()=>{d.classList.remove('open');if(typeof send==='function')send('Give me my workspace briefing for today. Tell me what needs my attention.');};
    d.addEventListener('click',async event=>{
      const thread=event.target.closest('[data-aura-today-thread]');if(thread&&typeof openThread==='function'){d.classList.remove('open');await openThread(thread.dataset.auraTodayThread);return}
      const mail=event.target.closest('[data-aura-mail]');if(mail&&typeof send==='function'){d.classList.remove('open');send(`Read the Gmail message with id ${mail.dataset.auraMail} and summarize what I need to know. Do not send or modify anything.`);return}
      const detail=event.target.closest('[data-aura-event-detail]');if(detail){try{const data=await request(`${api}/connectors/google/calendar/events/${encodeURIComponent(detail.dataset.auraEventDetail)}`);$('auraTodayEventDetail').innerHTML=eventDetail(data)}catch(e){toast(e.message,true)}return}
      const prepare=event.target.closest('[data-aura-event-prepare]');if(prepare&&typeof send==='function'){d.classList.remove('open');send(`Read the Google Calendar event with id ${prepare.dataset.auraEventPrepare}. Help me prepare for it using only the event details and our existing conversation context. Do not modify the calendar.`)}
    });
    return d;
  }
  function render(data){
    const w=data.workspace||{},connected=!!w.connected,pinned=data.pinned_project||'';
    const header=`<div style="padding:11px;border:1px solid #ffffff18;border-radius:14px;background:linear-gradient(135deg,#9b70ff16,#58dfff0c)"><div style="display:flex;justify-content:space-between;gap:8px"><b>${connected?'Connected workspace':'Aura workspace'}</b><span class="muted" style="font-size:.68rem">${data.unread_notification_count||0} unread</span></div><div class="muted" style="font-size:.7rem;margin-top:5px">${pinned?'📌 '+esc(pinned):'No creative project pinned'} · read-only overview</div></div>`;
    const noGoogle=connected?'':card('Google services','<p class="muted">Google is not connected to Aura. Tasks, notifications and your pinned project still work here. Open Connectors to add read-only Calendar/Gmail/Drive access.</p>');
    $('auraTodayEventDetail').innerHTML='';
    $('auraTodayRows').innerHTML=header+noGoogle+
      card('📅 Next 24 hours',calendarRows(w.calendar?.events||[]))+
      card('✉️ Gmail attention',mailRows(w.gmail?.messages||[]))+
      card('⏰ Active Aura Tasks',taskRows(data.tasks||[]))+
      card('🔔 Unread notifications',noteRows(data.notifications||[]))+
      card('📁 Pinned project Drive matches',driveRows(w.drive||{},pinned))+
      `<p class="muted" style="font-size:.65rem;line-height:1.45">Privacy: Today Center does not open email bodies automatically, bulk-scan Drive, download files, expose OAuth tokens, edit projects, send email, create calendar events or publish social content.</p>`;
  }
  async function loadToday(){try{const tid=(typeof current!=='undefined'&&current)?`&thread_id=${encodeURIComponent(current)}`:'';const data=await request(`${api}/today?limit=6${tid}`);render(data)}catch(e){toast(e.message,true)}}
  const foot=document.querySelector('.sideFoot');if(foot&&!$('auraTodayButton')){const b=document.createElement('button');b.id='auraTodayButton';b.className='btn';b.textContent='☀ Aura Today';b.onclick=async()=>{ensureDrawer().classList.add('open');await loadToday()};foot.prepend(b)}
})();
"""


@router.get("/aura-intelligence/today-ui.js")
def today_ui_script():
    return Response(content=TODAY_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})


__all__ = ["router", "build_today_snapshot", "TODAY_SCRIPT"]
