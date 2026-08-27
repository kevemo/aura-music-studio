from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .esp_support_center import SupportCaseStore, support

router = APIRouter(tags=["ESP Creator Safety and Appeals"])

AppealType = Literal[
    "policy_decision",
    "content_moderation",
    "account_restriction",
    "violation_notice",
    "harassment_report",
    "impersonation_report",
    "ip_dispute",
    "other",
]
AppealStatus = Literal[
    "draft",
    "owner_review",
    "changes_requested",
    "ready_for_external_submission",
    "submitted_external",
    "outcome_recorded",
    "withdrawn",
]
OwnerReviewDecision = Literal["ready", "changes_requested"]
OutcomeStatus = Literal["upheld", "partially_upheld", "denied", "withdrawn", "unknown"]

ELIGIBLE_CASE_CATEGORIES = {"policy", "violation", "harassment", "impersonation", "ip", "traffic_health", "other"}
EDITABLE_STATUSES = {"draft", "changes_requested"}
PRE_SUBMISSION_STATUSES = {"draft", "owner_review", "changes_requested", "ready_for_external_submission"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or (membership.get("roles") or "").lower() == "owner"


def _require_creator_or_owner(request: Request):
    member, membership = require_esp_hub_member(request)
    role = (membership.get("roles") or "").lower()
    if not _is_owner(membership) and role not in {"creator", "both"}:
        raise HTTPException(403, "ESP Creator or Owner access is required")
    return member, membership


def _require_owner(request: Request):
    member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    return member, membership


class CreateAppealRequest(BaseModel):
    case_id: str = Field(min_length=4, max_length=128)
    appeal_type: AppealType
    statement: str = Field(min_length=10, max_length=8000)
    requested_outcome: str = Field(min_length=3, max_length=2000)


class UpdateAppealDraftRequest(BaseModel):
    statement: str = Field(min_length=10, max_length=8000)
    requested_outcome: str = Field(min_length=3, max_length=2000)


class OwnerReviewRequest(BaseModel):
    decision: OwnerReviewDecision
    note: str = Field(min_length=3, max_length=4000)


class ExternalSubmissionRequest(BaseModel):
    destination: str = Field(min_length=2, max_length=240)
    reference: str = Field(default="", max_length=500)


class ExternalOutcomeRequest(BaseModel):
    outcome: OutcomeStatus
    note: str = Field(min_length=2, max_length=5000)


class SafetyAppealStore:
    """Evidence-bound appeal preparation layered over the existing ESP support cases.

    This store records preparation, human ESP review and member/owner-reported external
    submission/outcome state. It never transmits an appeal, impersonates a platform review,
    predicts success, overturns an external decision or changes an ESP role automatically.
    """

    def __init__(self, support_store: SupportCaseStore | None = None):
        self.support = support_store or support
        self.db_path = self.support.db_path
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
                CREATE TABLE IF NOT EXISTS esp_support_appeals (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    appeal_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    statement TEXT NOT NULL,
                    requested_outcome TEXT NOT NULL,
                    owner_review_note TEXT NOT NULL DEFAULT '',
                    owner_reviewed_by TEXT,
                    owner_reviewed_at TEXT,
                    evidence_pack_sha256 TEXT NOT NULL DEFAULT '',
                    evidence_bound_at TEXT,
                    external_destination TEXT NOT NULL DEFAULT '',
                    external_reference TEXT NOT NULL DEFAULT '',
                    external_submitted_at TEXT,
                    outcome_status TEXT NOT NULL DEFAULT '',
                    outcome_note TEXT NOT NULL DEFAULT '',
                    outcome_recorded_by TEXT,
                    outcome_recorded_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_appeals_member
                    ON esp_support_appeals(user_id,status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_support_appeals_case
                    ON esp_support_appeals(case_id,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_support_appeals_review
                    ON esp_support_appeals(status,updated_at DESC);
                """
            )

    @staticmethod
    def _activity(
        con: sqlite3.Connection,
        case_id: str,
        actor: str,
        action: str,
        metadata: dict | None = None,
    ) -> None:
        con.execute(
            """INSERT INTO esp_support_activity(id,case_id,actor,action,metadata_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                uuid4().hex,
                case_id,
                actor[:120],
                action[:120],
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )

    def _row(self, con: sqlite3.Connection, appeal_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM esp_support_appeals WHERE id=?", (appeal_id,)).fetchone()
        if row is None:
            raise KeyError(appeal_id)
        return row

    @staticmethod
    def _authorize(row: sqlite3.Row, *, user_id: str | None, owner: bool) -> None:
        if not owner and (not user_id or row["user_id"] != user_id):
            raise PermissionError("Appeal workspace is private to its creator and ESP ownership")

    def _case(self, case_id: str, *, user_id: str | None, owner: bool) -> dict:
        return self.support.get(case_id, user_id=user_id, owner=owner)

    def _project(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        try:
            case = self.support.get(row["case_id"], owner=True)
            item["case"] = {
                "id": case["id"],
                "category": case["category"],
                "severity": case["severity"],
                "status": case["status"],
                "subject": case["subject"],
                "evidence_count": len(case.get("evidence") or []),
            }
        except Exception:
            item["case"] = None
        item["command_center_transmitted_external_appeal"] = False
        item["command_center_decided_external_outcome"] = False
        item["external_outcome_verified_by_command_center"] = False
        item["human_review_required"] = True
        return item

    def get(self, appeal_id: str, *, user_id: str | None = None, owner: bool = False) -> dict:
        with self._connect() as con:
            row = self._row(con, appeal_id)
            self._authorize(row, user_id=user_id, owner=owner)
            return self._project(row)

    def list_for_user(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM esp_support_appeals WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [self._project(row) for row in rows]

    def list_for_owner(self, *, status: str | None = None) -> list[dict]:
        with self._connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM esp_support_appeals WHERE status=? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM esp_support_appeals ORDER BY updated_at DESC").fetchall()
        return [self._project(row) for row in rows]

    def create(
        self,
        user_id: str,
        *,
        case_id: str,
        appeal_type: str,
        statement: str,
        requested_outcome: str,
    ) -> dict:
        case = self._case(case_id, user_id=user_id, owner=False)
        if case["category"] not in ELIGIBLE_CASE_CATEGORIES:
            raise ValueError("This support case category is not an appeal/safety workflow")
        statement = (statement or "").strip()[:8000]
        requested_outcome = (requested_outcome or "").strip()[:2000]
        if len(statement) < 10 or len(requested_outcome) < 3:
            raise ValueError("Appeal draft needs a clear statement and requested outcome")
        with self._connect() as con:
            existing = con.execute(
                """SELECT id FROM esp_support_appeals
                   WHERE case_id=? AND status NOT IN ('withdrawn','outcome_recorded')
                   ORDER BY created_at DESC LIMIT 1""",
                (case_id,),
            ).fetchone()
            if existing:
                raise ValueError("This support case already has an active appeal workflow")
            now = _now()
            appeal_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_support_appeals
                   (id,case_id,user_id,appeal_type,status,statement,requested_outcome,created_at,updated_at)
                   VALUES (?,?,?,?, 'draft', ?,?,?,?)""",
                (appeal_id, case_id, user_id, appeal_type, statement, requested_outcome, now, now),
            )
            self._activity(
                con,
                case_id,
                user_id,
                "appeal_draft_created",
                {"appeal_id": appeal_id, "appeal_type": appeal_type},
            )
        return self.get(appeal_id, user_id=user_id)

    def update_draft(
        self,
        appeal_id: str,
        user_id: str,
        *,
        statement: str,
        requested_outcome: str,
    ) -> dict:
        statement = (statement or "").strip()[:8000]
        requested_outcome = (requested_outcome or "").strip()[:2000]
        if len(statement) < 10 or len(requested_outcome) < 3:
            raise ValueError("Appeal draft needs a clear statement and requested outcome")
        with self._connect() as con:
            row = self._row(con, appeal_id)
            self._authorize(row, user_id=user_id, owner=False)
            if row["status"] not in EDITABLE_STATUSES:
                raise ValueError("Appeal can be edited only while draft changes are open")
            con.execute(
                """UPDATE esp_support_appeals
                   SET statement=?,requested_outcome=?,evidence_pack_sha256='',evidence_bound_at=NULL,
                       owner_review_note='',owner_reviewed_by=NULL,owner_reviewed_at=NULL,updated_at=?
                   WHERE id=?""",
                (statement, requested_outcome, _now(), appeal_id),
            )
            self._activity(con, row["case_id"], user_id, "appeal_draft_updated", {"appeal_id": appeal_id})
        return self.get(appeal_id, user_id=user_id)

    def request_owner_review(self, appeal_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = self._row(con, appeal_id)
            self._authorize(row, user_id=user_id, owner=False)
            if row["status"] not in EDITABLE_STATUSES:
                raise ValueError("Appeal is not waiting for creator edits")
            case_id = row["case_id"]
        pack = self.support.evidence_pack(case_id, user_id=user_id)
        digest = pack["sha256"]
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_support_appeals
                   SET status='owner_review',evidence_pack_sha256=?,evidence_bound_at=?,updated_at=?
                   WHERE id=?""",
                (digest, now, now, appeal_id),
            )
            self._activity(
                con,
                case_id,
                user_id,
                "appeal_owner_review_requested",
                {"appeal_id": appeal_id, "evidence_pack_sha256": digest},
            )
        return self.get(appeal_id, user_id=user_id)

    def owner_review(self, appeal_id: str, actor: str, *, decision: str, note: str) -> dict:
        if decision not in {"ready", "changes_requested"}:
            raise ValueError("Unsupported owner review decision")
        note = (note or "").strip()[:4000]
        if len(note) < 3:
            raise ValueError("Owner review note is required")
        with self._connect() as con:
            row = self._row(con, appeal_id)
            if row["status"] != "owner_review":
                raise ValueError("Appeal is not waiting for owner review")
            case_id = row["case_id"]
        pack = self.support.evidence_pack(case_id, owner=True)
        digest = pack["sha256"]
        status = "ready_for_external_submission" if decision == "ready" else "changes_requested"
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_support_appeals
                   SET status=?,owner_review_note=?,owner_reviewed_by=?,owner_reviewed_at=?,
                       evidence_pack_sha256=?,evidence_bound_at=?,updated_at=? WHERE id=?""",
                (status, note, actor[:120], now, digest, now, now, appeal_id),
            )
            self._activity(
                con,
                case_id,
                actor,
                "appeal_owner_reviewed",
                {"appeal_id": appeal_id, "decision": decision, "evidence_pack_sha256": digest},
            )
        return self.get(appeal_id, owner=True)

    def record_external_submission(
        self,
        appeal_id: str,
        actor_user_id: str,
        *,
        owner: bool,
        destination: str,
        reference: str = "",
    ) -> dict:
        destination = _clean(destination, 240)
        reference = _clean(reference, 500)
        if len(destination) < 2:
            raise ValueError("External destination is required")
        with self._connect() as con:
            row = self._row(con, appeal_id)
            self._authorize(row, user_id=actor_user_id, owner=owner)
            if row["status"] != "ready_for_external_submission":
                raise ValueError("Owner review must mark the appeal ready before external submission can be recorded")
            now = _now()
            con.execute(
                """UPDATE esp_support_appeals
                   SET status='submitted_external',external_destination=?,external_reference=?,
                       external_submitted_at=?,updated_at=? WHERE id=?""",
                (destination, reference, now, now, appeal_id),
            )
            self._activity(
                con,
                row["case_id"],
                actor_user_id,
                "appeal_external_submission_recorded",
                {"appeal_id": appeal_id, "destination": destination, "reference_present": bool(reference)},
            )
        return self.get(appeal_id, user_id=actor_user_id, owner=owner)

    def record_external_outcome(
        self,
        appeal_id: str,
        actor_user_id: str,
        *,
        owner: bool,
        outcome: str,
        note: str,
    ) -> dict:
        if outcome not in {"upheld", "partially_upheld", "denied", "withdrawn", "unknown"}:
            raise ValueError("Unsupported external outcome")
        note = (note or "").strip()[:5000]
        if len(note) < 2:
            raise ValueError("Outcome note is required")
        with self._connect() as con:
            row = self._row(con, appeal_id)
            self._authorize(row, user_id=actor_user_id, owner=owner)
            if row["status"] != "submitted_external":
                raise ValueError("External submission must be recorded before an outcome")
            now = _now()
            con.execute(
                """UPDATE esp_support_appeals
                   SET status='outcome_recorded',outcome_status=?,outcome_note=?,outcome_recorded_by=?,
                       outcome_recorded_at=?,updated_at=? WHERE id=?""",
                (outcome, note, actor_user_id[:120], now, now, appeal_id),
            )
            self._activity(
                con,
                row["case_id"],
                actor_user_id,
                "appeal_external_outcome_recorded",
                {"appeal_id": appeal_id, "outcome": outcome, "command_center_verified": False},
            )
        return self.get(appeal_id, user_id=actor_user_id, owner=owner)

    def withdraw(self, appeal_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = self._row(con, appeal_id)
            self._authorize(row, user_id=user_id, owner=False)
            if row["status"] not in PRE_SUBMISSION_STATUSES:
                raise ValueError("A submitted or completed appeal cannot be withdrawn from this workspace")
            now = _now()
            con.execute("UPDATE esp_support_appeals SET status='withdrawn',updated_at=? WHERE id=?", (now, appeal_id))
            self._activity(con, row["case_id"], user_id, "appeal_withdrawn", {"appeal_id": appeal_id})
        return self.get(appeal_id, user_id=user_id)


appeals = SafetyAppealStore()


def _route_error(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Appeal or support case not found") from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


@router.get("/command-center/api/safety-appeals")
def list_appeals(request: Request):
    member, membership = _require_creator_or_owner(request)
    rows = appeals.list_for_owner() if _is_owner(membership) else appeals.list_for_user(member.user_id)
    return {
        "appeals": rows,
        "manual_external_submission_tracking": True,
        "command_center_transmits_external_appeals": False,
        "automated_outcome_decisions": False,
    }


@router.post("/command-center/api/safety-appeals")
def create_appeal(body: CreateAppealRequest, request: Request):
    member, membership = _require_creator_or_owner(request)
    if _is_owner(membership):
        raise HTTPException(403, "Appeal drafts are created by the affected ESP Creator")
    try:
        row = appeals.create(
            member.user_id,
            case_id=body.case_id,
            appeal_type=body.appeal_type,
            statement=body.statement,
            requested_outcome=body.requested_outcome,
        )
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row}


@router.patch("/command-center/api/safety-appeals/{appeal_id}")
def update_appeal(appeal_id: str, body: UpdateAppealDraftRequest, request: Request):
    member, membership = _require_creator_or_owner(request)
    if _is_owner(membership):
        raise HTTPException(403, "Owners review appeals; Creator draft text is not edited on the Creator's behalf")
    try:
        row = appeals.update_draft(
            appeal_id,
            member.user_id,
            statement=body.statement,
            requested_outcome=body.requested_outcome,
        )
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row}


@router.post("/command-center/api/safety-appeals/{appeal_id}/request-owner-review")
def request_appeal_review(appeal_id: str, request: Request):
    member, membership = _require_creator_or_owner(request)
    if _is_owner(membership):
        raise HTTPException(403, "Creator review request is required before Owner review")
    try:
        row = appeals.request_owner_review(appeal_id, member.user_id)
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row}


@router.post("/command-center/api/safety-appeals/{appeal_id}/withdraw")
def withdraw_appeal(appeal_id: str, request: Request):
    member, membership = _require_creator_or_owner(request)
    if _is_owner(membership):
        raise HTTPException(403, "Only the affected Creator can withdraw their prepared appeal")
    try:
        row = appeals.withdraw(appeal_id, member.user_id)
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row}


@router.get("/command-center/api/safety-appeals/owner/queue")
def owner_appeal_queue(request: Request, status: str | None = None):
    _member, _membership = _require_owner(request)
    return {"appeals": appeals.list_for_owner(status=status), "human_owner_review": True}


@router.post("/command-center/api/safety-appeals/owner/{appeal_id}/review")
def owner_review_appeal(appeal_id: str, body: OwnerReviewRequest, request: Request):
    member, _membership = _require_owner(request)
    try:
        row = appeals.owner_review(appeal_id, member.user_id, decision=body.decision, note=body.note)
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row}


@router.post("/command-center/api/safety-appeals/{appeal_id}/external-submission")
def record_appeal_submission(appeal_id: str, body: ExternalSubmissionRequest, request: Request):
    member, membership = _require_creator_or_owner(request)
    try:
        row = appeals.record_external_submission(
            appeal_id,
            member.user_id,
            owner=_is_owner(membership),
            destination=body.destination,
            reference=body.reference,
        )
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row, "transmitted_by_command_center": False}


@router.post("/command-center/api/safety-appeals/{appeal_id}/external-outcome")
def record_appeal_outcome(appeal_id: str, body: ExternalOutcomeRequest, request: Request):
    member, membership = _require_creator_or_owner(request)
    try:
        row = appeals.record_external_outcome(
            appeal_id,
            member.user_id,
            owner=_is_owner(membership),
            outcome=body.outcome,
            note=body.note,
        )
    except Exception as exc:
        _route_error(exc)
    return {"appeal": row, "external_outcome_verified_by_command_center": False}


CSS = """
:root{--line:#ffffff20;--muted:#c9c0d5;--gold:#efc86f;--violet:#9f70ff;--good:#78dfa7;--bad:#ff9aae}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#4c1b61,transparent 30%),#07060d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:38px 0 64px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.5rem,7vw,5rem);line-height:.94;letter-spacing:-.05em;margin:.16em 0}.muted{color:var(--muted);line-height:1.55}.card{border:1px solid var(--line);border-radius:16px;background:#15111eec;padding:15px;margin:10px 0}.btn,.field{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:#0b0911;color:#fff;font:inherit}.btn{font-weight:850;cursor:pointer;text-decoration:none;display:inline-block}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#180e20}.field{width:100%;margin:5px 0}textarea.field{min-height:125px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.72rem}.safe{border-left:3px solid var(--good)}@media(max-width:780px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/safety-appeals',q=s=>document.querySelector(s);let data=[];function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function req(u,o={}){const r=await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||'Request failed');return d}function render(rows){return (rows||[]).map(a=>`<article class="card"><div class="row"><div><span class="pill">${esc(a.appeal_type)} · ${esc(a.status)}</span><h2>${esc(a.case?.subject||'Appeal')}</h2><p class="muted">${esc(a.statement)}</p><p><b>Requested outcome:</b> ${esc(a.requested_outcome)}</p></div><div>${['draft','changes_requested'].includes(a.status)?`<button class="btn primary" onclick="reviewReq('${esc(a.id)}')">Request Owner Review</button>`:''}${a.status==='ready_for_external_submission'?` <button class="btn" onclick="submitted('${esc(a.id)}')">Record External Submission</button>`:''}${a.status==='submitted_external'?` <button class="btn" onclick="outcome('${esc(a.id)}')">Record Outcome</button>`:''}</div></div>${a.owner_review_note?`<p><b>Owner review:</b> ${esc(a.owner_review_note)}</p>`:''}${a.evidence_pack_sha256?`<p class="muted">Reviewed evidence SHA-256: ${esc(a.evidence_pack_sha256)}</p>`:''}</article>`).join('')||'<div class="card muted">No appeal workflows yet.</div>'}async function load(){try{const d=await req(API);data=d.appeals||[];q('#appeals').innerHTML=render(data)}catch(e){q('#appeals').innerHTML=`<div class="card">${esc(e.message)}</div>`}}q('#create').onclick=async()=>{try{await req(API,{method:'POST',body:JSON.stringify({case_id:q('#case').value,appeal_type:q('#type').value,statement:q('#statement').value,requested_outcome:q('#requested').value})});q('#statement').value='';q('#requested').value='';load()}catch(e){alert(e.message)}};async function reviewReq(id){try{await req(`${API}/${id}/request-owner-review`,{method:'POST'});load()}catch(e){alert(e.message)}}async function submitted(id){const destination=prompt('Where did you manually submit this appeal/report?')||'';if(!destination)return;const reference=prompt('External ticket/reference (optional):')||'';try{await req(`${API}/${id}/external-submission`,{method:'POST',body:JSON.stringify({destination,reference})});load()}catch(e){alert(e.message)}}async function outcome(id){const value=prompt('External outcome: upheld, partially_upheld, denied, withdrawn or unknown','unknown')||'';if(!value)return;const note=prompt('Outcome note:')||'';if(!note)return;try{await req(`${API}/${id}/external-outcome`,{method:'POST',body:JSON.stringify({outcome:value,note})});load()}catch(e){alert(e.message)}}load();
"""


@router.get("/command-center/safety-appeals", response_class=HTMLResponse, include_in_schema=False)
def safety_appeals_page(request: Request):
    _require_creator_or_owner(request)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Creator Safety & Appeals</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Private Creator Support</div><h1>Safety & Appeals Workspace</h1><p class='muted'>Prepare evidence-bound appeal/report material, request Mary/Kev owner review, and record what you submit externally. The Command Center does not transmit, decide or guarantee an external platform appeal.</p></div><div><a class='btn' href='/command-center/support'>Support & Evidence</a><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><section class='card safe'><b>Creator safety guidance</b><p class='muted'>Preserve evidence and dates, avoid publicly reposting sensitive material, use the relevant platform's block/report/safety controls, contact ESP support or your assigned Agent when appropriate, and step away from LIVE/content work when you need a break. If there is immediate danger, use the appropriate local emergency service or trusted real-world support.</p><p class='muted'>This workspace records operational support only; it does not diagnose wellbeing or make automated safety, guilt or policy decisions.</p></section><section class='grid'><div class='card'><h2>Prepare an appeal</h2><input class='field' id='case' placeholder='Existing private support case ID'><select class='field' id='type'><option value='violation_notice'>Violation notice</option><option value='policy_decision'>Policy decision</option><option value='content_moderation'>Content moderation</option><option value='account_restriction'>Account restriction</option><option value='harassment_report'>Harassment report</option><option value='impersonation_report'>Impersonation report</option><option value='ip_dispute'>IP dispute</option><option value='other'>Other</option></select><textarea class='field' id='statement' placeholder='Your clear factual appeal/report statement'></textarea><textarea class='field' id='requested' placeholder='What outcome are you requesting?'></textarea><button class='btn primary' id='create'>Create private draft</button></div><div class='card'><h2>How the workflow works</h2><p class='muted'>1. Create or use an eligible private Support & Evidence case. 2. Prepare your factual statement. 3. Request Owner review. 4. Mary/Kev can mark it ready or request changes. 5. You submit through the external platform's real process. 6. Record the external reference/outcome here for your private ESP history.</p></div></section><section id='appeals'><div class='card muted'>Loading…</div></section></main><script>{SCRIPT}</script></body></html>"""
    )


__all__ = ["router", "SafetyAppealStore", "appeals"]
