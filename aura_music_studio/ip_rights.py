from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .owner_auth import owner_authorized
from .owner_identity import owner_actor

member_router = APIRouter(prefix="/member/ip-rights", tags=["Member IP Rights"])
owner_router = APIRouter(prefix="/owner/ip-rights", tags=["Owner IP Rights Review"])

TERMINAL_NOTICE_STATES = {"accepted", "rejected", "withdrawn"}
TERMINAL_COUNTER_STATES = {"accepted", "rejected", "withdrawn"}
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._:-]{1,180}$")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_ref(value: str, *, field_name: str, required: bool = True) -> str:
    value = str(value or "").strip()
    if not value and not required:
        return ""
    if not _OPAQUE_REF.fullmatch(value):
        raise ValueError(f"{field_name} must be an opaque identifier, not a URL/path/free-form payload")
    return value


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authenticated member session required")
    return str(user_id)


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


class RightsNoticeInput(BaseModel):
    rights_type: Literal["copyright", "trademark", "design", "patent", "other"]
    jurisdiction_profile: Literal["us_dmca", "uk", "eu_dsa", "other"]
    claimant_basis: Literal["rights_holder", "authorised_agent", "licensee", "other"]
    protected_work_reference: str = Field(min_length=1, max_length=180)
    target_reference: str = Field(min_length=1, max_length=180)
    contact_evidence_reference: str = Field(min_length=1, max_length=180)
    signature_evidence_reference: str = Field(min_length=1, max_length=180)
    detail: str = Field(min_length=1, max_length=2500)
    good_faith_belief: bool
    accuracy_and_authority_attested: bool


class CounterNoticeInput(BaseModel):
    reason: str = Field(min_length=1, max_length=2500)
    contact_evidence_reference: str = Field(min_length=1, max_length=180)
    signature_evidence_reference: str = Field(min_length=1, max_length=180)
    good_faith_mistake_or_misidentification: bool
    us_jurisdiction_and_service_attested: bool = False


class OwnerNoticeReviewInput(BaseModel):
    status: Literal["needs_information", "under_review", "action_recommended", "accepted", "rejected", "withdrawn"]
    reason: str = Field(min_length=1, max_length=2500)
    decision_evidence_reference: str = Field(default="", max_length=180)
    external_content_action_reference: str = Field(default="", max_length=180)
    counter_notice_eligible_user_id: str = Field(default="", max_length=180)


class OwnerCounterReviewInput(BaseModel):
    status: Literal["needs_information", "under_review", "accepted", "rejected", "withdrawn"]
    reason: str = Field(min_length=1, max_length=2500)
    decision_evidence_reference: str = Field(default="", max_length=180)


class IPRightsStore:
    """Evidence-led IP workflow; not an automated takedown or legal-certification engine."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS ip_rights_notices (
                    id TEXT PRIMARY KEY,
                    claimant_user_id TEXT NOT NULL,
                    rights_type TEXT NOT NULL,
                    jurisdiction_profile TEXT NOT NULL,
                    claimant_basis TEXT NOT NULL,
                    protected_work_reference TEXT NOT NULL,
                    target_reference TEXT NOT NULL,
                    contact_evidence_reference TEXT NOT NULL,
                    signature_evidence_reference TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    good_faith_belief INTEGER NOT NULL,
                    accuracy_and_authority_attested INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    decision_evidence_reference TEXT NOT NULL DEFAULT '',
                    external_content_action_reference TEXT NOT NULL DEFAULT '',
                    counter_notice_eligible_user_id TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ip_notices_claimant
                    ON ip_rights_notices(claimant_user_id, submitted_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ip_notices_status
                    ON ip_rights_notices(status, submitted_at ASC);
                CREATE TABLE IF NOT EXISTS ip_rights_counter_notices (
                    id TEXT PRIMARY KEY,
                    notice_id TEXT NOT NULL,
                    submitter_user_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    contact_evidence_reference TEXT NOT NULL,
                    signature_evidence_reference TEXT NOT NULL,
                    good_faith_mistake_or_misidentification INTEGER NOT NULL,
                    us_jurisdiction_and_service_attested INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    decision_evidence_reference TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ip_counter_notice
                    ON ip_rights_counter_notices(notice_id, submitted_at DESC);
                CREATE TABLE IF NOT EXISTS ip_rights_events (
                    id TEXT PRIMARY KEY,
                    notice_id TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_ip_events_notice
                    ON ip_rights_events(notice_id, occurred_at ASC, id ASC);
                """
            )

    def _append_event(self, con: sqlite3.Connection, *, notice_id: str, actor_type: str, action: str, data: dict) -> None:
        previous = con.execute(
            "SELECT event_hash FROM ip_rights_events WHERE notice_id=? ORDER BY occurred_at DESC,id DESC LIMIT 1",
            (notice_id,),
        ).fetchone()
        previous_hash = str(previous["event_hash"]) if previous else "GENESIS"
        event_id = uuid4().hex
        occurred_at = _iso()
        event_data = dict(data)
        if actor_type == "owner":
            event_data.setdefault("reviewer_actor", owner_actor())
        payload = {"id": event_id, "notice_id": notice_id, "actor_type": actor_type,
                   "action": action, "occurred_at": occurred_at, "data": event_data,
                   "previous_hash": previous_hash}
        event_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        con.execute(
            "INSERT INTO ip_rights_events(id,notice_id,actor_type,action,occurred_at,data_json,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (event_id, notice_id, actor_type, action, occurred_at, _canonical_json(event_data), previous_hash, event_hash),
        )

    def submit_notice(self, *, claimant_user_id: str, payload: RightsNoticeInput) -> dict:
        claimant_user_id = str(claimant_user_id or "").strip()
        if not claimant_user_id:
            raise ValueError("Authenticated member id is required")
        work_ref = _validate_ref(payload.protected_work_reference, field_name="Protected work reference")
        target_ref = _validate_ref(payload.target_reference, field_name="Target reference")
        contact_ref = _validate_ref(payload.contact_evidence_reference, field_name="Contact evidence reference")
        signature_ref = _validate_ref(payload.signature_evidence_reference, field_name="Signature evidence reference")
        if not payload.good_faith_belief or not payload.accuracy_and_authority_attested:
            raise ValueError("Rights notices require good-faith and accuracy/authority attestations before review")
        now = _iso()
        notice_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO ip_rights_notices
                   (id,claimant_user_id,rights_type,jurisdiction_profile,claimant_basis,protected_work_reference,
                    target_reference,contact_evidence_reference,signature_evidence_reference,detail,
                    good_faith_belief,accuracy_and_authority_attested,status,submitted_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (notice_id, claimant_user_id, payload.rights_type, payload.jurisdiction_profile, payload.claimant_basis,
                 work_ref, target_ref, contact_ref, signature_ref, payload.detail.strip(), 1, 1, "submitted", now, now),
            )
            self._append_event(con, notice_id=notice_id, actor_type="member", action="rights_notice_submitted",
                               data={"rights_type": payload.rights_type, "jurisdiction_profile": payload.jurisdiction_profile,
                                     "automatic_content_action_taken": False, "legal_sufficiency_certified": False})
        return self.get_for_member(notice_id, claimant_user_id)

    def list_for_member(self, user_id: str, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,rights_type,jurisdiction_profile,claimant_basis,target_reference,status,submitted_at,updated_at FROM ip_rights_notices WHERE claimant_user_id=? ORDER BY submitted_at DESC,id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_for_member(self, notice_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM ip_rights_notices WHERE id=? AND claimant_user_id=?", (notice_id, user_id)).fetchone()
            if not row:
                raise KeyError("IP rights notice not found")
        notice = dict(row)
        notice["good_faith_belief"] = bool(notice["good_faith_belief"])
        notice["accuracy_and_authority_attested"] = bool(notice["accuracy_and_authority_attested"])
        notice.pop("counter_notice_eligible_user_id", None)
        return {"notice": notice, "automatic_content_action_taken": False,
                "legal_sufficiency_certified": False}

    def submit_counter_notice(self, *, notice_id: str, submitter_user_id: str, payload: CounterNoticeInput) -> dict:
        contact_ref = _validate_ref(payload.contact_evidence_reference, field_name="Contact evidence reference")
        signature_ref = _validate_ref(payload.signature_evidence_reference, field_name="Signature evidence reference")
        if not payload.good_faith_mistake_or_misidentification:
            raise ValueError("Counter-notice requires a good-faith mistake/misidentification attestation")
        with self._connect() as con:
            notice = con.execute("SELECT * FROM ip_rights_notices WHERE id=?", (notice_id,)).fetchone()
            if not notice:
                raise KeyError("IP rights notice not found")
            eligible = str(notice["counter_notice_eligible_user_id"] or "")
            if not eligible or eligible != str(submitter_user_id):
                raise PermissionError("Authenticated member is not the verified counter-notice respondent")
            if str(notice["status"]) not in {"action_recommended", "accepted"}:
                raise ValueError("Counter-notice is available only after the notice reaches an action/accepted review state")
            if str(notice["jurisdiction_profile"]) == "us_dmca" and not payload.us_jurisdiction_and_service_attested:
                raise ValueError("US DMCA profile requires jurisdiction/service attestation evidence before counter-notice review")
            existing = con.execute(
                "SELECT * FROM ip_rights_counter_notices WHERE notice_id=? AND submitter_user_id=? AND status IN ('submitted','needs_information','under_review') ORDER BY submitted_at DESC LIMIT 1",
                (notice_id, submitter_user_id),
            ).fetchone()
            if existing:
                return dict(existing)
            now = _iso()
            counter_id = uuid4().hex
            con.execute(
                """INSERT INTO ip_rights_counter_notices
                   (id,notice_id,submitter_user_id,reason,contact_evidence_reference,signature_evidence_reference,
                    good_faith_mistake_or_misidentification,us_jurisdiction_and_service_attested,status,submitted_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (counter_id, notice_id, submitter_user_id, payload.reason.strip(), contact_ref, signature_ref, 1,
                 int(payload.us_jurisdiction_and_service_attested), "submitted", now, now),
            )
            self._append_event(con, notice_id=notice_id, actor_type="member", action="counter_notice_submitted",
                               data={"counter_notice_id": counter_id, "automatic_content_restoration": False,
                                     "legal_sufficiency_certified": False})
            return dict(con.execute("SELECT * FROM ip_rights_counter_notices WHERE id=?", (counter_id,)).fetchone())

    def owner_list(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,claimant_user_id,rights_type,jurisdiction_profile,target_reference,status,submitted_at,updated_at FROM ip_rights_notices ORDER BY submitted_at ASC,id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def owner_review_notice(self, *, notice_id: str, payload: OwnerNoticeReviewInput) -> dict:
        decision_ref = _validate_ref(payload.decision_evidence_reference, field_name="Decision evidence reference", required=False)
        action_ref = _validate_ref(payload.external_content_action_reference, field_name="External content action reference", required=False)
        eligible_user = _validate_ref(payload.counter_notice_eligible_user_id, field_name="Counter-notice eligible user id", required=False)
        if payload.status in {"accepted", "rejected"} and not decision_ref:
            raise ValueError("Terminal legal-review decisions require an opaque decision evidence reference")
        with self._connect() as con:
            notice = con.execute("SELECT * FROM ip_rights_notices WHERE id=?", (notice_id,)).fetchone()
            if not notice:
                raise KeyError("IP rights notice not found")
            current = str(notice["status"])
            if current in TERMINAL_NOTICE_STATES and payload.status != current:
                raise ValueError("Terminal notice state cannot be changed through ordinary review")
            now = _iso()
            con.execute(
                "UPDATE ip_rights_notices SET status=?,decision_evidence_reference=?,external_content_action_reference=?,counter_notice_eligible_user_id=?,updated_at=? WHERE id=?",
                (payload.status, decision_ref, action_ref, eligible_user, now, notice_id),
            )
            self._append_event(con, notice_id=notice_id, actor_type="owner", action="notice_review",
                               data={"from": current, "to": payload.status, "reason": payload.reason,
                                     "decision_evidence_reference": decision_ref,
                                     "external_content_action_reference": action_ref,
                                     "counter_notice_respondent_bound": bool(eligible_user),
                                     "automatic_content_action_taken": False})
        return self.owner_get(notice_id)

    def owner_review_counter(self, *, notice_id: str, counter_id: str, payload: OwnerCounterReviewInput) -> dict:
        decision_ref = _validate_ref(payload.decision_evidence_reference, field_name="Decision evidence reference", required=False)
        if payload.status in {"accepted", "rejected"} and not decision_ref:
            raise ValueError("Terminal counter-notice decisions require an opaque decision evidence reference")
        with self._connect() as con:
            counter = con.execute("SELECT * FROM ip_rights_counter_notices WHERE id=? AND notice_id=?", (counter_id, notice_id)).fetchone()
            if not counter:
                raise KeyError("Counter-notice not found")
            current = str(counter["status"])
            if current in TERMINAL_COUNTER_STATES and payload.status != current:
                raise ValueError("Terminal counter-notice state cannot be changed through ordinary review")
            now = _iso()
            con.execute("UPDATE ip_rights_counter_notices SET status=?,decision_evidence_reference=?,updated_at=? WHERE id=?",
                        (payload.status, decision_ref, now, counter_id))
            self._append_event(con, notice_id=notice_id, actor_type="owner", action="counter_notice_review",
                               data={"counter_notice_id": counter_id, "from": current, "to": payload.status,
                                     "reason": payload.reason, "decision_evidence_reference": decision_ref,
                                     "automatic_content_restoration": False})
        return self.owner_get(notice_id)

    def owner_get(self, notice_id: str) -> dict:
        with self._connect() as con:
            notice = con.execute("SELECT * FROM ip_rights_notices WHERE id=?", (notice_id,)).fetchone()
            if not notice:
                raise KeyError("IP rights notice not found")
            counters = con.execute("SELECT * FROM ip_rights_counter_notices WHERE notice_id=? ORDER BY submitted_at ASC,id ASC", (notice_id,)).fetchall()
            events = con.execute("SELECT * FROM ip_rights_events WHERE notice_id=? ORDER BY occurred_at ASC,id ASC", (notice_id,)).fetchall()
        previous_hash = "GENESIS"
        chain_valid = True
        parsed_events = []
        for raw in events:
            item = dict(raw)
            data = json.loads(item.pop("data_json"))
            payload = {"id": item["id"], "notice_id": item["notice_id"], "actor_type": item["actor_type"],
                       "action": item["action"], "occurred_at": item["occurred_at"], "data": data,
                       "previous_hash": item["previous_hash"]}
            expected = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
            if item["previous_hash"] != previous_hash or item["event_hash"] != expected:
                chain_valid = False
            previous_hash = item["event_hash"]
            parsed_events.append({**payload, "event_hash": item["event_hash"]})
        return {"notice": dict(notice), "counter_notices": [dict(row) for row in counters],
                "events": parsed_events, "audit_chain_valid": chain_valid,
                "automatic_content_action_taken": False, "automatic_content_restoration": False,
                "legal_sufficiency_certified": False, "grants_esp_role_or_permission": False,
                "changes_billing_or_membership": False}


@member_router.get("/notices")
def member_notices(request: Request):
    return {"notices": IPRightsStore().list_for_member(_member_user_id(request)),
            "automatic_content_action_taken": False, "legal_sufficiency_certified": False}


@member_router.post("/notices")
def member_submit_notice(request: Request, payload: RightsNoticeInput):
    try:
        result = IPRightsStore().submit_notice(claimant_user_id=_member_user_id(request), payload=payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**result, "grants_esp_role_or_permission": False, "changes_billing_or_membership": False}


@member_router.get("/notices/{notice_id}")
def member_notice_detail(notice_id: str, request: Request):
    try:
        return IPRightsStore().get_for_member(notice_id, _member_user_id(request))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@member_router.post("/notices/{notice_id}/counter-notices")
def member_counter_notice(notice_id: str, request: Request, payload: CounterNoticeInput):
    try:
        counter = IPRightsStore().submit_counter_notice(notice_id=notice_id,
            submitter_user_id=_member_user_id(request), payload=payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"counter_notice": counter, "automatic_content_restoration": False,
            "legal_sufficiency_certified": False, "grants_esp_role_or_permission": False}


@owner_router.get("/notices", include_in_schema=False)
def owner_notices(request: Request):
    _require_owner(request)
    return {"notices": IPRightsStore().owner_list(), "automatic_content_action_taken": False}


@owner_router.get("/notices/{notice_id}", include_in_schema=False)
def owner_notice_detail(notice_id: str, request: Request):
    _require_owner(request)
    try:
        return IPRightsStore().owner_get(notice_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@owner_router.post("/notices/{notice_id}/review", include_in_schema=False)
def owner_review_notice(notice_id: str, request: Request, payload: OwnerNoticeReviewInput):
    _require_owner(request)
    try:
        return IPRightsStore().owner_review_notice(notice_id=notice_id, payload=payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@owner_router.post("/notices/{notice_id}/counter-notices/{counter_id}/review", include_in_schema=False)
def owner_review_counter(notice_id: str, counter_id: str, request: Request, payload: OwnerCounterReviewInput):
    _require_owner(request)
    try:
        return IPRightsStore().owner_review_counter(notice_id=notice_id, counter_id=counter_id, payload=payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["IPRightsStore", "member_router", "owner_router"]