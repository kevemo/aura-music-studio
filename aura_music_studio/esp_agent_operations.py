from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_agent_health import creator_health
from .esp_agent_roster import AgentRosterStore, _require_agent, rosters

router = APIRouter(tags=["ESP Agent Creator Success Operations"])
Pathway = Literal["activate", "build", "optimise", "reactivate", "technical_help"]
CheckinType = Literal["routine", "activation", "performance", "reactivation", "technical"]
PlanStatus = Literal["active", "completed", "cancelled"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckinRequest(BaseModel):
    creator_user_id: str = Field(min_length=1, max_length=128)
    checkin_type: CheckinType = "routine"
    summary: str = Field(min_length=1, max_length=3000)
    next_action: str = Field(default="", max_length=2000)
    due_at: str | None = Field(default=None, max_length=80)


class CompleteCheckinRequest(BaseModel):
    outcome: str = Field(default="", max_length=3000)


class SuccessPlanRequest(BaseModel):
    creator_user_id: str = Field(min_length=1, max_length=128)
    pathway: Pathway
    objective: str = Field(min_length=1, max_length=1200)
    target_at: str | None = Field(default=None, max_length=80)
    notes: str = Field(default="", max_length=3000)


class PlanStatusRequest(BaseModel):
    status: PlanStatus
    outcome: str = Field(default="", max_length=3000)


class AgentOperationsStore:
    """Assigned-creator success workflows with no automatic disciplinary actions."""

    def __init__(self, roster_store: AgentRosterStore | None = None):
        self.rosters = roster_store or rosters
        self.db_path = self.rosters.db_path
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
                CREATE TABLE IF NOT EXISTS esp_agent_checkins (
                    id TEXT PRIMARY KEY,
                    agent_user_id TEXT NOT NULL,
                    creator_user_id TEXT NOT NULL,
                    checkin_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    next_action TEXT NOT NULL DEFAULT '',
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    outcome TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_checkins_owner
                    ON esp_agent_checkins(agent_user_id,status,due_at,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_checkins_creator
                    ON esp_agent_checkins(creator_user_id,status,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_creator_success_plans (
                    id TEXT PRIMARY KEY,
                    agent_user_id TEXT NOT NULL,
                    creator_user_id TEXT NOT NULL,
                    pathway TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    target_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    outcome TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_success_plans_owner
                    ON esp_creator_success_plans(agent_user_id,status,target_at,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_success_plans_creator
                    ON esp_creator_success_plans(creator_user_id,status,created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_success_plan_one_active
                    ON esp_creator_success_plans(agent_user_id,creator_user_id)
                    WHERE status='active';
                """
            )

    def _require_assignment(self, agent_user_id: str, creator_user_id: str) -> None:
        if not self.rosters._active_assignment(agent_user_id, creator_user_id):
            raise PermissionError("Creator is not actively assigned to this agent")

    def add_checkin(
        self,
        agent_user_id: str,
        creator_user_id: str,
        *,
        checkin_type: str,
        summary: str,
        next_action: str = "",
        due_at: str | None = None,
    ) -> dict:
        self._require_assignment(agent_user_id, creator_user_id)
        if checkin_type not in {"routine", "activation", "performance", "reactivation", "technical"}:
            raise ValueError("Unsupported check-in type")
        clean_summary = " ".join((summary or "").split())[:3000]
        if not clean_summary:
            raise ValueError("Check-in summary is required")
        row = {
            "id": uuid4().hex,
            "agent_user_id": agent_user_id,
            "creator_user_id": creator_user_id,
            "checkin_type": checkin_type,
            "summary": clean_summary,
            "next_action": " ".join((next_action or "").split())[:2000],
            "due_at": (due_at or "").strip()[:80] or None,
            "status": "open",
            "outcome": "",
            "created_at": _now(),
            "completed_at": None,
        }
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_checkins
                   (id,agent_user_id,creator_user_id,checkin_type,summary,next_action,due_at,status,outcome,created_at,completed_at)
                   VALUES (:id,:agent_user_id,:creator_user_id,:checkin_type,:summary,:next_action,:due_at,:status,:outcome,:created_at,:completed_at)""",
                row,
            )
        return row

    def complete_checkin(self, agent_user_id: str, checkin_id: str, *, outcome: str = "") -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_agent_checkins WHERE id=? AND agent_user_id=?",
                (checkin_id, agent_user_id),
            ).fetchone()
            if row is None:
                raise KeyError(checkin_id)
            self._require_assignment(agent_user_id, row["creator_user_id"])
            completed_at = _now()
            con.execute(
                """UPDATE esp_agent_checkins SET status='completed',outcome=?,completed_at=?
                   WHERE id=? AND agent_user_id=?""",
                (" ".join((outcome or "").split())[:3000], completed_at, checkin_id, agent_user_id),
            )
            updated = con.execute("SELECT * FROM esp_agent_checkins WHERE id=?", (checkin_id,)).fetchone()
        return dict(updated) if updated else {}

    def start_plan(
        self,
        agent_user_id: str,
        creator_user_id: str,
        *,
        pathway: str,
        objective: str,
        target_at: str | None = None,
        notes: str = "",
    ) -> dict:
        self._require_assignment(agent_user_id, creator_user_id)
        if pathway not in {"activate", "build", "optimise", "reactivate", "technical_help"}:
            raise ValueError("Unsupported creator-success pathway")
        objective = " ".join((objective or "").split())[:1200]
        if not objective:
            raise ValueError("Success-plan objective is required")
        now = _now()
        row = {
            "id": uuid4().hex,
            "agent_user_id": agent_user_id,
            "creator_user_id": creator_user_id,
            "pathway": pathway,
            "objective": objective,
            "notes": " ".join((notes or "").split())[:3000],
            "target_at": (target_at or "").strip()[:80] or None,
            "status": "active",
            "outcome": "",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO esp_creator_success_plans
                       (id,agent_user_id,creator_user_id,pathway,objective,notes,target_at,status,outcome,created_at,updated_at,completed_at)
                       VALUES (:id,:agent_user_id,:creator_user_id,:pathway,:objective,:notes,:target_at,:status,:outcome,:created_at,:updated_at,:completed_at)""",
                    row,
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileExistsError("This assigned creator already has an active success pathway with this agent") from exc
            raise
        return row

    def set_plan_status(self, agent_user_id: str, plan_id: str, *, status: str, outcome: str = "") -> dict:
        if status not in {"active", "completed", "cancelled"}:
            raise ValueError("Unsupported success-plan status")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_success_plans WHERE id=? AND agent_user_id=?",
                (plan_id, agent_user_id),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            self._require_assignment(agent_user_id, row["creator_user_id"])
            completed_at = _now() if status in {"completed", "cancelled"} else None
            con.execute(
                """UPDATE esp_creator_success_plans SET status=?,outcome=?,updated_at=?,completed_at=?
                   WHERE id=? AND agent_user_id=?""",
                (status, " ".join((outcome or "").split())[:3000], _now(), completed_at, plan_id, agent_user_id),
            )
            updated = con.execute("SELECT * FROM esp_creator_success_plans WHERE id=?", (plan_id,)).fetchone()
        return dict(updated) if updated else {}

    def _creator_checkins(self, con: sqlite3.Connection, agent_user_id: str, creator_user_id: str) -> list[dict]:
        rows = con.execute(
            """SELECT * FROM esp_agent_checkins WHERE agent_user_id=? AND creator_user_id=?
               ORDER BY created_at DESC LIMIT 20""",
            (agent_user_id, creator_user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _creator_plans(self, con: sqlite3.Connection, agent_user_id: str, creator_user_id: str) -> list[dict]:
        rows = con.execute(
            """SELECT * FROM esp_creator_success_plans WHERE agent_user_id=? AND creator_user_id=?
               ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,created_at DESC LIMIT 20""",
            (agent_user_id, creator_user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def support_suggestion(creator: dict, health: dict) -> str:
        state = health.get("state")
        if state == "needs_action":
            if int(creator.get("progress_submissions") or 0) == 0:
                return "Schedule a human check-in and help the creator submit their first LIVE/video analysis."
            if float((creator.get("plan") or {}).get("completion_percent") or 0) < 30:
                return "Review the unfinished My Plan actions with the creator and agree one achievable next action."
            return "Review the explainable health signals with the creator and agree a support pathway together."
        if state == "watch":
            return "Use the next routine check-in to remove one blocker and keep the current plan moving."
        return "Creator is currently on track; reinforce what is working and set the next measurable growth objective."

    def dashboard(self, agent_user_id: str) -> list[dict]:
        creators = self.rosters.roster(agent_user_id)
        result: list[dict] = []
        with self._connect() as con:
            for creator in creators:
                item = dict(creator)
                health = creator_health(item)
                checkins = self._creator_checkins(con, agent_user_id, item["creator_user_id"])
                plans = self._creator_plans(con, agent_user_id, item["creator_user_id"])
                active_plan = next((row for row in plans if row["status"] == "active"), None)
                item.update({
                    "health": health,
                    "support_suggestion": self.support_suggestion(item, health),
                    "checkins": checkins,
                    "active_plan": active_plan,
                    "plan_history": plans,
                    "open_checkins": sum(1 for row in checkins if row["status"] == "open"),
                })
                result.append(item)
        order = {"needs_action": 0, "watch": 1, "on_track": 2}
        result.sort(key=lambda row: (order[row["health"]["state"]], str(row.get("display_name") or "").lower()))
        return result


operations = AgentOperationsStore()


@router.get("/command-center/api/agent/operations")
def operations_api(request: Request):
    member, _membership = _require_agent(request)
    creators = operations.dashboard(member.user_id)
    return {
        "creators": creators,
        "assignment_boundary": "explicit_esp_assignments_only",
        "automatic_interventions": False,
        "automatic_penalties": False,
        "pathways": ["activate", "build", "optimise", "reactivate", "technical_help"],
    }


@router.post("/command-center/api/agent/operations/checkins")
def add_checkin_api(body: CheckinRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        row = operations.add_checkin(
            member.user_id,
            body.creator_user_id,
            checkin_type=body.checkin_type,
            summary=body.summary,
            next_action=body.next_action,
            due_at=body.due_at,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"checkin": row}


@router.post("/command-center/api/agent/operations/checkins/{checkin_id}/complete")
def complete_checkin_api(checkin_id: str, body: CompleteCheckinRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        return {"checkin": operations.complete_checkin(member.user_id, checkin_id, outcome=body.outcome)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Check-in not found") from exc


@router.post("/command-center/api/agent/operations/plans")
def start_plan_api(body: SuccessPlanRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        row = operations.start_plan(
            member.user_id,
            body.creator_user_id,
            pathway=body.pathway,
            objective=body.objective,
            target_at=body.target_at,
            notes=body.notes,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": row}


@router.patch("/command-center/api/agent/operations/plans/{plan_id}")
def update_plan_api(plan_id: str, body: PlanStatusRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        row = operations.set_plan_status(member.user_id, plan_id, status=body.status, outcome=body.outcome)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Success pathway not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": row}


CSS = r"""
:root{--line:#ffffff1d;--muted:#c2bfd1;--gold:#efc86f;--violet:#9f70ff;--good:#78dfa7;--warn:#ffd17b;--bad:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#43195b,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1240px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.2rem);letter-spacing:-.055em;line-height:.94;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.card{border:1px solid var(--line);border-radius:15px;background:#14101ceb;padding:14px;margin:9px 0}.state,.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem}.needs_action{color:var(--bad)}.watch{color:var(--warn)}.on_track{color:var(--good)}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.metric{border:1px solid var(--line);border-radius:11px;padding:8px}.metric b{display:block;font-size:1.2rem}@media(max-width:780px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/agent/operations',$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function pretty(v){return String(v||'').replace(/_/g,' ')}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}function render(d){$('ops').innerHTML=(d.creators||[]).map(r=>`<article class="card"><div class="row"><div><span class="state ${esc(r.health.state)}">${esc(pretty(r.health.state))}</span><h2>${esc(r.display_name||r.tiktok_handle||'Assigned creator')}</h2><p class="muted">@${esc(r.tiktok_handle||'')} · ${esc(r.niche||'Niche not set')}</p></div><div><button class="btn" onclick="checkin('${esc(r.creator_user_id)}')">New check-in</button> <button class="btn primary" onclick="pathway('${esc(r.creator_user_id)}')">Start pathway</button></div></div><p>${esc(r.support_suggestion)}</p><div class="grid"><div class="metric"><span class="muted">Open check-ins</span><b>${r.open_checkins||0}</b></div><div class="metric"><span class="muted">Open follow-ups</span><b>${r.open_followups||0}</b></div><div class="metric"><span class="muted">My Plan</span><b>${r.plan?.completion_percent||0}%</b></div><div class="metric"><span class="muted">Pathway</span><b style="font-size:.9rem">${esc(pretty(r.active_plan?.pathway||'none'))}</b></div></div>${r.active_plan?`<p class="muted"><b>Objective:</b> ${esc(r.active_plan.objective)} <button class="btn" onclick="finishPlan('${esc(r.active_plan.id)}')">Complete pathway</button></p>`:''}${(r.checkins||[]).slice(0,3).map(c=>`<p class="muted">${esc(c.checkin_type)} · ${esc(c.summary)} · ${esc(c.status)}</p>`).join('')}</article>`).join('')||'<div class="card muted">No assigned creators.</div>'}
async function load(){try{render(await req(API))}catch(e){$('ops').textContent=e.message}}async function checkin(id){const summary=prompt('Check-in summary:')||'';if(!summary.trim())return;const next=prompt('Agreed next action (optional):')||'';await req(API+'/checkins',{method:'POST',body:JSON.stringify({creator_user_id:id,checkin_type:'routine',summary,next_action:next,due_at:null})});load()}async function pathway(id){const p=prompt('Pathway: activate, build, optimise, reactivate or technical_help','build')||'';if(!p.trim())return;const objective=prompt('Agreed objective:')||'';if(!objective.trim())return;try{await req(API+'/plans',{method:'POST',body:JSON.stringify({creator_user_id:id,pathway:p.trim(),objective,target_at:null,notes:''})});load()}catch(e){alert(e.message)}}async function finishPlan(id){const outcome=prompt('Outcome / what changed:')||'';await req(API+'/plans/'+encodeURIComponent(id),{method:'PATCH',body:JSON.stringify({status:'completed',outcome})});load()}load();
"""


@router.get("/command-center/agent/operations", response_class=HTMLResponse, include_in_schema=False)
def operations_page(request: Request):
    _require_agent(request)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Creator Success Operations</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div><h1>Creator Success Operations</h1><p class='muted'>Human-led check-ins and support pathways for creators explicitly assigned to this Agent account. Health signals can guide attention, but they never start an intervention or penalty automatically.</p></div><div><a class='btn' href='/command-center/agent/health'>Health Queue</a> <a class='btn' href='/command-center/agent/roster'>Roster</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><section id='ops'><div class='card muted'>Loading…</div></section></main><script>{SCRIPT}</script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "AgentOperationsStore", "operations"]
