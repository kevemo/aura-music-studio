from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .accounts import AccountStore
from .native_products import BillingPeriod
from .plans import get_plan


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MembershipBillingPreferenceStore:
    """Persist the requested/approved creative membership billing period.

    This is deliberately separate from native Aura OS/Aura Sec billing. Legacy creative
    memberships created before explicit period selection resolve to monthly, preserving
    compatibility without allowing an unrecorded annual entitlement to appear implicitly.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.store.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS membership_billing_preferences (
                    user_id TEXT PRIMARY KEY,
                    membership_request_id TEXT NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    billing_period TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'requested',
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(membership_request_id) REFERENCES membership_requests(id) ON DELETE CASCADE
                )"""
            )

    @staticmethod
    def validate(plan_id: str, billing_period: BillingPeriod | str) -> tuple[str, BillingPeriod]:
        plan = get_plan(plan_id)
        try:
            period = BillingPeriod(billing_period)
        except ValueError as exc:
            raise ValueError(f"Unsupported billing period: {billing_period}") from exc
        # Canonical catalogue remains the authority. This rejects Member annual and any
        # other plan/period combination that has no real price.
        plan.price_for(period)
        return plan.id, period

    def record_request(
        self,
        *,
        user_id: str,
        membership_request_id: str,
        plan_id: str,
        billing_period: BillingPeriod | str,
    ) -> dict:
        canonical_plan, period = self.validate(plan_id, billing_period)
        with self._connect() as con:
            con.execute(
                """INSERT INTO membership_billing_preferences
                   (user_id,membership_request_id,plan_id,billing_period,status,created_at,decided_at)
                   VALUES (?,?,?,?, 'requested', ?, NULL)""",
                (user_id, membership_request_id, canonical_plan, period.value, _iso()),
            )
            row = con.execute(
                "SELECT * FROM membership_billing_preferences WHERE user_id=?", (user_id,)
            ).fetchone()
        return dict(row)

    def for_user(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM membership_billing_preferences WHERE user_id=?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def for_request(self, membership_request_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM membership_billing_preferences WHERE membership_request_id=?",
                (membership_request_id,),
            ).fetchone()
        return dict(row) if row else None

    def decide(self, membership_request_id: str, *, approved: bool) -> dict | None:
        status = "approved" if approved else "rejected"
        with self._connect() as con:
            con.execute(
                """UPDATE membership_billing_preferences
                   SET status=?, decided_at=? WHERE membership_request_id=?""",
                (status, _iso(), membership_request_id),
            )
            row = con.execute(
                "SELECT * FROM membership_billing_preferences WHERE membership_request_id=?",
                (membership_request_id,),
            ).fetchone()
        return dict(row) if row else None

    def requested_period_for_user(self, user_id: str, plan_id: str) -> BillingPeriod:
        """Return recorded request period, or monthly for pre-period legacy rows."""
        plan = get_plan(plan_id)
        item = self.for_user(user_id)
        if not item:
            return BillingPeriod.MONTHLY
        if item["plan_id"] != plan.id:
            raise ValueError("Stored billing preference plan does not match the membership plan")
        return BillingPeriod(item["billing_period"])

    def approved_period_for_user(self, user_id: str, plan_id: str) -> BillingPeriod:
        """Return owner-approved period; legacy pre-period memberships are monthly.

        A new record that has not been approved is never silently promoted to annual.
        """
        plan = get_plan(plan_id)
        item = self.for_user(user_id)
        if not item:
            return BillingPeriod.MONTHLY
        if item["plan_id"] != plan.id:
            raise ValueError("Approved billing preference plan does not match the membership plan")
        if item["status"] != "approved":
            raise ValueError("Membership billing period has not been owner-approved")
        period = BillingPeriod(item["billing_period"])
        plan.price_for(period)
        return period


__all__ = ["MembershipBillingPreferenceStore"]
