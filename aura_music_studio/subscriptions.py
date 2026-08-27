from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .plans import get_plan


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_payment_currency_columns(con: sqlite3.Connection) -> None:
    """Upgrade pre-GBP ledgers in place without deleting the legacy amount_usd column."""
    columns = _columns(con, "subscription_payments")
    if "amount" not in columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN amount TEXT")
    if "amount_minor" not in columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN amount_minor INTEGER")
    if "currency" not in columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN currency TEXT NOT NULL DEFAULT 'GBP'")

    # Historical 4.99/9.99 values were the intended public GBP prices despite the old
    # amount_usd column name. Preserve that column as a compatibility mirror and backfill
    # the canonical currency-aware columns.
    con.execute(
        "UPDATE subscription_payments SET amount=COALESCE(NULLIF(amount,''),amount_usd) WHERE amount IS NULL OR amount=''"
    )
    con.execute(
        """UPDATE subscription_payments
           SET amount_minor=CAST(ROUND(CAST(COALESCE(NULLIF(amount,''),amount_usd,'0') AS REAL)*100) AS INTEGER)
           WHERE amount_minor IS NULL"""
    )
    con.execute("UPDATE subscription_payments SET currency='GBP' WHERE currency IS NULL OR currency=''")


class SubscriptionLedger:
    """Tracks paid Pulsar-Frequency House periods independently of ESP access.

    Free/Basic/Pro is the public creative subscription dimension. ESP Creator/Agent/Both
    is a separate owner-controlled permission and never grants, extends, downgrades or
    otherwise changes a creative subscription. Paid periods therefore fall back to Free
    when they expire unless ownership has separately assigned an owner-comped entitlement.

    The historical ``esp_comped`` billing state is treated as a deprecated compatibility
    value and is normalized to Free when encountered. This prevents legacy data from
    silently reintroducing the retired role→subscription coupling.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.store.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscription_state (
                    user_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    last_payment_reference TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS subscription_payments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    payment_reference TEXT NOT NULL UNIQUE,
                    amount_usd TEXT NOT NULL,
                    amount TEXT,
                    amount_minor INTEGER,
                    currency TEXT NOT NULL DEFAULT 'GBP',
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )
            _ensure_payment_currency_columns(con)

    def get(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM subscription_state WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def verify_payment(self, user_id: str, plan_id: str, payment_reference: str, *, period_days: int = 31) -> dict:
        plan = get_plan(plan_id)
        if plan.id == "free":
            raise ValueError("Free membership does not require a subscription payment")
        reference = (payment_reference or "").strip()
        if len(reference) < 3:
            raise ValueError("A PayPal/payment reference is required")

        user = self.store.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        if user.get("status") not in {"approved_pending_payment", "active"}:
            raise ValueError("Account approval is required before payment can activate a membership")

        now = _now()
        existing = self.get(user_id)
        existing_end = _parse(existing.get("period_end")) if existing else None
        start = existing_end if existing_end and existing_end > now else now
        end = start + timedelta(days=period_days)
        amount = str(plan.monthly_price)

        with self._connect() as con:
            duplicate = con.execute(
                "SELECT id FROM subscription_payments WHERE payment_reference=?", (reference,)
            ).fetchone()
            if duplicate:
                raise ValueError("This payment reference has already been used")

            con.execute(
                """INSERT INTO subscription_payments
                   (id,user_id,plan_id,payment_reference,amount_usd,amount,amount_minor,currency,period_start,period_end,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid4().hex,
                    user_id,
                    plan.id,
                    reference,
                    amount,  # deprecated compatibility mirror
                    amount,
                    plan.monthly_price_minor,
                    plan.currency,
                    _iso(start),
                    _iso(end),
                    _iso(now),
                ),
            )
            con.execute(
                """INSERT INTO subscription_state
                   (user_id,plan_id,status,period_start,period_end,last_payment_reference,updated_at)
                   VALUES (?,?, 'active', ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     plan_id=excluded.plan_id,
                     status='active',
                     period_start=excluded.period_start,
                     period_end=excluded.period_end,
                     last_payment_reference=excluded.last_payment_reference,
                     updated_at=excluded.updated_at""",
                (user_id, plan.id, _iso(start), _iso(end), reference, _iso(now)),
            )
            con.execute(
                "UPDATE users SET status='active', plan_id=?, requested_plan_id=?, billing_status='active' WHERE id=?",
                (plan.id, plan.id, user_id),
            )
        return self.status(user_id)

    def status(self, user_id: str) -> dict:
        user = self.store.get_user(user_id)
        state = self.get(user_id)
        return {"user": user, "subscription": state}

    def enforce(self, user: dict) -> dict:
        """Refresh billing without changing ESP Creator/Agent permissions."""
        if not user:
            return user

        billing_status = user.get("billing_status") or ""
        if billing_status in {"owner_comped", "owner_override"}:
            return user

        # Retired compatibility state. ESP membership no longer carries a creative plan.
        if billing_status == "esp_comped":
            with self._connect() as con:
                con.execute(
                    """UPDATE users SET status='active',plan_id='free',requested_plan_id='free',
                       billing_status='not_required' WHERE id=?""",
                    (user["id"],),
                )
            return self.store.get_user(user["id"]) or user

        if user.get("plan_id") == "free":
            return user

        state = self.get(user["id"])
        now = _now()
        end = _parse(state.get("period_end")) if state else None
        if state and state.get("status") == "active" and end and end > now:
            return user

        # A paid Basic/Pro period has ended. Keep the account usable on the permanent Free
        # creative tier. ESP role state is stored in esp_memberships and is deliberately
        # untouched here.
        with self._connect() as con:
            if state:
                con.execute(
                    "UPDATE subscription_state SET status='past_due', updated_at=? WHERE user_id=?",
                    (_iso(now), user["id"]),
                )
            con.execute(
                """UPDATE users SET status='active',plan_id='free',requested_plan_id='free',
                   billing_status='not_required' WHERE id=?""",
                (user["id"],),
            )
        return self.store.get_user(user["id"]) or user
