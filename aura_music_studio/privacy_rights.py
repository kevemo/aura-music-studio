from __future__ import annotations

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

from .global_compliance import POLICY_VERSION, canonical_locale
from .privacy_fulfilment import PrivacyFulfilmentStore

router = APIRouter(prefix="/member/privacy", tags=["Member Privacy Rights"])

RIGHT_TYPES = (
    "access",
    "portability",
    "correction",
    "deletion",
    "restrict_processing",
    "object_processing",
    "opt_out_sale_sharing",
    "limit_sensitive_use",
    "withdraw_consent",
)

POLICY_REGISTRY = {
    "global_compliance_notice": POLICY_VERSION,
    "community_safety": POLICY_VERSION,
    "ai_transparency": POLICY_VERSION,
    "tiktok_live_safety": POLICY_VERSION,
}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PrivacyRequestInput(BaseModel):
    request_type: Literal[
        "access", "portability", "correction", "deletion", "restrict_processing",
        "object_processing", "opt_out_sale_sharing", "limit_sensitive_use", "withdraw_consent",
    ]
    detail: str = Field(default="", max_length=2000)
    locale: str | None = Field(default=None, max_length=32)


class PolicyDecisionInput(BaseModel):
    policy_key: Literal[
        "global_compliance_notice", "community_safety", "ai_transparency", "tiktok_live_safety",
    ]
    decision: Literal["acknowledged", "declined", "withdrawn"]
    locale: str | None = Field(default=None, max_length=32)


class PrivacyEvidenceStore:
    """Append-only privacy evidence bound to authenticated member identifiers.

    Requests are evidence for review, not commands to delete/export data. Legal and identity
    review remains separate from ordinary application permissions and ESP role state.
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

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS privacy_rights_requests (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    request_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_privacy_rights_user
                    ON privacy_rights_requests(user_id, submitted_at DESC);
                CREATE INDEX IF NOT EXISTS idx_privacy_rights_status
                    ON privacy_rights_requests(status, submitted_at ASC);

                CREATE TABLE IF NOT EXISTS privacy_policy_evidence (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    policy_key TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_privacy_policy_user
                    ON privacy_policy_evidence(user_id, recorded_at DESC);
                """
            )

    def submit_request(self, *, user_id: str, request_type: str, detail: str = "", locale: str = "en-GB") -> dict:
        user_id = str(user_id or "").strip()
        request_type = str(request_type or "").strip()
        detail = str(detail or "").strip()
        locale = canonical_locale(locale)
        if not user_id:
            raise ValueError("Authenticated member id is required")
        if request_type not in RIGHT_TYPES:
            raise ValueError("Unsupported privacy-rights request type")
        if len(detail) > 2000:
            raise ValueError("Privacy request detail is too long")

        with self._connect() as con:
            existing = con.execute(
                """SELECT * FROM privacy_rights_requests
                   WHERE user_id=? AND request_type=? AND status IN ('submitted','under_review')
                   ORDER BY submitted_at DESC LIMIT 1""",
                (user_id, request_type),
            ).fetchone()
            if existing:
                return dict(existing)
            now = _iso()
            row_id = uuid4().hex
            con.execute(
                """INSERT INTO privacy_rights_requests
                   (id,user_id,request_type,status,locale,detail,submitted_at,updated_at,metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (row_id, user_id, request_type, "submitted", locale, detail, now, now,
                 json.dumps({"automatic_action_taken": False}, separators=(",", ":"))),
            )
            row = con.execute("SELECT * FROM privacy_rights_requests WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def record_policy_decision(self, *, user_id: str, policy_key: str, decision: str, locale: str = "en-GB") -> dict:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise ValueError("Authenticated member id is required")
        if policy_key not in POLICY_REGISTRY:
            raise ValueError("Unsupported policy evidence key")
        if decision not in {"acknowledged", "declined", "withdrawn"}:
            raise ValueError("Unsupported policy decision")
        locale = canonical_locale(locale)
        policy_version = POLICY_REGISTRY[policy_key]
        with self._connect() as con:
            latest = con.execute(
                """SELECT * FROM privacy_policy_evidence
                   WHERE user_id=? AND policy_key=? AND policy_version=?
                   ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                (user_id, policy_key, policy_version),
            ).fetchone()
            if latest and latest["decision"] == decision and latest["locale"] == locale:
                return dict(latest)
            row_id = uuid4().hex
            con.execute(
                """INSERT INTO privacy_policy_evidence
                   (id,user_id,policy_key,policy_version,decision,locale,recorded_at,source)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (row_id, user_id, policy_key, policy_version, decision, locale, _iso(), "authenticated_member"),
            )
            row = con.execute("SELECT * FROM privacy_policy_evidence WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def snapshot(self, user_id: str, *, limit: int = 50) -> dict:
        limit = max(1, min(int(limit), 200))
        with self._connect() as con:
            requests = con.execute(
                """SELECT id,request_type,status,locale,detail,submitted_at,updated_at
                   FROM privacy_rights_requests WHERE user_id=?
                   ORDER BY submitted_at DESC,id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            evidence = con.execute(
                """SELECT id,policy_key,policy_version,decision,locale,recorded_at,source
                   FROM privacy_policy_evidence WHERE user_id=?
                   ORDER BY recorded_at DESC,id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return {"requests": [dict(row) for row in requests], "policy_evidence": [dict(row) for row in evidence]}


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authenticated member session required")
    return str(user_id)


@router.get("/rights")
def member_privacy_rights(request: Request):
    user_id = _member_user_id(request)
    return {
        "policy_version": POLICY_VERSION,
        "available_request_types": list(RIGHT_TYPES),
        "policy_registry": dict(POLICY_REGISTRY),
        **PrivacyEvidenceStore().snapshot(user_id),
        "automatic_action_taken": False,
        "grants_esp_role_or_permission": False,
        "notice": "Submitting a request records authenticated evidence for review. Availability and handling timelines depend on applicable law and verified identity; access or portability data becomes available only after the separate owner-reviewed fulfilment workflow completes, and this submission endpoint does not itself delete, export, correct, or disclose account data.",
    }


@router.post("/rights/requests")
def submit_privacy_right(request: Request, payload: PrivacyRequestInput):
    user_id = _member_user_id(request)
    try:
        row = PrivacyEvidenceStore().submit_request(
            user_id=user_id, request_type=payload.request_type, detail=payload.detail,
            locale=payload.locale or "en-GB",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "request": row,
        "automatic_action_taken": False,
        "identity_or_legal_review_may_be_required": True,
        "grants_esp_role_or_permission": False,
    }


@router.get("/rights/requests/{request_id}/fulfilment")
def privacy_right_fulfilment(request_id: str, request: Request):
    user_id = _member_user_id(request)
    try:
        result = PrivacyFulfilmentStore().deliver(request_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return JSONResponse(
        result,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="privacy-{request_id}.json"',
        },
    )


@router.post("/policy-decisions")
def record_policy_decision(request: Request, payload: PolicyDecisionInput):
    user_id = _member_user_id(request)
    try:
        row = PrivacyEvidenceStore().record_policy_decision(
            user_id=user_id, policy_key=payload.policy_key, decision=payload.decision,
            locale=payload.locale or "en-GB",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "evidence": row,
        "grants_esp_role_or_permission": False,
        "contract_or_legal_certification": False,
    }


__all__ = ["POLICY_REGISTRY", "RIGHT_TYPES", "PrivacyEvidenceStore", "router"]
