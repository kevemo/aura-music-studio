from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_level_up import EspAgentAssignmentStore, assignments
from .esp_niche import require_esp_hub_member
from .esp_progress import EspProgressStore

router = APIRouter(tags=["ESP Agent Creator Roster"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_agent(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").strip().lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership


class FollowUpRequest(BaseModel):
    creator_user_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    note: str = Field(default="", max_length=2000)
    due_at: str | None = Field(default=None, max_length=80)


class AgentRosterStore:
    def __init__(self, esp_store: EspStore | None = None, assignment_store: EspAgentAssignmentStore | None = None):
        self.esp = esp_store or esp
        self.assignments = assignment_store or assignments
        self.db_path = self.esp.db_path
        self.progress = EspProgressStore(self.esp)
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
                CREATE TABLE IF NOT EXISTS esp_agent_followups (
                    id TEXT PRIMARY KEY,
                    agent_user_id TEXT NOT NULL,
                    creator_user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    due_at TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_followups_owner
                    ON esp_agent_followups(agent_user_id,status,due_at);
                CREATE INDEX IF NOT EXISTS idx_agent_followups_creator
                    ON esp_agent_followups(creator_user_id,status);

                -- Assignment rows are durable audit records, but their active state must never
                -- outlive the current server-authoritative ESP role/status on either side.
                -- These triggers make that invariant apply to every assignment consumer, not
                -- just this roster service.
                CREATE TRIGGER IF NOT EXISTS trg_esp_assignments_revoke_creator_ineligible
                AFTER UPDATE OF status, roles ON esp_memberships
                WHEN NEW.status != 'owner'
                 AND (
                    NEW.status != 'active'
                    OR lower(trim(COALESCE(NEW.roles,''))) NOT IN ('creator','both')
                 )
                BEGIN
                    UPDATE esp_agent_creator_assignments
                       SET status='revoked',
                           revoked_by='system:esp-role-boundary',
                           revoked_at=NEW.updated_at
                     WHERE creator_user_id=NEW.user_id
                       AND status='active';
                END;

                CREATE TRIGGER IF NOT EXISTS trg_esp_assignments_revoke_agent_ineligible
                AFTER UPDATE OF status, roles ON esp_memberships
                WHEN NEW.status != 'owner'
                 AND (
                    NEW.status != 'active'
                    OR lower(trim(COALESCE(NEW.roles,''))) NOT IN ('agent','both')
                 )
                BEGIN
                    UPDATE esp_agent_creator_assignments
                       SET status='revoked',
                           revoked_by='system:esp-role-boundary',
                           revoked_at=NEW.updated_at
                     WHERE agent_user_id=NEW.user_id
                       AND status='active';
                END;
                """
            )

    def _membership_allows(self, user_id: str, allowed_roles: set[str]) -> bool:
        membership = self.esp.membership(user_id)
        if not membership:
            return False
        status = str(membership.get("status") or "").strip().lower()
        if status == "owner":
            return True
        if status != "active":
            return False
        role = str(membership.get("roles") or "").strip().lower()
        return role in allowed_roles

    def _active_assignment(self, agent_user_id: str, creator_user_id: str) -> bool:
        # Assignment rows are durable audit records. They are not sufficient authorization on
        # their own: both parties must still hold the required current ESP roles.
        if not self._membership_allows(agent_user_id, {"agent", "both"}):
            return False
        if not self._membership_allows(creator_user_id, {"creator", "both"}):
            return False
        with self._connect() as con:
            row = con.execute(
                """SELECT 1 FROM esp_agent_creator_assignments
                   WHERE agent_user_id=? AND creator_user_id=? AND status='active'""",
                (agent_user_id, creator_user_id),
            ).fetchone()
        return row is not None

    def add_followup(self, agent_user_id: str, creator_user_id: str, *, title: str, note: str = "", due_at: str | None = None) -> dict:
        if not self._active_assignment(agent_user_id, creator_user_id):
            raise PermissionError("Creator is not actively assigned to this agent")
        row = {
            "id": uuid4().hex,
            "agent_user_id": agent_user_id,
            "creator_user_id": creator_user_id,
            "title": (title or "").strip()[:240],
            "note": (note or "").strip()[:2000],
            "status": "open",
            "due_at": (due_at or "").strip()[:80] or None,
            "created_at": _now(),
            "completed_at": None,
        }
        if not row["title"]:
            raise ValueError("Follow-up title is required")
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_followups
                   (id,agent_user_id,creator_user_id,title,note,status,due_at,created_at,completed_at)
                   VALUES (:id,:agent_user_id,:creator_user_id,:title,:note,:status,:due_at,:created_at,:completed_at)""",
                row,
            )
        return row

    def complete_followup(self, agent_user_id: str, followup_id: str) -> None:
        with self._connect() as con:
            row = con.execute(
                """SELECT id,creator_user_id FROM esp_agent_followups
                   WHERE id=? AND agent_user_id=? AND status='open'""",
                (followup_id, agent_user_id),
            ).fetchone()
            if not row:
                raise KeyError(followup_id)
            creator_user_id = row["creator_user_id"]
        if not self._active_assignment(agent_user_id, creator_user_id):
            raise PermissionError("Creator is not actively assigned to this agent")
        with self._connect() as con:
            con.execute(
                """UPDATE esp_agent_followups SET status='done',completed_at=?
                   WHERE id=? AND agent_user_id=? AND status='open'""",
                (_now(), followup_id, agent_user_id),
            )

    def followups(self, agent_user_id: str, creator_user_id: str | None = None) -> list[dict]:
        with self._connect() as con:
            if creator_user_id:
                rows = con.execute(
                    """SELECT * FROM esp_agent_followups WHERE agent_user_id=? AND creator_user_id=?
                       ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,due_at,created_at DESC""",
                    (agent_user_id, creator_user_id),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT * FROM esp_agent_followups WHERE agent_user_id=?
                       ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,due_at,created_at DESC""",
                    (agent_user_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def _plan_summary(self, con: sqlite3.Connection, creator_user_id: str) -> dict:
        try:
            plan = con.execute(
                "SELECT phase,monthly_objective FROM esp_creator_plans WHERE user_id=?",
                (creator_user_id,),
            ).fetchone()
            counts = con.execute(
                """SELECT COUNT(*) total,SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done
                   FROM esp_creator_plan_actions WHERE user_id=?""",
                (creator_user_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return {"phase": None, "objective": "", "completion_percent": 0.0}
        total = int(counts["total"] or 0) if counts else 0
        done = int(counts["done"] or 0) if counts else 0
        return {
            "phase": plan["phase"] if plan else None,
            "objective": plan["monthly_objective"] if plan else "",
            "completion_percent": round(done / total * 100, 1) if total else 0.0,
        }

    def roster(self, agent_user_id: str) -> list[dict]:
        if not self._membership_allows(agent_user_id, {"agent", "both"}):
            return []
        rows = self.assignments.for_agent(agent_user_id)
        result: list[dict] = []
        with self._connect() as con:
            for row in rows:
                creator_id = row["creator_user_id"]
                # The assignment table is retained for audit, but a current Creator/Both role
                # is required before any creator data is surfaced to the Agent workspace.
                if not self._membership_allows(creator_id, {"creator", "both"}):
                    continue
                training = self.esp.progress(creator_id)
                avg_training = round(sum(training.values()) / len(training), 1) if training else 0.0
                progress = self.progress.summary(creator_id)
                plan = self._plan_summary(con, creator_id)
                open_followups = con.execute(
                    """SELECT COUNT(*) n,MIN(due_at) next_due FROM esp_agent_followups
                       WHERE agent_user_id=? AND creator_user_id=? AND status='open'""",
                    (agent_user_id, creator_id),
                ).fetchone()
                item = dict(row)
                item.update({
                    "training_average": avg_training,
                    "progress_submissions": int(progress.get("total") or 0),
                    "plan": plan,
                    "open_followups": int(open_followups["n"] or 0) if open_followups else 0,
                    "next_followup_due": open_followups["next_due"] if open_followups else None,
                })
                result.append(item)
        return result


rosters = AgentRosterStore()


@router.get("/command-center/api/agent/roster")
def agent_roster_api(request: Request):
    member, _membership = _require_agent(request)
    return {"creators": rosters.roster(member.user_id), "assignment_boundary": "explicit_esp_assignments_only"}


@router.post("/command-center/api/agent/followups")
def add_agent_followup(body: FollowUpRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        row = rosters.add_followup(
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
    return {"followup": row}


@router.post("/command-center/api/agent/followups/{followup_id}/complete")
def complete_agent_followup(followup_id: str, request: Request):
    member, _membership = _require_agent(request)
    try:
        rosters.complete_followup(member.user_id, followup_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Follow-up not found") from exc
    return {"completed": True, "followup_id": followup_id}


CSS = r"""
:root{--line:#ffffff1d;--muted:#c1bfd0;--gold:#efc86f;--violet:#9f70ff;--good:#74dda5}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42175b,transparent 30%),#06050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1220px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.2rem);letter-spacing:-.055em;line-height:.94;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.card{border:1px solid var(--line);border-radius:17px;background:#14101ceb;padding:15px;margin:10px 0}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric{border:1px solid var(--line);border-radius:13px;padding:10px;background:#ffffff05}.metric b{display:block;font-size:1.3rem}.field{width:100%;border:1px solid var(--line);border-radius:10px;background:#09070f;color:#fff;padding:9px;margin-top:6px}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:750px){.metrics{grid-template-columns:1fr 1fr}}@media(max-width:480px){.metrics{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/agent', $=id=>document.getElementById(id);let rows=[];function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff90a455':''}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}function render(){ $('roster').innerHTML=rows.length?rows.map(r=>`<article class="card"><div class="row"><div><div class="eyebrow">${esc(r.niche||'Creator')} · ${esc(r.region||'')}</div><h2>${esc(r.display_name||r.tiktok_handle||'Assigned creator')}</h2><div class="muted">@${esc(r.tiktok_handle||'')} · ${esc(r.sub_niche||'')}</div></div><button class="btn" onclick="follow('${esc(r.creator_user_id)}','${esc(r.display_name||r.tiktok_handle||'creator')}')">Add follow-up</button></div><div class="metrics"><div class="metric"><span class="muted">Training</span><b>${r.training_average}%</b></div><div class="metric"><span class="muted">Progress uploads</span><b>${r.progress_submissions}</b></div><div class="metric"><span class="muted">My Plan</span><b>${r.plan?.completion_percent||0}%</b></div><div class="metric"><span class="muted">Open follow-ups</span><b>${r.open_followups}</b></div></div>${r.plan?.objective?`<p class="muted"><b>Objective:</b> ${esc(r.plan.objective)}</p>`:''}${r.next_followup_due?`<p class="muted">Next follow-up due: ${esc(r.next_followup_due)}</p>`:''}</article>`).join(''):'<div class="card muted">No creators are currently assigned to this Agent account. Assignments are owner-controlled.</div>'}async function load(){try{rows=(await req(API+'/roster')).creators||[];render()}catch(e){note(e.message,true)}}async function follow(creator,name){const title=prompt(`Follow-up for ${name}:`,'Creator check-in')||'';if(!title.trim())return;const noteText=prompt('Optional private follow-up note:')||'';try{await req(API+'/followups',{method:'POST',body:JSON.stringify({creator_user_id:creator,title,note:noteText,due_at:null})});note('Follow-up added.');await load()}catch(e){note(e.message,true)}}load();
"""


@router.get("/command-center/agent/roster", response_class=HTMLResponse, include_in_schema=False)
def agent_roster_page(request: Request):
    _require_agent(request)
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Assigned Creator Roster</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div><h1>Assigned Creator Roster</h1><p class='muted'>Only creators explicitly assigned by ESP ownership appear here. This is a creator-success workspace, not a creator directory or recruiting browser.</p></div><div><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><div id='notice' class='notice'></div><section id='roster'><div class='card muted'>Loading assigned creators…</div></section></main><script>{SCRIPT}</script></body></html>""")


__all__ = ["router", "AgentRosterStore", "rosters"]
