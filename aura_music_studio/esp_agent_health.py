from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_agent_roster import AgentRosterStore, _require_agent, rosters

router = APIRouter(tags=["ESP Agent Creator Health"])
HealthState = Literal["on_track", "watch", "needs_action"]


def creator_health(row: dict) -> dict:
    """Explainable creator-support signals only; never an automated penalty score."""
    reasons: list[str] = []
    positives: list[str] = []
    training = float(row.get("training_average") or 0)
    submissions = int(row.get("progress_submissions") or 0)
    plan_pct = float((row.get("plan") or {}).get("completion_percent") or 0)
    followups = int(row.get("open_followups") or 0)

    if training >= 75:
        positives.append("Training completion is strong")
    elif training < 25:
        reasons.append("Training completion is below 25%")
    else:
        reasons.append("Training pathway is still in progress")

    if submissions == 0:
        reasons.append("No LIVE/video analysis has been submitted yet")
    else:
        positives.append(f"{submissions} creator progress submission(s) recorded")

    if plan_pct >= 80:
        positives.append("Activation/My Plan completion is strong")
    elif plan_pct < 30:
        reasons.append("My Plan completion is below 30%")
    else:
        reasons.append("My Plan still has unfinished actions")

    if followups >= 3:
        reasons.append(f"{followups} agent follow-ups are still open")
    elif followups:
        reasons.append(f"{followups} agent follow-up(s) remain open")

    if training < 25 or submissions == 0 or plan_pct < 30 or followups >= 3:
        state: HealthState = "needs_action"
    elif reasons:
        state = "watch"
    else:
        state = "on_track"
    return {
        "state": state,
        "reasons": reasons,
        "positives": positives,
        "explainable": True,
        "automated_penalty": False,
        "source_scope": "assigned_creator_operational_data_only",
    }


class HealthFollowUpRequest(BaseModel):
    creator_user_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="Creator health check-in", min_length=1, max_length=240)
    note: str = Field(default="", max_length=2000)
    due_at: str | None = Field(default=None, max_length=80)


def health_queue(store: AgentRosterStore, agent_user_id: str) -> list[dict]:
    order = {"needs_action": 0, "watch": 1, "on_track": 2}
    rows: list[dict] = []
    for creator in store.roster(agent_user_id):
        item = dict(creator)
        item["health"] = creator_health(item)
        rows.append(item)
    rows.sort(key=lambda item: (order[item["health"]["state"]], str(item.get("display_name") or "").lower()))
    return rows


@router.get("/command-center/api/agent/health")
def agent_health_api(request: Request):
    member, _membership = _require_agent(request)
    rows = health_queue(rosters, member.user_id)
    return {
        "creators": rows,
        "counts": {
            "needs_action": sum(1 for row in rows if row["health"]["state"] == "needs_action"),
            "watch": sum(1 for row in rows if row["health"]["state"] == "watch"),
            "on_track": sum(1 for row in rows if row["health"]["state"] == "on_track"),
        },
        "automated_penalties": False,
        "directory_scope": "explicit_assignments_only",
    }


@router.post("/command-center/api/agent/health/follow-up")
def health_follow_up(body: HealthFollowUpRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        followup = rosters.add_followup(
            member.user_id,
            body.creator_user_id,
            title=body.title,
            note=body.note,
            due_at=body.due_at,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"followup": followup}


CSS = """
:root{--line:#ffffff1d;--muted:#c2bfd1;--gold:#efc86f;--violet:#9f70ff;--good:#78dfa7;--warn:#ffd17b;--bad:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#43195b,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1240px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.2rem);letter-spacing:-.055em;line-height:.94;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.metric,.card{border:1px solid var(--line);border-radius:15px;background:#14101ceb;padding:14px}.metric b{display:block;font-size:1.5rem}.card{margin:9px 0}.state,.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem}.needs_action{color:var(--bad)}.watch{color:var(--warn)}.on_track{color:var(--good)}.chips{display:flex;gap:5px;flex-wrap:wrap}@media(max-width:700px){.summary{grid-template-columns:1fr}}
"""
SCRIPT = """
const API='/command-center/api/agent';const $=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]))}async function req(u,o={}){const r=await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Request failed');return d}function pretty(v){return String(v||'').replace(/_/g,' ')}function render(d){document.getElementById('summary').innerHTML=`<div class=\"metric\"><span class=\"muted\">Needs action</span><b>${d.counts.needs_action||0}</b></div><div class=\"metric\"><span class=\"muted\">Watch</span><b>${d.counts.watch||0}</b></div><div class=\"metric\"><span class=\"muted\">On track</span><b>${d.counts.on_track||0}</b></div>`;document.getElementById('queue').innerHTML=(d.creators||[]).map(r=>`<article class=\"card\"><div class=\"row\"><div><span class=\"state ${esc(r.health.state)}\">${esc(pretty(r.health.state))}</span><h2>${esc(r.display_name||r.tiktok_handle||'Assigned creator')}</h2><p class=\"muted\">@${esc(r.tiktok_handle||'')} · ${esc(r.niche||'Niche not set')}</p></div><button class=\"btn\" onclick=\"follow('${esc(r.creator_user_id)}')\">Create follow-up</button></div><div class=\"chips\">${(r.health.reasons||[]).map(x=>`<span class=\"pill\">${esc(x)}</span>`).join('')}${(r.health.positives||[]).map(x=>`<span class=\"pill\">✓ ${esc(x)}</span>`).join('')}</div></article>`).join('')||'<div class=\"card muted\">No assigned creators.</div>'}async function follow(id){const title=prompt('Follow-up title:','Creator health check-in')||'';if(!title.trim())return;await req(API+'/health/follow-up',{method:'POST',body:JSON.stringify({creator_user_id:id,title,note:'',due_at:null})});load()}async function load(){try{render(await req(API+'/health'))}catch(e){document.getElementById('queue').textContent=e.message}}load();
"""


@router.get("/command-center/agent/health", response_class=HTMLResponse, include_in_schema=False)
def agent_health_page(request: Request):
    _require_agent(request)
    return HTMLResponse(f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Creator Health Queue</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div><h1>Assigned Creator Health Queue</h1><p class='muted'>Explainable support signals from assigned ESP creator operational data only. No automated penalties or disciplinary decisions.</p></div><div><a class='btn' href='/command-center/agent/roster'>Roster</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><section id='summary' class='summary'></section><section id='queue'><div class='card muted'>Loading…</div></section></main><script>{SCRIPT}</script></body></html>""")


__all__ = ["router", "creator_health", "health_queue"]
