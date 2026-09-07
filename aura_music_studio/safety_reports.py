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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .owner_auth import owner_authorized
from .owner_identity import owner_actor

member_router = APIRouter(prefix="/member/safety", tags=["Member Safety Reports"])
owner_router = APIRouter(prefix="/owner/safety", tags=["Owner Safety Review"])

REPORT_CATEGORIES = (
    "hate", "harassment", "bullying", "violent_threat", "self_harm", "sexual_content",
    "child_safety", "fraud_scam", "impersonation", "privacy", "copyright_ip",
    "tiktok_live", "other",
)
REPORT_STATES = ("submitted", "triaged", "under_review", "action_recommended", "resolved", "dismissed")
TERMINAL_STATES = {"resolved", "dismissed"}
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


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


class SafetyReportInput(BaseModel):
    category: Literal[
        "hate", "harassment", "bullying", "violent_threat", "self_harm", "sexual_content",
        "child_safety", "fraud_scam", "impersonation", "privacy", "copyright_ip",
        "tiktok_live", "other",
    ]
    target_type: Literal["member", "content", "live", "message", "project", "other"]
    target_reference: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=2000)
    evidence_references: list[str] = Field(default_factory=list, max_length=10)
    immediate_danger: bool = False


class SafetyAppealInput(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    evidence_references: list[str] = Field(default_factory=list, max_length=10)


class OwnerReviewInput(BaseModel):
    status: Literal["triaged", "under_review", "action_recommended", "resolved", "dismissed"]
    reason: str = Field(min_length=1, max_length=2000)
    resolution_reference: str = Field(default="", max_length=160)


class SafetyReportStore:
    """Evidence-led safety workflow with no automatic punishment or privilege mutation."""

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
                CREATE TABLE IF NOT EXISTS safety_reports (
                    id TEXT PRIMARY KEY,
                    reporter_user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_reference TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    immediate_danger INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    resolution_reference TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_safety_reports_reporter
                    ON safety_reports(reporter_user_id, submitted_at DESC);
                CREATE INDEX IF NOT EXISTS idx_safety_reports_status
                    ON safety_reports(status, submitted_at ASC);
                CREATE TABLE IF NOT EXISTS safety_report_events (
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_safety_report_events_report
                    ON safety_report_events(report_id, occurred_at ASC, id ASC);
                CREATE TABLE IF NOT EXISTS safety_report_appeals (
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    appellant_user_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_safety_report_appeals_report
                    ON safety_report_appeals(report_id, submitted_at DESC);
                """
            )

    def _append_event(self, con: sqlite3.Connection, *, report_id: str, actor_type: str, action: str, data: dict) -> None:
        previous = con.execute(
            "SELECT event_hash FROM safety_report_events WHERE report_id=? ORDER BY occurred_at DESC,id DESC LIMIT 1",
            (report_id,),
        ).fetchone()
        previous_hash = str(previous["event_hash"]) if previous else "GENESIS"
        event_id = uuid4().hex
        occurred_at = _iso()
        payload = {
            "id": event_id, "report_id": report_id, "actor_type": actor_type,
            "action": action, "occurred_at": occurred_at, "data": data, "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        con.execute(
            "INSERT INTO safety_report_events(id,report_id,actor_type,action,occurred_at,data_json,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (event_id, report_id, actor_type, action, occurred_at, _canonical_json(data), previous_hash, event_hash),
        )

    @staticmethod
    def _refs(values: list[str]) -> list[str]:
        if len(values) > 10:
            raise ValueError("At most 10 evidence references are allowed")
        return [_validate_ref(value, field_name="Evidence reference") for value in values]

    def submit(self, *, reporter_user_id: str, category: str, target_type: str, target_reference: str,
               detail: str, evidence_references: list[str], immediate_danger: bool) -> dict:
        reporter_user_id = str(reporter_user_id or "").strip()
        if not reporter_user_id:
            raise ValueError("Authenticated member id is required")
        if category not in REPORT_CATEGORIES:
            raise ValueError("Unsupported safety-report category")
        target_reference = _validate_ref(target_reference, field_name="Target reference")
        detail = str(detail or "").strip()
        if not detail or len(detail) > 2000:
            raise ValueError("Safety report detail is required and limited to 2000 characters")
        refs = self._refs(evidence_references)
        now = _iso()
        report_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO safety_reports
                   (id,reporter_user_id,category,target_type,target_reference,detail,evidence_json,immediate_danger,status,submitted_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (report_id, reporter_user_id, category, target_type, target_reference, detail,
                 json.dumps(refs, separators=(",", ":")), int(immediate_danger), "submitted", now, now),
            )
            self._append_event(
                con, report_id=report_id, actor_type="member", action="report_submitted",
                data={"category": category, "target_type": target_type, "immediate_danger": bool(immediate_danger),
                      "automatic_moderation_action_taken": False},
            )
        return self.get_for_member(report_id, reporter_user_id)

    def get_for_member(self, report_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM safety_reports WHERE id=? AND reporter_user_id=?", (report_id, user_id)
            ).fetchone()
            if not row:
                raise KeyError("Safety report not found")
            appeals = con.execute(
                "SELECT id,reason,evidence_json,status,submitted_at,updated_at FROM safety_report_appeals WHERE report_id=? AND appellant_user_id=? ORDER BY submitted_at DESC",
                (report_id, user_id),
            ).fetchall()
        report = dict(row)
        report["evidence_references"] = json.loads(report.pop("evidence_json"))
        report["immediate_danger"] = bool(report["immediate_danger"])
        return {"report": report, "appeals": [{**dict(a), "evidence_references": json.loads(a["evidence_json"])} for a in appeals]}

    def list_for_member(self, user_id: str, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,category,target_type,target_reference,status,immediate_danger,submitted_at,updated_at FROM safety_reports WHERE reporter_user_id=? ORDER BY submitted_at DESC,id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{**dict(row), "immediate_danger": bool(row["immediate_danger"])} for row in rows]

    def appeal(self, *, report_id: str, appellant_user_id: str, reason: str, evidence_references: list[str]) -> dict:
        reason = str(reason or "").strip()
        if not reason or len(reason) > 2000:
            raise ValueError("Appeal reason is required and limited to 2000 characters")
        refs = self._refs(evidence_references)
        with self._connect() as con:
            report = con.execute(
                "SELECT * FROM safety_reports WHERE id=? AND reporter_user_id=?", (report_id, appellant_user_id)
            ).fetchone()
            if not report:
                raise KeyError("Safety report not found")
            if str(report["status"]) not in TERMINAL_STATES:
                raise ValueError("An appeal can be submitted only after a report is resolved or dismissed")
            existing = con.execute(
                "SELECT * FROM safety_report_appeals WHERE report_id=? AND appellant_user_id=? AND status IN ('submitted','under_review') ORDER BY submitted_at DESC LIMIT 1",
                (report_id, appellant_user_id),
            ).fetchone()
            if existing:
                return dict(existing)
            now = _iso()
            appeal_id = uuid4().hex
            con.execute(
                "INSERT INTO safety_report_appeals(id,report_id,appellant_user_id,reason,evidence_json,status,submitted_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (appeal_id, report_id, appellant_user_id, reason, json.dumps(refs, separators=(",", ":")), "submitted", now, now),
            )
            self._append_event(con, report_id=report_id, actor_type="member", action="appeal_submitted",
                               data={"appeal_id": appeal_id, "automatic_moderation_action_taken": False})
            return dict(con.execute("SELECT * FROM safety_report_appeals WHERE id=?", (appeal_id,)).fetchone())

    def owner_list(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,reporter_user_id,category,target_type,target_reference,status,immediate_danger,submitted_at,updated_at FROM safety_reports ORDER BY immediate_danger DESC,submitted_at ASC,id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(row), "immediate_danger": bool(row["immediate_danger"])} for row in rows]

    def owner_review(self, *, report_id: str, status: str, reason: str, resolution_reference: str = "",
                     reviewer_actor: str = "ESP Owner") -> dict:
        if status not in REPORT_STATES or status == "submitted":
            raise ValueError("Unsupported owner review state")
        reason = str(reason or "").strip()
        reviewer_actor = str(reviewer_actor or "ESP Owner").strip() or "ESP Owner"
        if len(reviewer_actor) > 120:
            raise ValueError("Reviewer actor label is too long")
        if not reason:
            raise ValueError("Review reason is required")
        resolution_reference = _validate_ref(resolution_reference, field_name="Resolution reference", required=False)
        with self._connect() as con:
            report = con.execute("SELECT * FROM safety_reports WHERE id=?", (report_id,)).fetchone()
            if not report:
                raise KeyError("Safety report not found")
            current = str(report["status"])
            if current in TERMINAL_STATES and status != current:
                raise ValueError("Terminal report state cannot be changed by this endpoint; use an appeal review workflow")
            if status in TERMINAL_STATES and not resolution_reference:
                raise ValueError("Resolving or dismissing a report requires an opaque resolution evidence reference")
            now = _iso()
            con.execute(
                "UPDATE safety_reports SET status=?,resolution_reference=?,updated_at=? WHERE id=?",
                (status, resolution_reference, now, report_id),
            )
            self._append_event(
                con, report_id=report_id, actor_type="owner", action="owner_review",
                data={"from": current, "to": status, "reason": reason,
                      "resolution_reference": resolution_reference, "reviewer_actor": reviewer_actor,
                      "automatic_moderation_action_taken": False},
            )
        return self.owner_get(report_id)

    def owner_get(self, report_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM safety_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise KeyError("Safety report not found")
            events = con.execute("SELECT * FROM safety_report_events WHERE report_id=? ORDER BY occurred_at ASC,id ASC", (report_id,)).fetchall()
            appeals = con.execute("SELECT * FROM safety_report_appeals WHERE report_id=? ORDER BY submitted_at ASC,id ASC", (report_id,)).fetchall()
        previous_hash = "GENESIS"
        chain_valid = True
        parsed_events = []
        for row_event in events:
            item = dict(row_event)
            data = json.loads(item.pop("data_json"))
            payload = {"id": item["id"], "report_id": item["report_id"], "actor_type": item["actor_type"],
                       "action": item["action"], "occurred_at": item["occurred_at"], "data": data,
                       "previous_hash": item["previous_hash"]}
            expected = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
            if item["previous_hash"] != previous_hash or item["event_hash"] != expected:
                chain_valid = False
            previous_hash = item["event_hash"]
            parsed_events.append({**payload, "event_hash": item["event_hash"]})
        report = dict(row)
        report["evidence_references"] = json.loads(report.pop("evidence_json"))
        report["immediate_danger"] = bool(report["immediate_danger"])
        parsed_appeals = []
        for appeal in appeals:
            value = dict(appeal)
            value["evidence_references"] = json.loads(value.pop("evidence_json"))
            parsed_appeals.append(value)
        return {"report": report, "appeals": parsed_appeals, "events": parsed_events,
                "audit_chain_valid": chain_valid, "automatic_moderation_action_taken": False,
                "grants_esp_role_or_permission": False, "changes_billing_or_membership": False}


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authenticated member session required")
    return str(user_id)


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


@member_router.get("/reports")
def member_safety_reports(request: Request):
    user_id = _member_user_id(request)
    return JSONResponse({"reports": SafetyReportStore().list_for_member(user_id),
                         "grants_esp_role_or_permission": False}, headers={"Cache-Control": "private, no-store"})


@member_router.post("/reports")
def submit_safety_report(request: Request, payload: SafetyReportInput):
    user_id = _member_user_id(request)
    try:
        result = SafetyReportStore().submit(
            reporter_user_id=user_id, category=payload.category, target_type=payload.target_type,
            target_reference=payload.target_reference, detail=payload.detail,
            evidence_references=payload.evidence_references, immediate_danger=payload.immediate_danger,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**result, "automatic_moderation_action_taken": False, "grants_esp_role_or_permission": False,
            "emergency_notice": "If anyone is in immediate danger, contact local emergency services or an appropriate qualified service now." if payload.immediate_danger else None}


@member_router.get("/reports/{report_id}")
def member_safety_report(report_id: str, request: Request):
    user_id = _member_user_id(request)
    try:
        return SafetyReportStore().get_for_member(report_id, user_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@member_router.post("/reports/{report_id}/appeals")
def appeal_safety_report(report_id: str, request: Request, payload: SafetyAppealInput):
    user_id = _member_user_id(request)
    try:
        appeal = SafetyReportStore().appeal(report_id=report_id, appellant_user_id=user_id,
                                            reason=payload.reason, evidence_references=payload.evidence_references)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"appeal": appeal, "automatic_moderation_action_taken": False,
            "grants_esp_role_or_permission": False}


@owner_router.get("/reports", include_in_schema=False)
def owner_safety_reports(request: Request):
    _require_owner(request)
    return JSONResponse({"reports": SafetyReportStore().owner_list(), "automatic_moderation_action_taken": False,
                         "grants_esp_role_or_permission": False}, headers={"Cache-Control": "private, no-store"})


@owner_router.get("/reports/{report_id}", include_in_schema=False)
def owner_safety_report(report_id: str, request: Request):
    _require_owner(request)
    try:
        return JSONResponse(SafetyReportStore().owner_get(report_id), headers={"Cache-Control": "private, no-store"})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@owner_router.post("/reports/{report_id}/review", include_in_schema=False)
def owner_review_safety_report(report_id: str, request: Request, payload: OwnerReviewInput):
    _require_owner(request)
    try:
        return SafetyReportStore().owner_review(
            report_id=report_id,
            status=payload.status,
            reason=payload.reason,
            resolution_reference=payload.resolution_reference,
            reviewer_actor=owner_actor(),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["REPORT_CATEGORIES", "REPORT_STATES", "SafetyReportStore", "member_router", "owner_router"]