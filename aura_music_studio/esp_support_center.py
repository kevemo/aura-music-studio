from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Support Safety and Evidence"])

CaseCategory = Literal[
    "technical", "policy", "violation", "harassment", "impersonation", "ip",
    "traffic_health", "commerce", "other",
]
CaseSeverity = Literal["low", "normal", "high", "urgent"]
CaseStatus = Literal["open", "triage", "in_progress", "waiting_member", "resolved", "closed"]
EvidenceKind = Literal["note", "url", "artifact_ref"]

CATEGORIES = {
    "technical": "Technical / platform issue",
    "policy": "Policy or rules guidance",
    "violation": "Violation / appeal support",
    "harassment": "Harassment / bullying evidence",
    "impersonation": "Impersonation / scam",
    "ip": "Copyright / IP / stolen content",
    "traffic_health": "Traffic / account health",
    "commerce": "Commerce / brand / Shop",
    "other": "Other private support",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or (membership.get("roles") or "").lower() == "owner"


def _require_support(request: Request):
    return require_esp_hub_member(request)


def _clean_line(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _validate_evidence(kind: str, value: str) -> str:
    clean = (value or "").strip()
    if kind == "note":
        clean = clean[:5000]
        if not clean:
            raise ValueError("Evidence note cannot be empty")
        return clean
    if kind == "url":
        if len(clean) > 2000:
            raise ValueError("Evidence URL is too long")
        parsed = urlparse(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Evidence URL must use http or https")
        return clean
    if kind == "artifact_ref":
        clean = clean[:240]
        if not clean:
            raise ValueError("Artifact reference cannot be empty")
        if ".." in clean or "/" in clean or "\\" in clean:
            raise ValueError("Artifact reference must be an application reference, not a file path")
        return clean
    raise ValueError("Unsupported evidence type")


class CreateCaseRequest(BaseModel):
    category: CaseCategory
    severity: CaseSeverity = "normal"
    subject: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3, max_length=6000)


class AddEvidenceRequest(BaseModel):
    kind: EvidenceKind
    value: str = Field(min_length=1, max_length=6000)
    label: str = Field(default="", max_length=240)


class OwnerCaseUpdate(BaseModel):
    status: CaseStatus
    assigned_owner: str = Field(default="", max_length=120)
    resolution: str = Field(default="", max_length=5000)


class SupportCaseStore:
    """Private ESP support cases with structured, auditable evidence.

    This store organises member-supplied evidence. It never determines guilt, liability,
    platform violations or appeal outcomes. Those remain human/platform decisions.
    """

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
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
                CREATE TABLE IF NOT EXISTS esp_support_cases (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'open',
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    assigned_owner TEXT NOT NULL DEFAULT '',
                    resolution TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_cases_member
                    ON esp_support_cases(user_id,status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_support_cases_queue
                    ON esp_support_cases(status,severity,updated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_support_evidence (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    value TEXT NOT NULL,
                    added_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_evidence_case
                    ON esp_support_evidence(case_id,created_at);

                CREATE TABLE IF NOT EXISTS esp_support_activity (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_activity_case
                    ON esp_support_activity(case_id,created_at);
                """
            )

    def _activity(self, con: sqlite3.Connection, case_id: str, actor: str, action: str, metadata: dict | None = None) -> None:
        con.execute(
            """INSERT INTO esp_support_activity(id,case_id,actor,action,metadata_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (uuid4().hex, case_id, actor[:120], action[:120], json.dumps(metadata or {}, sort_keys=True), _now()),
        )

    def create_case(self, user_id: str, *, category: str, severity: str, subject: str, description: str) -> dict:
        if category not in CATEGORIES:
            raise ValueError("Unsupported support category")
        if severity not in {"low", "normal", "high", "urgent"}:
            raise ValueError("Unsupported support severity")
        subject = _clean_line(subject, 240)
        description = (description or "").strip()[:6000]
        if len(subject) < 3 or len(description) < 3:
            raise ValueError("Support case needs a subject and description")
        now = _now()
        case_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_support_cases
                   (id,user_id,category,severity,status,subject,description,assigned_owner,resolution,created_at,updated_at,resolved_at)
                   VALUES (?,?,?,?,'open',?,?,'','',?,?,NULL)""",
                (case_id, user_id, category, severity, subject, description, now, now),
            )
            self._activity(con, case_id, user_id, "case_created", {"category": category, "severity": severity})
        return self.get(case_id, user_id=user_id)

    def _case_row(self, con: sqlite3.Connection, case_id: str):
        return con.execute("SELECT * FROM esp_support_cases WHERE id=?", (case_id,)).fetchone()

    def _authorize(self, row: sqlite3.Row | None, *, user_id: str | None, owner: bool) -> None:
        if row is None:
            raise KeyError("Support case not found")
        if not owner and (not user_id or row["user_id"] != user_id):
            raise PermissionError("Support case is private to its member and ESP ownership")

    def get(self, case_id: str, *, user_id: str | None = None, owner: bool = False) -> dict:
        with self._connect() as con:
            row = self._case_row(con, case_id)
            self._authorize(row, user_id=user_id, owner=owner)
            evidence = con.execute(
                "SELECT * FROM esp_support_evidence WHERE case_id=? ORDER BY created_at",
                (case_id,),
            ).fetchall()
            activity = con.execute(
                "SELECT * FROM esp_support_activity WHERE case_id=? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        item = dict(row)
        item["evidence"] = [dict(value) for value in evidence]
        history: list[dict] = []
        for value in activity:
            event = dict(value)
            try:
                event["metadata"] = json.loads(event.pop("metadata_json") or "{}")
            except Exception:
                event["metadata"] = {}
            history.append(event)
        item["activity"] = history
        return item

    def list_for_user(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM esp_support_cases WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [self.get(row["id"], user_id=user_id) for row in rows]

    def list_all(self, *, status: str | None = None) -> list[dict]:
        with self._connect() as con:
            if status:
                rows = con.execute(
                    "SELECT id FROM esp_support_cases WHERE status=? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = con.execute("SELECT id FROM esp_support_cases ORDER BY updated_at DESC").fetchall()
        return [self.get(row["id"], owner=True) for row in rows]

    def add_evidence(
        self,
        case_id: str,
        *,
        user_id: str | None,
        owner: bool,
        kind: str,
        value: str,
        label: str = "",
        actor: str,
    ) -> dict:
        clean = _validate_evidence(kind, value)
        with self._connect() as con:
            row = self._case_row(con, case_id)
            self._authorize(row, user_id=user_id, owner=owner)
            evidence_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_support_evidence(id,case_id,kind,label,value,added_by,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (evidence_id, case_id, kind, _clean_line(label, 240), clean, actor[:120], _now()),
            )
            con.execute("UPDATE esp_support_cases SET updated_at=? WHERE id=?", (_now(), case_id))
            self._activity(con, case_id, actor, "evidence_added", {"kind": kind, "evidence_id": evidence_id})
        return self.get(case_id, user_id=user_id, owner=owner)

    def owner_update(self, case_id: str, *, status: str, assigned_owner: str = "", resolution: str = "", actor: str) -> dict:
        if status not in {"open", "triage", "in_progress", "waiting_member", "resolved", "closed"}:
            raise ValueError("Unsupported support case status")
        with self._connect() as con:
            row = self._case_row(con, case_id)
            self._authorize(row, user_id=None, owner=True)
            before = {"status": row["status"], "assigned_owner": row["assigned_owner"], "resolution": row["resolution"]}
            resolved_at = _now() if status in {"resolved", "closed"} else None
            con.execute(
                """UPDATE esp_support_cases SET status=?,assigned_owner=?,resolution=?,updated_at=?,resolved_at=?
                   WHERE id=?""",
                (status, _clean_line(assigned_owner, 120), (resolution or "").strip()[:5000], _now(), resolved_at, case_id),
            )
            self._activity(
                con,
                case_id,
                actor,
                "owner_case_updated",
                {"before": before, "after": {"status": status, "assigned_owner": assigned_owner[:120]}},
            )
        return self.get(case_id, owner=True)

    def evidence_pack(self, case_id: str, *, user_id: str | None = None, owner: bool = False) -> dict:
        case = self.get(case_id, user_id=user_id, owner=owner)
        pack = {
            "schema": "esp-support-evidence-pack/v1",
            "generated_at": _now(),
            "case": {key: value for key, value in case.items() if key not in {"activity"}},
            "activity": case.get("activity") or [],
            "disclaimer": (
                "This pack organises supplied information. It does not determine fault, guilt, policy liability, "
                "legal liability or whether a platform appeal/report will succeed. Human/platform review is required."
            ),
            "automated_fault_decision": False,
            "human_review_required": True,
        }
        canonical = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {"pack": pack, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


support = SupportCaseStore()


@router.get("/command-center/api/support/cases")
def member_cases(request: Request):
    member, membership = _require_support(request)
    return {
        "cases": support.list_for_user(member.user_id),
        "owner": _is_owner(membership),
        "private": True,
        "automated_fault_decisions": False,
    }


@router.post("/command-center/api/support/cases")
def create_case(body: CreateCaseRequest, request: Request):
    member, _membership = _require_support(request)
    try:
        row = support.create_case(
            member.user_id,
            category=body.category,
            severity=body.severity,
            subject=body.subject,
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"case": row}


@router.get("/command-center/api/support/cases/{case_id}")
def case_detail(case_id: str, request: Request):
    member, membership = _require_support(request)
    try:
        row = support.get(case_id, user_id=member.user_id, owner=_is_owner(membership))
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"case": row}


@router.post("/command-center/api/support/cases/{case_id}/evidence")
def add_case_evidence(case_id: str, body: AddEvidenceRequest, request: Request):
    member, membership = _require_support(request)
    owner = _is_owner(membership)
    try:
        row = support.add_evidence(
            case_id,
            user_id=member.user_id,
            owner=owner,
            kind=body.kind,
            value=body.value,
            label=body.label,
            actor=member.user_id,
        )
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"case": row}


@router.get("/command-center/api/support/cases/{case_id}/evidence-pack")
def case_evidence_pack(case_id: str, request: Request):
    member, membership = _require_support(request)
    try:
        return support.evidence_pack(case_id, user_id=member.user_id, owner=_is_owner(membership))
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/command-center/api/support/owner/cases")
def owner_cases(request: Request, status: str | None = None):
    _member, membership = _require_support(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    return {"cases": support.list_all(status=status), "human_triage": True}


@router.patch("/command-center/api/support/owner/cases/{case_id}")
def owner_update_case(case_id: str, body: OwnerCaseUpdate, request: Request):
    member, membership = _require_support(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        row = support.owner_update(
            case_id,
            status=body.status,
            assigned_owner=body.assigned_owner,
            resolution=body.resolution,
            actor=member.user_id,
        )
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"case": row}


CSS = r"""
:root{--line:#ffffff1d;--muted:#c2bfd1;--gold:#efc86f;--violet:#9f70ff;--good:#78dfa7;--bad:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#46175d,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.2rem);letter-spacing:-.055em;line-height:.94;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn,.field{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#09070f;color:#fff;font:inherit}.btn{font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.card{border:1px solid var(--line);border-radius:15px;background:#14101ceb;padding:14px;margin:9px 0}.grid{display:grid;grid-template-columns:.8fr 1.2fr;gap:12px}.field{width:100%;margin:5px 0}textarea.field{min-height:110px}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:780px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/support',$=id=>document.getElementById(id);let owner=false;function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'var(--bad)':''}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
function cards(rows){return (rows||[]).map(c=>`<article class="card"><div class="row"><div><span class="pill">${esc(c.category)} · ${esc(c.severity)} · ${esc(c.status)}</span><h2>${esc(c.subject)}</h2><p class="muted">${esc(c.description)}</p></div><div><button class="btn" onclick="evidence('${esc(c.id)}')">Add evidence</button> <button class="btn" onclick="pack('${esc(c.id)}')">Evidence pack</button>${owner?` <button class="btn primary" onclick="triage('${esc(c.id)}','${esc(c.status)}')">Owner triage</button>`:''}</div></div><p class="muted">Evidence: ${(c.evidence||[]).length} · Updated ${esc((c.updated_at||'').slice(0,16))}${c.assigned_owner?` · Owner: ${esc(c.assigned_owner)}`:''}</p>${c.resolution?`<p><b>Resolution:</b> ${esc(c.resolution)}</p>`:''}</article>`).join('')||'<div class="card muted">No support cases yet.</div>'}
async function load(){try{const d=await req(API+'/cases');owner=!!d.owner;$('cases').innerHTML=cards(d.cases);if(owner){$('ownerTitle').style.display='block';const all=await req(API+'/owner/cases');$('ownerCases').innerHTML=cards(all.cases)}}catch(e){note(e.message,true)}}
$('create').onclick=async()=>{try{await req(API+'/cases',{method:'POST',body:JSON.stringify({category:$('category').value,severity:$('severity').value,subject:$('subject').value,description:$('description').value})});$('subject').value='';$('description').value='';note('Private support case created.');await load()}catch(e){note(e.message,true)}}
async function evidence(id){const kind=prompt('Evidence type: note, url or artifact_ref','note')||'';if(!kind)return;const value=prompt(kind==='url'?'Paste evidence URL:':kind==='artifact_ref'?'Enter application artifact reference:':'Evidence note:')||'';if(!value)return;try{await req(`${API}/cases/${encodeURIComponent(id)}/evidence`,{method:'POST',body:JSON.stringify({kind,value,label:''})});note('Evidence added privately.');await load()}catch(e){note(e.message,true)}}
async function pack(id){try{const d=await req(`${API}/cases/${encodeURIComponent(id)}/evidence-pack`);const text=JSON.stringify(d,null,2);await navigator.clipboard?.writeText(text);note(`Evidence pack generated. SHA-256 ${d.sha256}. JSON copied where clipboard permission is available.`)}catch(e){note(e.message,true)}}
async function triage(id,current){const status=prompt('Status: open, triage, in_progress, waiting_member, resolved or closed',current)||'';if(!status)return;const assigned=prompt('Assigned owner / lead (optional):')||'';const resolution=prompt('Resolution / owner note (optional):')||'';try{await req(`${API}/owner/cases/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({status,assigned_owner:assigned,resolution})});note('Owner triage updated.');await load()}catch(e){note(e.message,true)}}load();
"""


@router.get("/command-center/support", response_class=HTMLResponse, include_in_schema=False)
def support_page(request: Request):
    _require_support(request)
    options = "".join(f"<option value='{key}'>{label}</option>" for key, label in CATEGORIES.items())
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Support & Evidence Centre</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Private Support</div><h1>Support, Safety & Evidence Centre</h1><p class='muted'>Resolve issues privately, organise evidence and keep a clear audit trail. This system does not make automated guilt, violation, legal or appeal-outcome decisions.</p></div><div><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><div id='notice' class='notice'></div><section class='grid'><div class='card'><h2>Open private case</h2><select id='category' class='field'>{options}</select><select id='severity' class='field'><option value='normal'>Normal</option><option value='low'>Low</option><option value='high'>High</option><option value='urgent'>Urgent</option></select><input id='subject' class='field' placeholder='What do you need help with?'><textarea id='description' class='field' placeholder='Describe what happened, what you have already tried, and what outcome you need.'></textarea><button id='create' class='btn primary'>Create private case</button><p class='muted'>Urgent marks priority for ESP human triage; it does not guarantee external platform action or emergency response.</p></div><div><h2>Your cases</h2><div id='cases'><div class='card muted'>Loading…</div></div></div></section><h2 id='ownerTitle' style='display:none'>Owner triage queue</h2><section id='ownerCases'></section></main><script>{SCRIPT}</script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "SupportCaseStore", "support", "CATEGORIES"]
