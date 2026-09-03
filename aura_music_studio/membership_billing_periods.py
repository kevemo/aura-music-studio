from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .plans import BILLING_MONTH, BILLING_YEAR, get_plan, normalize_billing_period
from .subscriptions import SubscriptionLedger


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_billing_period_schema(db_path: str | Path) -> None:
    """Add explicit billing-period provenance without invalidating existing monthly rows."""
    con = sqlite3.connect(Path(db_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        for table in ("subscription_state", "subscription_payments", "stripe_customer_bindings"):
            columns = _columns(con, table)
            if "billing_period" not in columns:
                con.execute(
                    f"ALTER TABLE {table} ADD COLUMN billing_period TEXT NOT NULL DEFAULT '{BILLING_MONTH}'"
                )
        con.commit()
    finally:
        con.close()


def period_days(period: str) -> int:
    normalized = normalize_billing_period(period)
    return 365 if normalized == BILLING_YEAR else 31


def bind_stripe_subscription(
    db_path: str | Path,
    *,
    user_id: str,
    customer_id: str,
    subscription_id: str,
    plan_id: str,
    billing_period: str,
    status: str = "active",
) -> None:
    period = normalize_billing_period(billing_period)
    plan = get_plan(plan_id)
    if not plan.supports_billing_period(period):
        raise ValueError(f"{plan.name} does not support {period} billing")
    if not customer_id or not subscription_id:
        raise ValueError("Stripe customer and subscription ids are required")
    ensure_billing_period_schema(db_path)
    now = _iso(datetime.now(timezone.utc))
    con = sqlite3.connect(Path(db_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            """INSERT INTO stripe_customer_bindings
               (user_id,stripe_customer_id,stripe_subscription_id,plan_id,status,updated_at,billing_period)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 stripe_customer_id=excluded.stripe_customer_id,
                 stripe_subscription_id=excluded.stripe_subscription_id,
                 plan_id=excluded.plan_id,
                 status=excluded.status,
                 updated_at=excluded.updated_at,
                 billing_period=excluded.billing_period""",
            (user_id, customer_id, subscription_id, plan.id, status, now, period),
        )
        con.commit()
    finally:
        con.close()


def verify_catalog_payment(
    ledger: SubscriptionLedger,
    user_id: str,
    plan_id: str,
    payment_reference: str,
    *,
    billing_period: str,
) -> dict:
    """Record one provider-verified paid membership period using the canonical catalogue.

    The paid amount and entitlement duration are derived from the same plan + billing-period
    object used by checkout and webhook amount validation. Provider redirects alone never call
    this function.
    """
    period = normalize_billing_period(billing_period)
    plan = get_plan(plan_id)
    if plan.id == "free" or not plan.supports_billing_period(period):
        raise ValueError(f"{plan.name} does not support {period} paid membership billing")
    reference = (payment_reference or "").strip()
    if len(reference) < 3:
        raise ValueError("A provider payment reference is required")

    user = ledger.store.get_user(user_id)
    if not user:
        raise ValueError("User not found")
    if user.get("status") not in {"approved_pending_payment", "active"}:
        raise ValueError("Account approval is required before payment can activate a membership")

    ensure_billing_period_schema(ledger.store.db_path)
    now = datetime.now(timezone.utc)
    existing = ledger.get(user_id)
    existing_end = _parse(existing.get("period_end")) if existing else None
    start = existing_end if existing_end and existing_end > now else now
    end = start + timedelta(days=period_days(period))
    amount = plan.price(period)
    amount_text = str(amount)
    amount_minor = plan.price_minor(period)

    con = sqlite3.connect(ledger.store.db_path)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        duplicate = con.execute(
            "SELECT id FROM subscription_payments WHERE payment_reference=?", (reference,)
        ).fetchone()
        if duplicate:
            raise ValueError("This payment reference has already been used")

        con.execute(
            """INSERT INTO subscription_payments
               (id,user_id,plan_id,payment_reference,amount_usd,amount,amount_minor,currency,period_start,period_end,verified_at,billing_period)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid4().hex,
                user_id,
                plan.id,
                reference,
                amount_text,
                amount_text,
                amount_minor,
                plan.currency,
                _iso(start),
                _iso(end),
                _iso(now),
                period,
            ),
        )
        con.execute(
            """INSERT INTO subscription_state
               (user_id,plan_id,status,period_start,period_end,last_payment_reference,updated_at,billing_period)
               VALUES (?,?, 'active', ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 plan_id=excluded.plan_id,
                 status='active',
                 period_start=excluded.period_start,
                 period_end=excluded.period_end,
                 last_payment_reference=excluded.last_payment_reference,
                 updated_at=excluded.updated_at,
                 billing_period=excluded.billing_period""",
            (user_id, plan.id, _iso(start), _iso(end), reference, _iso(now), period),
        )
        con.execute(
            "UPDATE users SET status='active', plan_id=?, requested_plan_id=?, billing_status='active' WHERE id=?",
            (plan.id, plan.id, user_id),
        )
        con.commit()
    finally:
        con.close()
    return ledger.status(user_id)


__all__ = [
    "bind_stripe_subscription",
    "ensure_billing_period_schema",
    "period_days",
    "verify_catalog_payment",
]
