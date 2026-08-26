from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .branding import ENDORSEMENT
from .esp_command_center import esp
from .esp_niche import EspNicheStore, require_esp_hub_member
from .esp_progress import EspProgressStore

router = APIRouter(tags=["ESP Creator My Plan"])
PlanPhase = Literal["activate", "build", "optimise", "reactivate", "technical_help"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_creator(membership: dict) -> bool:
    return membership.get("status") == "owner" or (membership.get("roles") or "").lower() in {"creator", "both"}


def _require_creator(request: Request):
    member, membership = require_esp_hub_member(request)
    if not _is_creator(membership):
        raise HTTPException(403, "ESP Creator access is required for My Plan")
    return member, membership


class ActionEvidence(BaseModel):
    evidence_note: str = Field(default="", max_length=1500)


class CreatorPlanStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
        self.niches = EspNicheStore(esp)
        self.progress = EspProgressStore(esp)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_creator_plans (
                    user_id TEXT PRIMARY KEY,
                    phase TEXT NOT NULL DEFAULT 'activate',
                    monthly_objective TEXT NOT NULL DEFAULT '',
                    live_days_target INTEGER NOT NULL DEFAULT 8,
                    live_hours_target REAL NOT NULL DEFAULT 20,
                    videos_per_week INTEGER NOT NULL DEFAULT 15,
                    engagement_minutes_daily INTEGER NOT NULL DEFAULT 60,
                    review_cycle_days INTEGER NOT NULL DEFAULT 30,
                    created_by TEXT NOT NULL DEFAULT 'Aura / ESP',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_creator_plan_actions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    day_number INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    evidence_required INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open',
                    evidence_note TEXT NOT NULL DEFAULT '',
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_plan_actions_user
                    ON esp_creator_plan_actions(user_id, day_number, created_at);
                """
            )

    def _default_actions(self, user_id: str, profile: dict | None) -> list[dict]:
        niche_title = ((profile or {}).get("catalog") or {}).get("title") or "your niche"
        niche_training = list(((profile or {}).get("catalog") or {}).get("training") or [])
        niche_action = niche_training[0] if niche_training else f"Build a repeatable content format for {niche_title}."
        rows = [
            (1, "LIVE", "Set your weekly LIVE schedule", "Choose reliable LIVE days/times and protect them in your weekly routine.", False),
            (2, "LIVE", "Build your repeatable LIVE show structure", "Plan your opening hook, repeatable segments, audience interaction and re-introduction loops.", True),
            (3, "Content", "Build this week's short-form content plan", "Plan consistent discoverable videos around your niche, LIVE schedule and strongest recurring topics.", True),
            (4, "Niche", f"Complete one {niche_title} growth action", niche_action, True),
            (5, "Activation", "Use the pre-LIVE activation funnel", "Before a scheduled LIVE, publish a clear upcoming-LIVE video and engage relevant niche communities.", True),
            (6, "Analytics", "Upload LIVE or video performance data", "Add analytics in ESP Creator Progress so Aura can compare performance and recommend the next adjustment.", True),
            (7, "Review", "Review the week and choose one improvement", "Record what improved, what underperformed and the single highest-priority adjustment for the next seven days.", True),
        ]
        now = _now()
        return [
            {
                "id": uuid4().hex,
                "user_id": user_id,
                "day_number": day,
                "category": category,
                "title": title,
                "detail": detail,
                "evidence_required": int(required),
                "status": "open",
                "evidence_note": "",
                "completed_at": None,
                "created_at": now,
                "updated_at": now,
            }
            for day, category, title, detail, required in rows
        ]

    def ensure(self, user_id: str) -> dict:
        profile = self.niches.get(user_id)
        goals = list((profile or {}).get("goals") or [])
        objective = goals[0] if goals else "Build a consistent, measurable LIVE and content system"
        with self._connect() as con:
            plan = con.execute("SELECT * FROM esp_creator_plans WHERE user_id=?", (user_id,)).fetchone()
            if plan is None:
                now = _now()
                con.execute(
                    """INSERT INTO esp_creator_plans
                       (user_id,phase,monthly_objective,live_days_target,live_hours_target,videos_per_week,
                        engagement_minutes_daily,review_cycle_days,created_by,created_at,updated_at)
                       VALUES (?,'activate',?,8,20,15,60,30,'Aura / ESP',?,?)""",
                    (user_id, objective[:500], now, now),
                )
            action_count = con.execute("SELECT COUNT(*) FROM esp_creator_plan_actions WHERE user_id=?", (user_id,)).fetchone()[0]
            if not action_count:
                for row in self._default_actions(user_id, profile):
                    con.execute(
                        """INSERT INTO esp_creator_plan_actions
                           (id,user_id,day_number,category,title,detail,evidence_required,status,evidence_note,completed_at,created_at,updated_at)
                           VALUES (:id,:user_id,:day_number,:category,:title,:detail,:evidence_required,:status,:evidence_note,:completed_at,:created_at,:updated_at)""",
                        row,
                    )
        return self.get(user_id)

    def get(self, user_id: str) -> dict:
        with self._connect() as con:
            plan = con.execute("SELECT * FROM esp_creator_plans WHERE user_id=?", (user_id,)).fetchone()
            actions = con.execute(
                "SELECT * FROM esp_creator_plan_actions WHERE user_id=? ORDER BY day_number,created_at",
                (user_id,),
            ).fetchall()
        if plan is None:
            return self.ensure(user_id)
        result = dict(plan)
        result["actions"] = [dict(row) | {"evidence_required": bool(row["evidence_required"])} for row in actions]
        done = sum(1 for row in result["actions"] if row["status"] == "done")
        result["completion"] = {
            "done": done,
            "total": len(result["actions"]),
            "percent": round((done / len(result["actions"]) * 100), 1) if result["actions"] else 0.0,
        }
        result["aura_guidance"] = self._guidance(user_id, result)
        return result

    def _guidance(self, user_id: str, plan: dict) -> list[str]:
        guidance: list[str] = []
        profile = self.niches.get(user_id)
        summary = self.progress.summary(user_id)
        completion = plan.get("completion") or {}
        if int(summary.get("total") or 0) == 0:
            guidance.append("Add your first LIVE or video analysis in Creator Progress so Aura can coach from your real data.")
        if float(completion.get("percent") or 0) < 50:
            guidance.append("Prioritise the next unfinished seven-day action before adding more tactics; execution data is more useful than a longer checklist.")
        elif float(completion.get("percent") or 0) < 100:
            guidance.append("Your activation pathway is moving. Complete the remaining actions, then compare the newest analytics with your starting point.")
        else:
            guidance.append("Seven-day activation is complete. Use your latest performance evidence to move into the next build/optimise cycle with one measurable objective.")
        if profile:
            definition = profile.get("catalog") or {}
            training = list(definition.get("training") or [])
            if training:
                guidance.append(f"Niche priority: {training[0]}")
        return guidance[:5]

    def set_action(self, user_id: str, action_id: str, *, done: bool, evidence_note: str = "") -> dict:
        clean = " ".join((evidence_note or "").split())[:1500]
        with self._connect() as con:
            row = con.execute(
                "SELECT evidence_required FROM esp_creator_plan_actions WHERE id=? AND user_id=?",
                (action_id, user_id),
            ).fetchone()
            if row is None:
                raise KeyError(action_id)
            if done and row["evidence_required"] and not clean:
                raise ValueError("Add a short evidence/check-in note before completing this action")
            now = _now()
            con.execute(
                """UPDATE esp_creator_plan_actions SET status=?,evidence_note=?,completed_at=?,updated_at=?
                   WHERE id=? AND user_id=?""",
                ("done" if done else "open", clean if done else "", now if done else None, now, action_id, user_id),
            )
        return self.get(user_id)


plans = CreatorPlanStore()


@router.get("/command-center/api/my-plan")
def my_plan_api(request: Request):
    member, _membership = _require_creator(request)
    return {"plan": plans.ensure(member.user_id), "subscription_independent_from_esp": True}


@router.post("/command-center/api/my-plan/actions/{action_id}/complete")
def complete_action(action_id: str, body: ActionEvidence, request: Request):
    member, _membership = _require_creator(request)
    try:
        return {"plan": plans.set_action(member.user_id, action_id, done=True, evidence_note=body.evidence_note)}
    except KeyError as exc:
        raise HTTPException(404, "Plan action not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/my-plan/actions/{action_id}/reopen")
def reopen_action(action_id: str, request: Request):
    member, _membership = _require_creator(request)
    try:
        return {"plan": plans.set_action(member.user_id, action_id, done=False)}
    except KeyError as exc:
        raise HTTPException(404, "Plan action not found") from exc


CSS = r"""
:root{--line:#ffffff1d;--muted:#c3bfd2;--gold:#efc76d;--violet:#a16fff;--good:#75dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#4b195d,transparent 30%),#07050d;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1120px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5rem);line-height:.94;letter-spacing:-.055em;margin:.15em 0 .2em}.muted{color:var(--muted);line-height:1.55}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric,.card,.action{border:1px solid var(--line);border-radius:16px;background:#120d1aeb;padding:14px}.metric b{display:block;font-size:1.45rem}.card{margin:12px 0}.action{margin:8px 0}.action.done{border-color:#75dda750;background:#75dda709}.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.68rem}.field{width:100%;border:1px solid var(--line);border-radius:10px;padding:9px;background:#09070f;color:#fff;font:inherit;margin-top:7px}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:520px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
let plan=null;const API='/command-center/api/my-plan',$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff90a455':''}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}function render(){const c=plan.completion||{};$('objective').textContent=plan.monthly_objective;$('metrics').innerHTML=`<div class="metric"><span class="muted">LIVE days / month</span><b>${plan.live_days_target}</b></div><div class="metric"><span class="muted">LIVE hours / month</span><b>${plan.live_hours_target}</b></div><div class="metric"><span class="muted">Videos / week</span><b>${plan.videos_per_week}</b></div><div class="metric"><span class="muted">Path complete</span><b>${c.percent||0}%</b></div>`;$('guidance').innerHTML=(plan.aura_guidance||[]).map(x=>`<li>${esc(x)}</li>`).join('');$('actions').innerHTML=(plan.actions||[]).map(a=>`<div class="action ${a.status==='done'?'done':''}"><div class="row"><div><span class="pill">Day ${a.day_number} · ${esc(a.category)}</span><h3>${esc(a.title)}</h3><p class="muted">${esc(a.detail)}</p>${a.evidence_note?`<p><b>Evidence:</b> ${esc(a.evidence_note)}</p>`:''}</div><div>${a.status==='done'?`<button class="btn" onclick="reopen('${esc(a.id)}')">Reopen</button>`:`<button class="btn" onclick="complete('${esc(a.id)}',${a.evidence_required})">Complete</button>`}</div></div></div>`).join('')}async function load(){try{plan=(await req(API)).plan;render()}catch(e){note(e.message,true)}}async function complete(id,required){let evidence='';if(required){evidence=prompt('Add a short evidence/check-in note for this action:')||'';if(!evidence.trim())return}try{plan=(await req(`${API}/actions/${encodeURIComponent(id)}/complete`,{method:'POST',body:JSON.stringify({evidence_note:evidence})})).plan;render();note('Action completed.')}catch(e){note(e.message,true)}}async function reopen(id){try{plan=(await req(`${API}/actions/${encodeURIComponent(id)}/reopen`,{method:'POST'})).plan;render();note('Action reopened.')}catch(e){note(e.message,true)}}load();
"""


@router.get("/command-center/my-plan", response_class=HTMLResponse, include_in_schema=False)
def my_plan_page(request: Request):
    try:
        _require_creator(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/signin?next=/command-center/my-plan", status_code=303)
        raise
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP My Plan</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Creator OS</div><h1>My Plan & <span style='color:var(--gold)'>Activation Pathway</span></h1></div><div><a class='btn' href='/command-center/progress'>Creator Progress</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><p class='muted'>A focused seven-day execution pathway backed by your selected niche, uploaded performance evidence and Aura guidance. Your ESP role remains separate from your Free/Basic/Pro creative subscription.</p><div id='notice' class='notice'></div><section class='card'><div class='eyebrow'>Monthly objective</div><h2 id='objective'>Loading…</h2><div id='metrics' class='grid'></div></section><section class='card'><div class='eyebrow'>Aura next actions</div><ul id='guidance'></ul></section><section><div class='eyebrow'>Seven-day pathway</div><div id='actions'></div></section><footer class='muted' style='margin-top:30px'>{escape(ENDORSEMENT)}</footer></main><script>{SCRIPT}</script></body></html>"""
    return HTMLResponse(html)


__all__ = ["router", "CreatorPlanStore", "plans"]
