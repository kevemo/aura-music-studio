from __future__ import annotations

"""Owner-only, read-only operational summary for Chat 9 domains.

This module deliberately exposes aggregate counts only. It does not grant new mutation
capabilities, does not bypass domain services, and does not create a second Owner dashboard.
The existing Owner Control Center can consume this stable contract as its Chat 9 summary.
"""

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .owner_identity import owner_session_authorized

router = APIRouter(tags=["Owner Chat 9 Operations"])

_ACTIVE_LEAD_STATUSES = (
    "discovered",
    "review",
    "assigned",
    "contacted",
    "replied",
    "interested",
    "follow_up",
    "applied",
    "accepted",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OwnerChat9Operations:
    """Aggregate canonical Chat 9 state without creating or mutating workflow records."""

    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or AccountStore()
        self.db_path = self.accounts.db_path

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    def _has_table(con: sqlite3.Connection, table: str) -> bool:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return bool(row)

    @classmethod
    def _count(
        cls,
        con: sqlite3.Connection,
        table: str,
        where: str = "",
        params: tuple = (),
        *,
        expression: str = "*",
    ) -> int:
        if not cls._has_table(con, table):
            return 0
        sql = f"SELECT COUNT({expression}) AS n FROM {table}"
        if where:
            sql += f" WHERE {where}"
        row = con.execute(sql, params).fetchone()
        return int(row["n"] or 0) if row else 0

    def summary(self) -> dict:
        now = _now()
        active_placeholders = ",".join("?" for _ in _ACTIVE_LEAD_STATUSES)
        with self._connect() as con:
            support = {
                "open_cases": self._count(
                    con,
                    "esp_support_cases",
                    "status NOT IN ('resolved','closed')",
                ),
                "high_or_urgent_open": self._count(
                    con,
                    "esp_support_cases",
                    "status NOT IN ('resolved','closed') AND severity IN ('high','urgent')",
                ),
                "waiting_member": self._count(
                    con,
                    "esp_support_cases",
                    "status='waiting_member'",
                ),
            }
            recruitment = {
                "active_leads": self._count(
                    con,
                    "esp_recruitment_leads",
                    f"status IN ({active_placeholders}) AND do_not_contact=0",
                    _ACTIVE_LEAD_STATUSES,
                ),
                "follow_up": self._count(
                    con,
                    "esp_recruitment_leads",
                    "status='follow_up' AND do_not_contact=0",
                ),
                "activated": self._count(
                    con,
                    "esp_recruitment_leads",
                    "status='activated'",
                ),
                "do_not_contact": self._count(
                    con,
                    "esp_recruitment_leads",
                    "do_not_contact=1 OR status='do_not_contact'",
                ),
            }
            training = {
                "published_courses": self._count(
                    con,
                    "esp_training_course_versions_v2",
                    "status='published'",
                    expression="DISTINCT course_id",
                ),
                "certificates": self._count(con, "esp_training_certificates_v2"),
                "failed_attempts": self._count(
                    con,
                    "esp_training_exam_attempts_v2",
                    "passed=0",
                ),
            }
            announcements = {
                "active_published": self._count(
                    con,
                    "esp_announcements",
                    "status='published' AND (publish_at IS NULL OR publish_at<=?) AND (expires_at IS NULL OR expires_at>?)",
                    (now, now),
                ),
                "scheduled": self._count(
                    con,
                    "esp_announcements",
                    "status='scheduled'",
                ),
                "ack_required_active": self._count(
                    con,
                    "esp_announcements",
                    "status='published' AND acknowledgement_required=1 AND (publish_at IS NULL OR publish_at<=?) AND (expires_at IS NULL OR expires_at>?)",
                    (now, now),
                ),
                "acknowledgements_recorded": self._count(
                    con,
                    "esp_announcement_acknowledgements",
                ),
            }
            evidence = {
                "draft_batches": self._count(
                    con,
                    "esp_creator_evidence_batches",
                    "status='draft'",
                ),
                "metrics_needing_review": self._count(
                    con,
                    "esp_creator_evidence_metrics",
                    "needs_review=1",
                ),
            }

        return {
            "generated_at": now,
            "read_only": True,
            "support": support,
            "recruitment": recruitment,
            "training": training,
            "announcements": announcements,
            "evidence": evidence,
        }


operations = OwnerChat9Operations()


@router.get("/owner/api/chat9-operations")
def owner_chat9_operations(request: Request):
    if not owner_session_authorized(request):
        raise HTTPException(403, "Owner session required")
    return operations.summary()


__all__ = ["OwnerChat9Operations", "router"]
