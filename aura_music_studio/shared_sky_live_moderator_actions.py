from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized
from .shared_sky_live_moderator_permissions import ModeratorPermissionService


router = APIRouter(tags=["Shared Sky Limited LIVE Moderator Actions"])

_DELEGATED_MODERATION_ACTIONS = {"delete_message", "timeout_user", "remove_user"}
_DELEGATED_QA_ACTIONS = {"approve", "reject", "remove"}

# Capture the canonical Chat 4 mutations before the Wave 5 guards are installed. The wrappers
# enforce capability scope only; they do not create a second chat/Q&A moderation authority.
_BASE_MODERATE = live.LiveCommunityStore.moderate
_BASE_CREATE_POLL = live.LiveCommunityStore.create_poll
_BASE_MODERATE_QA = live.LiveCommunityStore.moderate_qa


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_reason(value: str) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if len(clean) < 3:
        raise ValueError("A moderation reason of at least 3 characters is required")
    return clean[:500]


def _authority_kind(store: Any, broadcast_id: str, user_id: str | None, owner: bool = False) -> str:
    if owner:
        return "owner"
    if not user_id:
        return "none"
    broadcast = store._broadcast(broadcast_id)
    if str(broadcast["user_id"]) == user_id:
        return "creator"
    permissions = ModeratorPermissionService(store)
    if permissions.is_enabled(user_id) and permissions.is_live_assigned(broadcast_id, user_id):
        return "moderator"
    return "none"


def _limited_moderate(self: Any, broadcast_id: str, actor_user_id: str | None, owner: bool, body: Any) -> dict:
    kind = _authority_kind(self, broadcast_id, actor_user_id, owner)
    if kind == "none":
        raise PermissionError("Creator, Owner or assigned Moderator permission required")
    if kind == "moderator" and str(body.action) not in _DELEGATED_MODERATION_ACTIONS:
        raise PermissionError(
            "Limited Moderators may delete comments, temporarily timeout/mute users, or remove viewers; "
            "persistent blocks and LIVE configuration remain Creator/Owner controls"
        )
    return _BASE_MODERATE(self, broadcast_id, actor_user_id, owner, body)


def _creator_only_create_poll(self: Any, broadcast_id: str, actor_user_id: str, body: Any) -> dict:
    if _authority_kind(self, broadcast_id, actor_user_id, False) != "creator":
        raise PermissionError("Only the LIVE creator can create viewer polls")
    return _BASE_CREATE_POLL(self, broadcast_id, actor_user_id, body)


def _limited_moderate_qa(self: Any, broadcast_id: str, question_id: str, actor_user_id: str, body: Any) -> dict:
    kind = _authority_kind(self, broadcast_id, actor_user_id, False)
    if kind == "none":
        raise PermissionError("Creator or assigned Moderator permission required")
    if kind == "moderator" and str(body.action) not in _DELEGATED_QA_ACTIONS:
        raise PermissionError(
            "Limited Moderators may approve, reject or remove Q&A submissions; "
            "selecting/marking answered remains a Creator control"
        )
    return _BASE_MODERATE_QA(self, broadcast_id, question_id, actor_user_id, body)


_INSTALLED = False


def install_limited_moderator_actions() -> None:
    global _INSTALLED
    _ensure_schema()
    if _INSTALLED:
        return
    live.LiveCommunityStore.moderate = _limited_moderate
    live.LiveCommunityStore.create_poll = _creator_only_create_poll
    live.LiveCommunityStore.moderate_qa = _limited_moderate_qa
    _INSTALLED = True


class EscalateReportRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)


class FlagStreamRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)
    severity: Literal["review", "urgent"] = "review"


def _ensure_schema() -> None:
    with live.community._connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS shared_sky_report_escalations (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                broadcast_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                reason TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(report_id,actor_user_id,idempotency_key),
                FOREIGN KEY(report_id) REFERENCES shared_sky_reports(id) ON DELETE CASCADE,
                FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shared_sky_report_escalations_live
                ON shared_sky_report_escalations(broadcast_id,created_at DESC);

            CREATE TABLE IF NOT EXISTS shared_sky_stream_review_flags (
                id TEXT PRIMARY KEY,
                broadcast_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'review',
                reason TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'submitted',
                idempotency_key TEXT NOT NULL,
                audit_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(broadcast_id,actor_user_id,idempotency_key),
                FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shared_sky_stream_review_flags_live
                ON shared_sky_stream_review_flags(broadcast_id,state,created_at DESC);
            """
        )


def _request_actor(request: Request) -> tuple[str, bool, str]:
    if owner_session_authorized(request):
        member = getattr(request.state, "member", None)
        return str(getattr(member, "user_id", "") or "") or "owner", True, "owner"
    member = live.require_member(request)
    return str(member.user_id), False, "member"


def _require_queue_authority(broadcast_id: str, request: Request) -> tuple[str, str]:
    user_id, owner, _ = _request_actor(request)
    try:
        kind = _authority_kind(live.community, broadcast_id, user_id, owner)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky LIVE not found") from exc
    if kind == "none":
        raise HTTPException(403, "Creator, Owner or assigned Moderator permission required")
    return user_id, "owner" if owner else kind


def _safe_report(row: Any) -> dict:
    item = dict(row)
    try:
        evidence = json.loads(item.get("evidence_json") or "{}")
    except Exception:
        evidence = {}
    return {
        "id": item["id"],
        "target_user_id": item.get("target_user_id"),
        "message_id": item.get("message_id"),
        "category": item.get("category"),
        "reason": item.get("reason"),
        "state": item.get("state"),
        "created_at": item.get("created_at"),
        "audit_id": item.get("audit_id"),
        "evidence": evidence if isinstance(evidence, dict) else {},
        "reporter_identity_exposed": False,
    }


@router.get("/shared-sky/live/api/watch/{broadcast_id}/moderation-queue")
def moderation_queue(broadcast_id: str, request: Request, limit: int = 100):
    _require_queue_authority(broadcast_id, request)
    maximum = max(1, min(int(limit), 200))
    _ensure_schema()
    with live.community._connect() as con:
        reports = con.execute(
            """SELECT id,target_user_id,message_id,category,reason,state,evidence_json,created_at,audit_id
               FROM shared_sky_reports WHERE broadcast_id=? ORDER BY created_at DESC LIMIT ?""",
            (broadcast_id, maximum),
        ).fetchall()
        flags = con.execute(
            """SELECT id,severity,reason,state,audit_id,created_at
               FROM shared_sky_stream_review_flags WHERE broadcast_id=? ORDER BY created_at DESC LIMIT ?""",
            (broadcast_id, maximum),
        ).fetchall()
    return {
        "broadcast_id": broadcast_id,
        "reports": [_safe_report(row) for row in reports],
        "stream_flags": [dict(row) for row in flags],
        "reporter_identity_exposed": False,
    }


@router.post("/shared-sky/live/api/watch/{broadcast_id}/reports/{report_id}/escalate")
def escalate_report(broadcast_id: str, report_id: str, body: EscalateReportRequest, request: Request):
    user_id, actor_kind = _require_queue_authority(broadcast_id, request)
    reason = _clean_reason(body.reason)
    _ensure_schema()
    now = _now()
    with live.community._connect() as con:
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        report = con.execute(
            "SELECT id FROM shared_sky_reports WHERE id=? AND broadcast_id=?",
            (report_id, broadcast_id),
        ).fetchone()
        if not report:
            con.execute("ROLLBACK")
            raise HTTPException(404, "Shared Sky report not found")
        existing = con.execute(
            """SELECT id,created_at FROM shared_sky_report_escalations
               WHERE report_id=? AND actor_user_id=? AND idempotency_key=?""",
            (report_id, user_id, body.idempotency_key),
        ).fetchone()
        if existing:
            con.execute("COMMIT")
            return {
                "report_id": report_id,
                "state": "escalated",
                "escalation_id": existing["id"],
                "created_at": existing["created_at"],
            }
        escalation_id = uuid4().hex
        con.execute("UPDATE shared_sky_reports SET state='escalated' WHERE id=?", (report_id,))
        con.execute(
            """INSERT INTO shared_sky_report_escalations
               (id,report_id,broadcast_id,actor_user_id,actor_kind,reason,idempotency_key,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (escalation_id, report_id, broadcast_id, user_id, actor_kind, reason, body.idempotency_key, now),
        )
        con.execute("COMMIT")
    live.community.emit(
        broadcast_id,
        None if user_id == "owner" else user_id,
        "moderation.action",
        {"action": "escalate_report", "report_id": report_id},
        idempotency_key=f"report-escalation:{report_id}:{user_id}:{body.idempotency_key}",
        audience="moderators",
    )
    return {"report_id": report_id, "state": "escalated", "escalation_id": escalation_id, "created_at": now}


@router.post("/shared-sky/live/api/watch/{broadcast_id}/flag-stream")
def flag_stream_for_review(broadcast_id: str, body: FlagStreamRequest, request: Request):
    user_id, actor_kind = _require_queue_authority(broadcast_id, request)
    reason = _clean_reason(body.reason)
    _ensure_schema()
    now = _now()
    audit_id = uuid4().hex
    with live.community._connect() as con:
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """SELECT id,state,audit_id,created_at FROM shared_sky_stream_review_flags
               WHERE broadcast_id=? AND actor_user_id=? AND idempotency_key=?""",
            (broadcast_id, user_id, body.idempotency_key),
        ).fetchone()
        if existing:
            con.execute("COMMIT")
            return {
                "flag_id": existing["id"],
                "state": existing["state"],
                "audit_id": existing["audit_id"],
                "created_at": existing["created_at"],
            }
        flag_id = uuid4().hex
        con.execute(
            """INSERT INTO shared_sky_stream_review_flags
               (id,broadcast_id,actor_user_id,actor_kind,severity,reason,state,idempotency_key,audit_id,created_at)
               VALUES(?,?,?,?,?,?,'submitted',?,?,?)""",
            (flag_id, broadcast_id, user_id, actor_kind, body.severity, reason, body.idempotency_key, audit_id, now),
        )
        con.execute("COMMIT")
    live.community.emit(
        broadcast_id,
        None if user_id == "owner" else user_id,
        "moderation.action",
        {"action": "flag_stream_for_review", "flag_id": flag_id, "severity": body.severity},
        idempotency_key=f"stream-review:{broadcast_id}:{user_id}:{body.idempotency_key}",
        audience="moderators",
    )
    return {"flag_id": flag_id, "state": "submitted", "audit_id": audit_id, "created_at": now}


__all__ = [
    "router",
    "install_limited_moderator_actions",
    "_authority_kind",
    "_limited_moderate",
    "_creator_only_create_poll",
    "_limited_moderate_qa",
    "EscalateReportRequest",
    "FlagStreamRequest",
]
