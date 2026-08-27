from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_agent_health import creator_health
from .esp_agent_roster import _require_agent, rosters
from .esp_support_center import support

router = APIRouter(tags=["ESP Agent Support Escalation"])

CaseStatus = Literal["open", "triage", "in_progress", "waiting_member", "resolved", "closed"]
EscalationTarget = Literal["owner", "technical", "compliance", "safety", "shop", "social"]


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or (membership.get("roles") or "").lower() == "owner"


def _target_response(severity: str) -> str:
    # Internal prioritisation target only; never presented as an external-platform guarantee.
    hours = {"urgent": 4, "high": 24, "normal": 72, "low": 120}.get(severity, 72)
    return (_now_dt() + timedelta(hours=hours)).isoformat()


class ClaimCaseRequest(BaseModel):
    agent_user_id: str | None = Field(default=None, max_length=128)


class AgentCaseStatusRequest(BaseModel):
    status: CaseStatus
    resolution: str = Field(default="", max_length=5000)


class InternalNoteRequest(BaseModel):
    note: str = Field(min_length=2, max_length=5000)


class EscalateCaseRequest(BaseModel):
    target: EscalationTarget
    reason: str = Field(min_length=3, max_length=3000)


class SupportEscalationStore:
    """Agent/Owner workflow overlay for the existing private ESP support case store.

    Creator-facing support evidence remains in ``esp_support_center``. This layer adds
    assignment-aware triage, internal notes and specialist escalation without turning a
    support case into an automated violation, penalty, role change or platform action.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(support.db_path)
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
                CREATE TABLE IF NOT EXISTS esp_support_case_workflow (
                    case_id TEXT PRIMARY KEY,
                    lead_agent_user_id TEXT,
                    escalation_target TEXT,
                    escalation_reason TEXT NOT NULL DEFAULT '',
                    target_response_at TEXT,
                    claimed_at TEXT,
                    escalated_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(lead_agent_user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_support_workflow_lead
                    ON esp_support_case_workflow(lead_agent_user_id,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_support_workflow_escalation
                    ON esp_support_case_workflow(escalation_target,escalated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_support_internal_notes (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_internal_notes_case
                    ON esp_support_internal_notes(case_id,created_at);
                """
            )

    @staticmethod
    def _activity(
        con: sqlite3.Connection,
        case_id: str,
        actor: str,
        action: str,
        metadata_json: str = "{}",
    ) -> None:
        con.execute(
            """INSERT INTO esp_support_activity(id,case_id,actor,action,metadata_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (uuid4().hex, case_id, actor[:120], action[:120], metadata_json, _now()),
        )

    def _case(self, con: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM esp_support_cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return row

    def _active_assignment(self, agent_user_id: str, creator_user_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                """SELECT 1 FROM esp_agent_creator_assignments
                   WHERE agent_user_id=? AND creator_user_id=? AND status='active'""",
                (agent_user_id, creator_user_id),
            ).fetchone()
        return row is not None

    def _active_agent(self, agent_user_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT status,roles FROM esp_memberships WHERE user_id=?",
                (agent_user_id,),
            ).fetchone()
        if row is None or row["status"] not in {"active", "owner"}:
            return False
        return row["status"] == "owner" or (row["roles"] or "").lower() in {"agent", "both"}

    def _authorize(self, actor_user_id: str, creator_user_id: str, *, owner: bool) -> None:
        if owner:
            return
        if not self._active_assignment(actor_user_id, creator_user_id):
            raise PermissionError("Support case belongs to a creator who is not actively assigned to this agent")

    def _workflow(self, con: sqlite3.Connection, case_id: str) -> dict:
        row = con.execute("SELECT * FROM esp_support_case_workflow WHERE case_id=?", (case_id,)).fetchone()
        return dict(row) if row else {
            "case_id": case_id,
            "lead_agent_user_id": None,
            "escalation_target": None,
            "escalation_reason": "",
            "target_response_at": None,
            "claimed_at": None,
            "escalated_at": None,
            "updated_at": None,
        }

    def _internal_notes(self, con: sqlite3.Connection, case_id: str) -> list[dict]:
        rows = con.execute(
            "SELECT id,case_id,actor_user_id,note,created_at FROM esp_support_internal_notes WHERE case_id=? ORDER BY created_at",
            (case_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _health(self, actor_user_id: str, creator_user_id: str, lead_agent_user_id: str | None, *, owner: bool) -> dict | None:
        agent_id = lead_agent_user_id if owner else actor_user_id
        if not agent_id:
            return None
        try:
            row = next((item for item in rosters.roster(agent_id) if item.get("creator_user_id") == creator_user_id), None)
        except Exception:
            row = None
        return creator_health(row) if row else None

    def _project(self, case_id: str, actor_user_id: str, *, owner: bool) -> dict:
        with self._connect() as con:
            row = self._case(con, case_id)
            self._authorize(actor_user_id, row["user_id"], owner=owner)
            workflow = self._workflow(con, case_id)
            notes = self._internal_notes(con, case_id)
        case = support.get(case_id, owner=True)
        case["workflow"] = workflow
        case["internal_notes"] = notes
        case["health"] = self._health(
            actor_user_id,
            case["user_id"],
            workflow.get("lead_agent_user_id"),
            owner=owner,
        )
        case["internal_notes_visible_to_creator"] = False
        return case

    def list_for_actor(self, actor_user_id: str, *, owner: bool) -> list[dict]:
        with self._connect() as con:
            if owner:
                rows = con.execute(
                    """SELECT id FROM esp_support_cases
                       ORDER BY CASE severity WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                                CASE status WHEN 'open' THEN 0 WHEN 'triage' THEN 1 WHEN 'in_progress' THEN 2 WHEN 'waiting_member' THEN 3 ELSE 4 END,
                                updated_at DESC"""
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT DISTINCT c.id FROM esp_support_cases c
                       JOIN esp_agent_creator_assignments a ON a.creator_user_id=c.user_id
                       WHERE a.agent_user_id=? AND a.status='active'
                       ORDER BY CASE c.severity WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                                c.updated_at DESC""",
                    (actor_user_id,),
                ).fetchall()
        return [self._project(row["id"], actor_user_id, owner=owner) for row in rows]

    def claim(
        self,
        case_id: str,
        actor_user_id: str,
        *,
        owner: bool,
        requested_agent_user_id: str | None = None,
    ) -> dict:
        with self._connect() as con:
            case = self._case(con, case_id)
        self._authorize(actor_user_id, case["user_id"], owner=owner)
        target = (requested_agent_user_id or actor_user_id).strip()
        if not owner and target != actor_user_id:
            raise PermissionError("An Agent can claim a case only for themselves")
        if not self._active_agent(target):
            raise ValueError("Lead user does not have active ESP Agent access")
        if not self._active_assignment(target, case["user_id"]):
            raise ValueError("Lead Agent must have an active assignment to this creator")
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_support_case_workflow
                   (case_id,lead_agent_user_id,escalation_target,escalation_reason,target_response_at,claimed_at,escalated_at,updated_at)
                   VALUES (?, ?, NULL, '', ?, ?, NULL, ?)
                   ON CONFLICT(case_id) DO UPDATE SET
                     lead_agent_user_id=excluded.lead_agent_user_id,
                     claimed_at=excluded.claimed_at,
                     target_response_at=COALESCE(esp_support_case_workflow.target_response_at,excluded.target_response_at),
                     updated_at=excluded.updated_at""",
                (case_id, target, _target_response(case["severity"]), now, now),
            )
            self._activity(con, case_id, actor_user_id, "support_case_claimed", '{"lead_agent_user_id":"' + target.replace('"', '') + '"}')
        return self._project(case_id, actor_user_id, owner=owner)

    def set_status(
        self,
        case_id: str,
        actor_user_id: str,
        *,
        owner: bool,
        status: str,
        resolution: str = "",
    ) -> dict:
        if status not in {"open", "triage", "in_progress", "waiting_member", "resolved", "closed"}:
            raise ValueError("Unsupported support case status")
        if status == "closed" and not owner:
            raise PermissionError("Only ESP ownership can close a support case")
        resolution = (resolution or "").strip()[:5000]
        if status in {"resolved", "closed"} and len(resolution) < 3:
            raise ValueError("A human-written resolution is required before resolving or closing a case")
        with self._connect() as con:
            case = self._case(con, case_id)
        self._authorize(actor_user_id, case["user_id"], owner=owner)
        resolved_at = _now() if status in {"resolved", "closed"} else None
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_support_cases SET status=?,resolution=?,resolved_at=?,updated_at=? WHERE id=?""",
                (status, resolution, resolved_at, now, case_id),
            )
            self._activity(con, case_id, actor_user_id, "support_case_status_updated", '{"status":"' + status + '"}')
        return self._project(case_id, actor_user_id, owner=owner)

    def add_internal_note(self, case_id: str, actor_user_id: str, *, owner: bool, note: str) -> dict:
        note = (note or "").strip()[:5000]
        if len(note) < 2:
            raise ValueError("Internal note is required")
        with self._connect() as con:
            case = self._case(con, case_id)
        self._authorize(actor_user_id, case["user_id"], owner=owner)
        note_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO esp_support_internal_notes(id,case_id,actor_user_id,note,created_at) VALUES (?,?,?,?,?)",
                (note_id, case_id, actor_user_id, note, _now()),
            )
            con.execute("UPDATE esp_support_cases SET updated_at=? WHERE id=?", (_now(), case_id))
            self._activity(con, case_id, actor_user_id, "support_internal_note_added", '{"note_id":"' + note_id + '"}')
        return self._project(case_id, actor_user_id, owner=owner)

    def escalate(
        self,
        case_id: str,
        actor_user_id: str,
        *,
        owner: bool,
        target: str,
        reason: str,
    ) -> dict:
        if target not in {"owner", "technical", "compliance", "safety", "shop", "social"}:
            raise ValueError("Unsupported escalation target")
        reason = (reason or "").strip()[:3000]
        if len(reason) < 3:
            raise ValueError("Escalation reason is required")
        with self._connect() as con:
            case = self._case(con, case_id)
        self._authorize(actor_user_id, case["user_id"], owner=owner)
        now = _now()
        lead_agent = None if owner else actor_user_id
        with self._connect() as con:
            current = self._workflow(con, case_id)
            if current.get("lead_agent_user_id"):
                lead_agent = current["lead_agent_user_id"]
            con.execute(
                """INSERT INTO esp_support_case_workflow
                   (case_id,lead_agent_user_id,escalation_target,escalation_reason,target_response_at,claimed_at,escalated_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(case_id) DO UPDATE SET
                     lead_agent_user_id=COALESCE(esp_support_case_workflow.lead_agent_user_id,excluded.lead_agent_user_id),
                     escalation_target=excluded.escalation_target,
                     escalation_reason=excluded.escalation_reason,
                     target_response_at=excluded.target_response_at,
                     escalated_at=excluded.escalated_at,
                     updated_at=excluded.updated_at""",
                (
                    case_id, lead_agent, target, reason, _target_response(case["severity"]),
                    now if lead_agent else None, now, now,
                ),
            )
            if case["status"] == "open":
                con.execute("UPDATE esp_support_cases SET status='triage',updated_at=? WHERE id=?", (now, case_id))
            self._activity(con, case_id, actor_user_id, "support_case_escalated", '{"target":"' + target + '"}')
        return self._project(case_id, actor_user_id, owner=owner)


escalations = SupportEscalationStore()


def _actor(request: Request):
    member, membership = _require_agent(request)
    return member.user_id, _owner(membership)


@router.get("/command-center/api/agent/support/cases")
def agent_support_cases(request: Request):
    actor, owner = _actor(request)
    rows = escalations.list_for_actor(actor, owner=owner)
    return {
        "cases": rows,
        "owner": owner,
        "assignment_boundary": "active_esp_agent_creator_assignments_only",
        "internal_notes_creator_visible": False,
        "automatic_penalties": False,
        "external_platform_action": False,
    }


@router.post("/command-center/api/agent/support/cases/{case_id}/claim")
def claim_support_case(case_id: str, body: ClaimCaseRequest, request: Request):
    actor, owner = _actor(request)
    try:
        return {"case": escalations.claim(case_id, actor, owner=owner, requested_agent_user_id=body.agent_user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/agent/support/cases/{case_id}/status")
def update_support_case(case_id: str, body: AgentCaseStatusRequest, request: Request):
    actor, owner = _actor(request)
    try:
        return {"case": escalations.set_status(case_id, actor, owner=owner, status=body.status, resolution=body.resolution)}
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/agent/support/cases/{case_id}/internal-notes")
def add_support_internal_note(case_id: str, body: InternalNoteRequest, request: Request):
    actor, owner = _actor(request)
    try:
        return {"case": escalations.add_internal_note(case_id, actor, owner=owner, note=body.note)}
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/agent/support/cases/{case_id}/escalate")
def escalate_support_case(case_id: str, body: EscalateCaseRequest, request: Request):
    actor, owner = _actor(request)
    try:
        return {"case": escalations.escalate(case_id, actor, owner=owner, target=body.target, reason=body.reason)}
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


CSS = """
:root{--line:#ffffff1d;--muted:#c8bfd4;--gold:#efc86f;--violet:#9f70ff;--bad:#ff90a4;--good:#78dfa7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#43195b,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1240px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;line-height:.95;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d;border:0}.card{border:1px solid var(--line);border-radius:15px;background:#14101ceb;padding:14px;margin:9px 0}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.72rem}.notes{border-left:3px solid var(--violet);padding-left:10px;margin-top:8px}@media(max-width:700px){h1{font-size:2.5rem}}
"""

SCRIPT = r"""
const API='/command-center/api/agent/support',q=s=>document.querySelector(s);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function req(u,o={}){const r=await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Request failed');return d}function health(c){return c.health?` · health ${esc(c.health.state)}`:''}function cards(rows){return (rows||[]).map(c=>`<article class="card"><div class="row"><div><span class="pill">${esc(c.category)} · ${esc(c.severity)} · ${esc(c.status)}</span><h2>${esc(c.subject)}</h2><p class="muted">Creator ${esc(c.user_id)}${health(c)}</p></div><div><button class="btn" onclick="claim('${esc(c.id)}')">Claim</button> <button class="btn" onclick="note('${esc(c.id)}')">Internal note</button> <button class="btn primary" onclick="escalateCase('${esc(c.id)}')">Escalate</button> <button class="btn" onclick="statusCase('${esc(c.id)}','${esc(c.status)}')">Status</button></div></div><p class="muted">${esc(c.description)}</p>${c.workflow?.escalation_target?`<p><b>Escalated:</b> ${esc(c.workflow.escalation_target)} · target response ${esc((c.workflow.target_response_at||'').slice(0,16))}</p>`:''}${(c.internal_notes||[]).length?`<div class="notes"><b>Internal notes</b>${c.internal_notes.map(n=>`<p>${esc(n.note)} <span class="muted">${esc((n.created_at||'').slice(0,16))}</span></p>`).join('')}</div>`:''}</article>`).join('')||'<div class="card muted">No assigned Creator support cases.</div>'}async function load(){try{const d=await req(API+'/cases');q('#queue').innerHTML=cards(d.cases)}catch(e){q('#queue').textContent=e.message}}async function claim(id){try{await req(`${API}/cases/${id}/claim`,{method:'POST',body:JSON.stringify({agent_user_id:null})});load()}catch(e){alert(e.message)}}async function note(id){const v=prompt('Private Agent/Owner note — creators cannot see this:')||'';if(!v.trim())return;try{await req(`${API}/cases/${id}/internal-notes`,{method:'POST',body:JSON.stringify({note:v})});load()}catch(e){alert(e.message)}}async function escalateCase(id){const target=prompt('Escalate to: owner, technical, compliance, safety, shop or social','technical')||'';if(!target)return;const reason=prompt('Escalation reason:')||'';if(!reason.trim())return;try{await req(`${API}/cases/${id}/escalate`,{method:'POST',body:JSON.stringify({target,reason})});load()}catch(e){alert(e.message)}}async function statusCase(id,current){const status=prompt('Status: open, triage, in_progress, waiting_member, resolved or closed',current)||'';if(!status)return;let resolution='';if(status==='resolved'||status==='closed')resolution=prompt('Human-written resolution:')||'';try{await req(`${API}/cases/${id}/status`,{method:'PATCH',body:JSON.stringify({status,resolution})});load()}catch(e){alert(e.message)}}load();
"""


@router.get("/command-center/agent/support", response_class=HTMLResponse, include_in_schema=False)
def agent_support_page(request: Request):
    _actor(request)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Support Escalation Desk</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent / Owner Operations</div><h1>Support & Escalation Desk</h1><p class='muted'>Private human-led triage for assigned ESP creators. Escalation never applies a penalty, changes an ESP role or claims TikTok/platform enforcement.</p></div><div><a class='btn' href='/command-center/agent/health'>Health Queue</a> <a class='btn' href='/command-center/agent/operations'>Creator Success</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><section id='queue'><div class='card muted'>Loading…</div></section></main><script>{SCRIPT}</script></body></html>"""
    )


__all__ = ["router", "SupportEscalationStore", "escalations"]
