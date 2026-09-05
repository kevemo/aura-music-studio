from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .esp_support_casework import (
    CaseAssignmentCreate,
    CaseEscalationEventCreate,
    CaseMessageCreate,
    SupportCaseworkStore,
)

_INSTALLED = False
_ORIGINAL_ADD_MESSAGE = SupportCaseworkStore.add_message
_ORIGINAL_ASSIGN = SupportCaseworkStore.assign
_ORIGINAL_ESCALATE = SupportCaseworkStore.record_escalation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_agent_workflow_schema(store: SupportCaseworkStore) -> None:
    """Create only the shared current-state table if the Agent escalation module has not yet done so."""
    with store._connect() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS esp_support_case_workflow (
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
            )"""
        )


def _sync_creator_reply_sla(store: SupportCaseworkStore, case_id: str, actor_user_id: str, message_id: str) -> None:
    """Count a staff Creator-visible casework reply as an SLA substantive response exactly once."""
    now = _now()
    marker = f"casework-message:{message_id}"
    with store._connect() as con:
        # The SLA module is optional for legacy cases. If its table is not present, there is
        # nothing to synchronize and the canonical casework reply remains valid.
        table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='esp_support_service_meta'"
        ).fetchone()
        if not table:
            return
        meta = con.execute("SELECT * FROM esp_support_service_meta WHERE case_id=?", (case_id,)).fetchone()
        if not meta:
            return
        exists = con.execute(
            """SELECT 1 FROM esp_support_service_touches
               WHERE case_id=? AND kind='casework_creator_reply' AND note=?""",
            (case_id, marker),
        ).fetchone()
        if exists:
            return
        con.execute(
            """INSERT INTO esp_support_service_touches
               (id,case_id,actor,kind,note,substantive_human_response,creator_visible,created_at)
               VALUES (?,?,?,?,?,1,1,?)""",
            (uuid4().hex, case_id, actor_user_id[:160], "casework_creator_reply", marker, now),
        )
        con.execute(
            """UPDATE esp_support_service_meta
               SET acknowledged_at=COALESCE(acknowledged_at,?),
                   first_substantive_response_at=COALESCE(first_substantive_response_at,?),
                   last_creator_update_at=?,updated_at=?
               WHERE case_id=?""",
            (now, now, now, now, case_id),
        )


def _sync_assignment(store: SupportCaseworkStore, case_id: str, assignee_user_id: str) -> None:
    _ensure_agent_workflow_schema(store)
    now = _now()
    with store._connect() as con:
        con.execute(
            """INSERT INTO esp_support_case_workflow
               (case_id,lead_agent_user_id,escalation_target,escalation_reason,target_response_at,claimed_at,escalated_at,updated_at)
               VALUES (?,?,NULL,'',NULL,?,NULL,?)
               ON CONFLICT(case_id) DO UPDATE SET
                 lead_agent_user_id=excluded.lead_agent_user_id,
                 claimed_at=COALESCE(esp_support_case_workflow.claimed_at,excluded.claimed_at),
                 updated_at=excluded.updated_at""",
            (case_id, assignee_user_id, now, now),
        )


def _sync_escalation(store: SupportCaseworkStore, case_id: str, target: str, reason: str) -> None:
    _ensure_agent_workflow_schema(store)
    now = _now()
    with store._connect() as con:
        con.execute(
            """INSERT INTO esp_support_case_workflow
               (case_id,lead_agent_user_id,escalation_target,escalation_reason,target_response_at,claimed_at,escalated_at,updated_at)
               VALUES (?,NULL,?,?,NULL,NULL,?,?)
               ON CONFLICT(case_id) DO UPDATE SET
                 escalation_target=excluded.escalation_target,
                 escalation_reason=excluded.escalation_reason,
                 escalated_at=excluded.escalated_at,
                 updated_at=excluded.updated_at""",
            (case_id, target[:80], reason[:3000], now, now),
        )


def install_support_casework_integration() -> None:
    """Bridge Chat 9 casework into the existing SLA and Agent current-state models."""
    global _INSTALLED
    if _INSTALLED:
        return

    def integrated_add_message(
        self: SupportCaseworkStore,
        case_id: str,
        actor_user_id: str,
        body: CaseMessageCreate,
    ) -> dict:
        _case, actor_role = self.authorize(actor_user_id, case_id, staff_only=body.visibility == "internal")
        result = _ORIGINAL_ADD_MESSAGE(self, case_id, actor_user_id, body)
        if body.visibility == "creator" and actor_role in {"agent", "owner"}:
            _sync_creator_reply_sla(self, case_id, actor_user_id, result["message_id"])
        return result

    def integrated_assign(
        self: SupportCaseworkStore,
        case_id: str,
        actor_user_id: str,
        body: CaseAssignmentCreate,
    ) -> dict:
        result = _ORIGINAL_ASSIGN(self, case_id, actor_user_id, body)
        _sync_assignment(self, case_id, result["assignee_user_id"])
        return result

    def integrated_escalation(
        self: SupportCaseworkStore,
        case_id: str,
        actor_user_id: str,
        body: CaseEscalationEventCreate,
    ) -> dict:
        result = _ORIGINAL_ESCALATE(self, case_id, actor_user_id, body)
        _sync_escalation(self, case_id, result["target"], body.reason)
        return result

    SupportCaseworkStore.add_message = integrated_add_message
    SupportCaseworkStore.assign = integrated_assign
    SupportCaseworkStore.record_escalation = integrated_escalation
    _INSTALLED = True


__all__ = ["install_support_casework_integration"]
