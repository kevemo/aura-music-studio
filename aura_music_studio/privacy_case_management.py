from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .owner_auth import owner_authorized
from .owner_identity import owner_actor

router = APIRouter(prefix="/owner/privacy", tags=["Owner Privacy Case Management"])

IDENTITY_STATES = ("unverified", "pending", "verified", "failed")
CASE_STATES = ("submitted", "under_review", "awaiting_identity", "ready_for_fulfilment", "fulfilled", "denied", "withdrawn")
TERMINAL_STATES = {"fulfilled", "denied", "withdrawn"}
DELETION_EXECUTION_KINDS = ("deleted", "anonymised", "mixed")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class CaseContextInput(BaseModel):
    jurisdiction: str = Field(min_length=2, max_length=80)
    legal_basis: str = Field(min_length=2, max_length=240)
    due_at: str | None = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=1000)


class IdentityReviewInput(BaseModel):
    status: Literal["unverified", "pending", "verified", "failed"]
    method: str = Field(default="", max_length=120)
    evidence_reference: str = Field(default="", max_length=240)
    note: str = Field(default="", max_length=1000)


class HoldInput(BaseModel):
    legal_hold: bool
    retention_hold: bool
    reason: str = Field(default="", max_length=1000)


class CaseTransitionInput(BaseModel):
    status: Literal[
        "submitted", "under_review", "awaiting_identity", "ready_for_fulfilment", "fulfilled", "denied", "withdrawn"
    ]
    fulfilment_reference: str = Field(default="", max_length=240)
    reason: str = Field(default="", max_length=1000)


class DeletionExecutionInput(BaseModel):
    execution_kind: Literal["deleted", "anonymised", "mixed"]
    evidence_reference: str = Field(min_length=2, max_length=240)
    retained_exception_reference: str = Field(default="", max_length=240)
    note: str = Field(default="", max_length=1000)


class PrivacyCaseStore:
    """Owner-only review controls layered over authenticated member privacy requests.

    This module stores review evidence and workflow state. It never performs disclosure,
    export, correction, deletion, account mutation, or ESP role mutation itself. A deletion
    may only reach ``fulfilled`` after a separate execution-confirmation record references
    the actual deletion/anonymisation operation performed by the responsible subsystem.
    """

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

    @staticmethod
    def _ensure_column(con: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS privacy_case_controls (
                    request_id TEXT PRIMARY KEY,
                    jurisdiction TEXT NOT NULL DEFAULT '',
                    legal_basis TEXT NOT NULL DEFAULT '',
                    due_at TEXT,
                    identity_status TEXT NOT NULL DEFAULT 'unverified',
                    identity_method TEXT NOT NULL DEFAULT '',
                    identity_evidence_reference TEXT NOT NULL DEFAULT '',
                    legal_hold INTEGER NOT NULL DEFAULT 0,
                    retention_hold INTEGER NOT NULL DEFAULT 0,
                    fulfilment_reference TEXT NOT NULL DEFAULT '',
                    deletion_execution_kind TEXT NOT NULL DEFAULT '',
                    deletion_execution_reference TEXT NOT NULL DEFAULT '',
                    deletion_executed_at TEXT,
                    retained_exception_reference TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS privacy_case_events (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_privacy_case_events_request
                    ON privacy_case_events(request_id, occurred_at ASC, id ASC);
                """
            )
            # Existing installations predate deletion execution evidence. Add columns in place.
            self._ensure_column(con, "privacy_case_controls", "deletion_execution_kind", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(con, "privacy_case_controls", "deletion_execution_reference", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(con, "privacy_case_controls", "deletion_executed_at", "TEXT")
            self._ensure_column(con, "privacy_case_controls", "retained_exception_reference", "TEXT NOT NULL DEFAULT ''")

    def _request_row(self, con: sqlite3.Connection, request_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM privacy_rights_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise KeyError("Privacy request not found")
        return row

    def _ensure_control(self, con: sqlite3.Connection, request_id: str) -> sqlite3.Row:
        self._request_row(con, request_id)
        row = con.execute("SELECT * FROM privacy_case_controls WHERE request_id=?", (request_id,)).fetchone()
        if row:
            return row
        con.execute(
            "INSERT INTO privacy_case_controls(request_id,updated_at) VALUES (?,?)",
            (request_id, _iso()),
        )
        return con.execute("SELECT * FROM privacy_case_controls WHERE request_id=?", (request_id,)).fetchone()

    def _append_event(self, con: sqlite3.Connection, *, request_id: str, action: str, data: dict) -> dict:
        previous = con.execute(
            "SELECT event_hash FROM privacy_case_events WHERE request_id=? ORDER BY occurred_at DESC,id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        previous_hash = str(previous["event_hash"]) if previous else "GENESIS"
        occurred_at = _iso()
        event_id = uuid4().hex
        event_data = dict(data)
        event_data.setdefault("reviewer_actor", owner_actor())
        payload = {
            "id": event_id,
            "request_id": request_id,
            "actor": "owner_session",
            "action": action,
            "occurred_at": occurred_at,
            "data": event_data,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        con.execute(
            """INSERT INTO privacy_case_events
               (id,request_id,actor,action,occurred_at,data_json,previous_hash,event_hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (event_id, request_id, "owner_session", action, occurred_at, _canonical_json(event_data), previous_hash, event_hash),
        )
        return {**payload, "event_hash": event_hash}

    def set_context(self, request_id: str, *, jurisdiction: str, legal_basis: str, due_at: str | None, note: str = "") -> dict:
        jurisdiction = jurisdiction.strip()
        legal_basis = legal_basis.strip()
        note = note.strip()
        if not jurisdiction or not legal_basis:
            raise ValueError("Jurisdiction and legal basis are required")
        with self._connect() as con:
            self._ensure_control(con, request_id)
            con.execute(
                """UPDATE privacy_case_controls
                   SET jurisdiction=?,legal_basis=?,due_at=?,updated_at=? WHERE request_id=?""",
                (jurisdiction, legal_basis, due_at or None, _iso(), request_id),
            )
            self._append_event(
                con,
                request_id=request_id,
                action="context_reviewed",
                data={"jurisdiction": jurisdiction, "legal_basis": legal_basis, "due_at": due_at, "note": note},
            )
        return self.get_case(request_id)

    def set_identity(self, request_id: str, *, status: str, method: str = "", evidence_reference: str = "", note: str = "") -> dict:
        if status not in IDENTITY_STATES:
            raise ValueError("Unsupported identity-review state")
        if status == "verified" and not evidence_reference.strip():
            raise ValueError("Verified identity requires an evidence reference")
        with self._connect() as con:
            self._ensure_control(con, request_id)
            con.execute(
                """UPDATE privacy_case_controls
                   SET identity_status=?,identity_method=?,identity_evidence_reference=?,updated_at=? WHERE request_id=?""",
                (status, method.strip(), evidence_reference.strip(), _iso(), request_id),
            )
            self._append_event(
                con,
                request_id=request_id,
                action="identity_reviewed",
                data={
                    "status": status,
                    "method": method.strip(),
                    "evidence_reference": evidence_reference.strip(),
                    "note": note.strip(),
                    "raw_identity_document_stored": False,
                },
            )
        return self.get_case(request_id)

    def set_holds(self, request_id: str, *, legal_hold: bool, retention_hold: bool, reason: str = "") -> dict:
        if (legal_hold or retention_hold) and not reason.strip():
            raise ValueError("A hold reason is required")
        with self._connect() as con:
            self._ensure_control(con, request_id)
            con.execute(
                """UPDATE privacy_case_controls
                   SET legal_hold=?,retention_hold=?,updated_at=? WHERE request_id=?""",
                (int(legal_hold), int(retention_hold), _iso(), request_id),
            )
            self._append_event(
                con,
                request_id=request_id,
                action="holds_updated",
                data={"legal_hold": legal_hold, "retention_hold": retention_hold, "reason": reason.strip()},
            )
        return self.get_case(request_id)

    def record_deletion_execution(
        self,
        request_id: str,
        *,
        execution_kind: str,
        evidence_reference: str,
        retained_exception_reference: str = "",
        note: str = "",
    ) -> dict:
        if execution_kind not in DELETION_EXECUTION_KINDS:
            raise ValueError("Unsupported deletion execution kind")
        evidence_reference = evidence_reference.strip()
        retained_exception_reference = retained_exception_reference.strip()
        note = note.strip()
        if not evidence_reference:
            raise ValueError("Deletion execution requires an evidence reference")
        if execution_kind == "mixed" and not retained_exception_reference:
            raise ValueError("Mixed deletion/anonymisation requires a retained-exception reference")

        with self._connect() as con:
            request_row = self._request_row(con, request_id)
            control = self._ensure_control(con, request_id)
            if str(request_row["request_type"]) != "deletion":
                raise ValueError("Deletion execution evidence can only be recorded for deletion requests")
            if str(request_row["status"]) != "ready_for_fulfilment":
                raise ValueError("Deletion request must be ready for fulfilment before execution evidence is recorded")
            if str(control["identity_status"]) != "verified":
                raise ValueError("Verified identity is required before deletion execution is recorded")
            if bool(control["legal_hold"]) or bool(control["retention_hold"]):
                raise ValueError("Active legal or retention hold blocks deletion execution confirmation")
            if not str(control["jurisdiction"] or "").strip() or not str(control["legal_basis"] or "").strip():
                raise ValueError("Jurisdiction and legal basis review are required before deletion execution is recorded")

            executed_at = _iso()
            con.execute(
                """UPDATE privacy_case_controls
                   SET deletion_execution_kind=?,deletion_execution_reference=?,deletion_executed_at=?,
                       retained_exception_reference=?,updated_at=? WHERE request_id=?""",
                (
                    execution_kind,
                    evidence_reference,
                    executed_at,
                    retained_exception_reference,
                    executed_at,
                    request_id,
                ),
            )
            self._append_event(
                con,
                request_id=request_id,
                action="deletion_execution_recorded",
                data={
                    "execution_kind": execution_kind,
                    "evidence_reference": evidence_reference,
                    "executed_at": executed_at,
                    "retained_exception_reference": retained_exception_reference,
                    "note": note,
                    "operation_performed_by_case_management": False,
                },
            )
        return self.get_case(request_id)

    def transition(self, request_id: str, *, status: str, fulfilment_reference: str = "", reason: str = "") -> dict:
        if status not in CASE_STATES:
            raise ValueError("Unsupported case state")
        with self._connect() as con:
            request_row = self._request_row(con, request_id)
            control = self._ensure_control(con, request_id)
            current = str(request_row["status"])
            if current in TERMINAL_STATES and status != current:
                raise ValueError("Terminal privacy cases cannot be reopened by this endpoint")
            if status in {"ready_for_fulfilment", "fulfilled"}:
                if str(control["identity_status"]) != "verified":
                    raise ValueError("Verified identity is required before fulfilment")
                if bool(control["legal_hold"]) or bool(control["retention_hold"]):
                    raise ValueError("Active legal or retention hold blocks fulfilment")
                if not str(control["jurisdiction"] or "").strip() or not str(control["legal_basis"] or "").strip():
                    raise ValueError("Jurisdiction and legal basis review are required before fulfilment")
            if status == "fulfilled" and not fulfilment_reference.strip():
                raise ValueError("Fulfilment requires an evidence reference")
            if status == "fulfilled" and str(request_row["request_type"]) == "deletion":
                if not (
                    str(control["deletion_execution_kind"] or "").strip()
                    and str(control["deletion_execution_reference"] or "").strip()
                    and str(control["deletion_executed_at"] or "").strip()
                ):
                    raise ValueError("Deletion cannot be marked fulfilled until execution evidence is recorded")
            now = _iso()
            con.execute(
                "UPDATE privacy_rights_requests SET status=?,updated_at=? WHERE id=?",
                (status, now, request_id),
            )
            if fulfilment_reference.strip():
                con.execute(
                    "UPDATE privacy_case_controls SET fulfilment_reference=?,updated_at=? WHERE request_id=?",
                    (fulfilment_reference.strip(), now, request_id),
                )
            self._append_event(
                con,
                request_id=request_id,
                action="status_transition",
                data={
                    "from": current,
                    "to": status,
                    "reason": reason.strip(),
                    "fulfilment_reference": fulfilment_reference.strip(),
                    "automatic_data_action_taken": False,
                },
            )
        return self.get_case(request_id)

    def get_case(self, request_id: str) -> dict:
        with self._connect() as con:
            request_row = self._request_row(con, request_id)
            control = self._ensure_control(con, request_id)
            events = con.execute(
                "SELECT * FROM privacy_case_events WHERE request_id=? ORDER BY occurred_at ASC,id ASC",
                (request_id,),
            ).fetchall()
        parsed_events = []
        previous_hash = "GENESIS"
        chain_valid = True
        for row in events:
            item = dict(row)
            data = json.loads(item.pop("data_json"))
            payload = {
                "id": item["id"],
                "request_id": item["request_id"],
                "actor": item["actor"],
                "action": item["action"],
                "occurred_at": item["occurred_at"],
                "data": data,
                "previous_hash": item["previous_hash"],
            }
            expected = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
            if item["previous_hash"] != previous_hash or item["event_hash"] != expected:
                chain_valid = False
            previous_hash = item["event_hash"]
            parsed_events.append({**payload, "event_hash": item["event_hash"]})
        return {
            "request": dict(request_row),
            "control": {**dict(control), "legal_hold": bool(control["legal_hold"]), "retention_hold": bool(control["retention_hold"])},
            "events": parsed_events,
            "audit_chain_valid": chain_valid,
            "automatic_data_action_taken": False,
            "grants_esp_role_or_permission": False,
        }

    def list_cases(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as con:
            rows = con.execute(
                """SELECT r.id,r.user_id,r.request_type,r.status,r.locale,r.submitted_at,r.updated_at,
                          c.jurisdiction,c.due_at,c.identity_status,c.legal_hold,c.retention_hold,
                          c.deletion_execution_kind,c.deletion_executed_at
                   FROM privacy_rights_requests r
                   LEFT JOIN privacy_case_controls c ON c.request_id=r.id
                   ORDER BY r.submitted_at ASC,r.id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "legal_hold": bool(row["legal_hold"] or 0),
                "retention_hold": bool(row["retention_hold"] or 0),
                "identity_status": row["identity_status"] or "unverified",
                "deletion_execution_kind": row["deletion_execution_kind"] or "",
            }
            for row in rows
        ]


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


@router.get("/cases", include_in_schema=False)
def owner_privacy_cases(request: Request):
    _require_owner(request)
    return JSONResponse(
        {
            "cases": PrivacyCaseStore().list_cases(),
            "automatic_data_action_taken": False,
            "legal_certification": False,
            "grants_esp_role_or_permission": False,
        },
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/cases/{request_id}", include_in_schema=False)
def owner_privacy_case(request_id: str, request: Request):
    _require_owner(request)
    try:
        payload = PrivacyCaseStore().get_case(request_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})


@router.post("/cases/{request_id}/context", include_in_schema=False)
def owner_privacy_case_context(request_id: str, request: Request, payload: CaseContextInput):
    _require_owner(request)
    try:
        result = PrivacyCaseStore().set_context(
            request_id,
            jurisdiction=payload.jurisdiction,
            legal_basis=payload.legal_basis,
            due_at=payload.due_at,
            note=payload.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.post("/cases/{request_id}/identity", include_in_schema=False)
def owner_privacy_case_identity(request_id: str, request: Request, payload: IdentityReviewInput):
    _require_owner(request)
    try:
        return PrivacyCaseStore().set_identity(
            request_id,
            status=payload.status,
            method=payload.method,
            evidence_reference=payload.evidence_reference,
            note=payload.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{request_id}/holds", include_in_schema=False)
def owner_privacy_case_holds(request_id: str, request: Request, payload: HoldInput):
    _require_owner(request)
    try:
        return PrivacyCaseStore().set_holds(
            request_id,
            legal_hold=payload.legal_hold,
            retention_hold=payload.retention_hold,
            reason=payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{request_id}/deletion-execution", include_in_schema=False)
def owner_privacy_deletion_execution(request_id: str, request: Request, payload: DeletionExecutionInput):
    _require_owner(request)
    try:
        return PrivacyCaseStore().record_deletion_execution(
            request_id,
            execution_kind=payload.execution_kind,
            evidence_reference=payload.evidence_reference,
            retained_exception_reference=payload.retained_exception_reference,
            note=payload.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{request_id}/transition", include_in_schema=False)
def owner_privacy_case_transition(request_id: str, request: Request, payload: CaseTransitionInput):
    _require_owner(request)
    try:
        return PrivacyCaseStore().transition(
            request_id,
            status=payload.status,
            fulfilment_reference=payload.fulfilment_reference,
            reason=payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["CASE_STATES", "DELETION_EXECUTION_KINDS", "IDENTITY_STATES", "PrivacyCaseStore", "router"]