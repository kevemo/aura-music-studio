from __future__ import annotations

import calendar
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .accounts import AccountStore
from .native_products import BillingPeriod
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


def _billing_period(value: BillingPeriod | str) -> BillingPeriod:
    try:
        return BillingPeriod(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported billing period: {value}") from exc


def _advance_billing_period(start: datetime, period: BillingPeriod | str) -> datetime:
    """Advance by one real calendar billing period, clamping invalid month-end dates."""
    billing_period = _billing_period(period)
    if billing_period is BillingPeriod.MONTHLY:
        year = start.year + (1 if start.month == 12 else 0)
        month = 1 if start.month == 12 else start.month + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return start.replace(year=year, month=month, day=day)

    year = start.year + 1
    day = min(start.day, calendar.monthrange(year, start.month)[1])
    return start.replace(year=year, day=day)


def _ensure_payment_currency_columns(con: sqlite3.Connection) -> None:
    """Upgrade pre-GBP ledgers in place without deleting the legacy amount_usd column."""
    columns = _columns(con, "subscription_payments")
    if "amount" not in columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN amount TEXT")
    if "amount_minor" not in columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN amount_minor INTEGER")
    if "currency" not in columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN currency TEXT NOT NULL DEFAULT 'GBP'")

    con.execute(
        "UPDATE subscription_payments SET amount=COALESCE(NULLIF(amount,''),amount_usd) WHERE amount IS NULL OR amount=''"
    )
    con.execute(
        """UPDATE subscription_payments
           SET amount_minor=CAST(ROUND(CAST(COALESCE(NULLIF(amount,''),amount_usd,'0') AS REAL)*100) AS INTEGER)
           WHERE amount_minor IS NULL"""
    )
    con.execute("UPDATE subscription_payments SET currency='GBP' WHERE currency IS NULL OR currency=''")


def _ensure_billing_period_columns(con: sqlite3.Connection) -> None:
    state_columns = _columns(con, "subscription_state")
    if "billing_period" not in state_columns:
        con.execute("ALTER TABLE subscription_state ADD COLUMN billing_period TEXT NOT NULL DEFAULT 'monthly'")
    payment_columns = _columns(con, "subscription_payments")
    if "billing_period" not in payment_columns:
        con.execute("ALTER TABLE subscription_payments ADD COLUMN billing_period TEXT NOT NULL DEFAULT 'monthly'")
    con.execute("UPDATE subscription_state SET billing_period='monthly' WHERE billing_period IS NULL OR billing_period=''")
    con.execute("UPDATE subscription_payments SET billing_period='monthly' WHERE billing_period IS NULL OR billing_period=''")


class SubscriptionLedger:
    """Authoritative creative-membership payment periods and lifecycle transitions.

    Paid membership remains independent of ESP organisational access. Verified future
    plan/period changes are scheduled rather than granting an entitlement before their
    paid effective date. Browser redirects are never payment or refund evidence.
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
                    billing_period TEXT NOT NULL DEFAULT 'monthly',
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
                    billing_period TEXT NOT NULL DEFAULT 'monthly',
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS subscription_transitions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    target_plan_id TEXT NOT NULL,
                    target_billing_period TEXT NOT NULL,
                    payment_reference TEXT NOT NULL UNIQUE,
                    effective_at TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS subscription_refunds (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    payment_reference TEXT NOT NULL,
                    refund_reference TEXT NOT NULL UNIQUE,
                    outcome TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )
            _ensure_payment_currency_columns(con)
            _ensure_billing_period_columns(con)

    def get(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM subscription_state WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def scheduled_transition(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM subscription_transitions
                   WHERE user_id=? AND status='scheduled'
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def _enforce_pending_approval_contract(self, user: dict, plan_id: str, period: BillingPeriod) -> None:
        if user.get("status") != "approved_pending_payment":
            return
        if user.get("requested_plan_id") != plan_id:
            raise ValueError("Payment plan does not match the owner-approved membership plan")

        from .membership_billing_periods import MembershipBillingPreferenceStore

        approved_period = MembershipBillingPreferenceStore(self.store).approved_period_for_user(
            user["id"], plan_id
        )
        if approved_period is not period:
            raise ValueError("Payment billing period does not match the owner-approved membership period")

    def _insert_payment(
        self,
        con: sqlite3.Connection,
        *,
        user_id: str,
        plan_id: str,
        payment_reference: str,
        amount: str,
        amount_minor: int,
        currency: str,
        billing_period: BillingPeriod,
        period_start: datetime,
        period_end: datetime,
        verified_at: datetime,
    ) -> None:
        duplicate = con.execute(
            "SELECT id FROM subscription_payments WHERE payment_reference=?", (payment_reference,)
        ).fetchone()
        if duplicate:
            raise ValueError("This payment reference has already been used")
        con.execute(
            """INSERT INTO subscription_payments
               (id,user_id,plan_id,payment_reference,amount_usd,amount,amount_minor,currency,billing_period,period_start,period_end,verified_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid4().hex,
                user_id,
                plan_id,
                payment_reference,
                amount,
                amount,
                amount_minor,
                currency,
                billing_period.value,
                _iso(period_start),
                _iso(period_end),
                _iso(verified_at),
            ),
        )

    def verify_payment(
        self,
        user_id: str,
        plan_id: str,
        payment_reference: str,
        *,
        billing_period: BillingPeriod | str = BillingPeriod.MONTHLY,
    ) -> dict:
        """Record verified payment and activate or schedule its exact paid entitlement.

        If a paid term is already active, any additional verified payment is a prepaid future
        term and is scheduled for the existing period end. This includes same-plan renewals,
        making future refunds/cancellation reversible without corrupting the current entitlement.
        """
        plan = get_plan(plan_id)
        if plan.id == "free":
            raise ValueError("Free membership does not require a subscription payment")
        period = _billing_period(billing_period)
        amount_decimal = plan.price_for(period)
        amount_minor = plan.price_minor_for(period)
        reference = (payment_reference or "").strip()
        if len(reference) < 3:
            raise ValueError("A PayPal/payment reference is required")

        user = self.store.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        if user.get("status") not in {"approved_pending_payment", "active"}:
            raise ValueError("Account approval is required before payment can activate a membership")
        self._enforce_pending_approval_contract(user, plan.id, period)

        now = _now()
        existing = self.get(user_id)
        existing_end = _parse(existing.get("period_end")) if existing else None
        active_existing = bool(
            existing
            and existing.get("status") in {"active", "cancel_at_period_end"}
            and existing_end
            and existing_end > now
        )
        start = existing_end if active_existing and existing_end else now
        end = _advance_billing_period(start, period)
        amount = str(amount_decimal)

        with self._connect() as con:
            self._insert_payment(
                con,
                user_id=user_id,
                plan_id=plan.id,
                payment_reference=reference,
                amount=amount,
                amount_minor=amount_minor,
                currency=plan.currency,
                billing_period=period,
                period_start=start,
                period_end=end,
                verified_at=now,
            )

            if active_existing:
                pending = con.execute(
                    "SELECT id FROM subscription_transitions WHERE user_id=? AND status='scheduled'",
                    (user_id,),
                ).fetchone()
                if pending:
                    raise ValueError("A paid subscription transition is already scheduled")
                con.execute(
                    """INSERT INTO subscription_transitions
                       (id,user_id,target_plan_id,target_billing_period,payment_reference,effective_at,period_end,status,cancel_at_period_end,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,'scheduled',0,?,?)""",
                    (
                        uuid4().hex,
                        user_id,
                        plan.id,
                        period.value,
                        reference,
                        _iso(start),
                        _iso(end),
                        _iso(now),
                        _iso(now),
                    ),
                )
            else:
                con.execute(
                    """INSERT INTO subscription_state
                       (user_id,plan_id,status,billing_period,period_start,period_end,last_payment_reference,updated_at)
                       VALUES (?,?, 'active', ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET
                         plan_id=excluded.plan_id,
                         status='active',
                         billing_period=excluded.billing_period,
                         period_start=excluded.period_start,
                         period_end=excluded.period_end,
                         last_payment_reference=excluded.last_payment_reference,
                         updated_at=excluded.updated_at""",
                    (user_id, plan.id, period.value, _iso(start), _iso(end), reference, _iso(now)),
                )
                con.execute(
                    "UPDATE users SET status='active', plan_id=?, requested_plan_id=?, billing_status='active' WHERE id=?",
                    (plan.id, plan.id, user_id),
                )

        return self.status(user_id)

    def cancel_at_period_end(self, user_id: str) -> dict:
        """Stop renewal without removing any entitlement that has already been paid for."""
        user = self.store.get_user(user_id)
        state = self.get(user_id)
        now = _now()
        end = _parse(state.get("period_end")) if state else None
        if not user or not state or user.get("plan_id") == "free" or not end or end <= now:
            raise ValueError("An active paid subscription is required for cancellation")

        with self._connect() as con:
            transition = con.execute(
                """SELECT id FROM subscription_transitions
                   WHERE user_id=? AND status='scheduled'
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
            if transition:
                con.execute(
                    """UPDATE subscription_transitions
                       SET cancel_at_period_end=1, updated_at=? WHERE id=?""",
                    (_iso(now), transition["id"]),
                )
            else:
                con.execute(
                    "UPDATE subscription_state SET status='cancel_at_period_end', updated_at=? WHERE user_id=?",
                    (_iso(now), user_id),
                )
            con.execute(
                "UPDATE users SET billing_status='cancel_at_period_end' WHERE id=?",
                (user_id,),
            )
        return self.status(user_id)

    def resume_renewal(self, user_id: str) -> dict:
        """Reverse a scheduled cancellation while the already-paid term is still active.

        This changes renewal intent only. It does not create a new paid term, extend the current
        period, change the plan, or bypass provider confirmation handled by the API boundary.
        """
        user = self.store.get_user(user_id)
        state = self.get(user_id)
        now = _now()
        end = _parse(state.get("period_end")) if state else None
        transition = self.scheduled_transition(user_id)
        cancellation_scheduled = bool(
            user
            and state
            and user.get("plan_id") != "free"
            and end
            and end > now
            and (
                user.get("billing_status") == "cancel_at_period_end"
                or state.get("status") == "cancel_at_period_end"
                or (transition and int(transition.get("cancel_at_period_end") or 0))
            )
        )
        if not cancellation_scheduled:
            raise ValueError("Membership renewal is not currently scheduled for cancellation")

        with self._connect() as con:
            con.execute(
                """UPDATE subscription_state SET status='active', updated_at=?
                   WHERE user_id=? AND status='cancel_at_period_end'""",
                (_iso(now), user_id),
            )
            con.execute(
                """UPDATE subscription_transitions SET cancel_at_period_end=0, updated_at=?
                   WHERE user_id=? AND status='scheduled'""",
                (_iso(now), user_id),
            )
            con.execute(
                "UPDATE users SET billing_status='active' WHERE id=?",
                (user_id,),
            )
        return self.status(user_id)

    def record_verified_refund(
        self,
        user_id: str,
        payment_reference: str,
        refund_reference: str,
    ) -> dict:
        """Apply a provider/owner-verified refund without over-revoking unrelated paid terms."""
        payment_ref = (payment_reference or "").strip()
        refund_ref = (refund_reference or "").strip()
        if len(payment_ref) < 3 or len(refund_ref) < 3:
            raise ValueError("Verified payment and refund references are required")

        now = _now()
        state = self.get(user_id)
        with self._connect() as con:
            payment = con.execute(
                "SELECT * FROM subscription_payments WHERE user_id=? AND payment_reference=?",
                (user_id, payment_ref),
            ).fetchone()
            if not payment:
                raise ValueError("Verified payment reference was not found for this user")
            duplicate = con.execute(
                "SELECT id FROM subscription_refunds WHERE refund_reference=?", (refund_ref,)
            ).fetchone()
            if duplicate:
                raise ValueError("This refund reference has already been used")

            transition = con.execute(
                """SELECT * FROM subscription_transitions
                   WHERE user_id=? AND payment_reference=? AND status='scheduled'""",
                (user_id, payment_ref),
            ).fetchone()

            if transition:
                con.execute(
                    "UPDATE subscription_transitions SET status='refunded', updated_at=? WHERE id=?",
                    (_iso(now), transition["id"]),
                )
                outcome = "future_transition_refunded_current_term_preserved"
                if int(transition["cancel_at_period_end"] or 0):
                    con.execute(
                        "UPDATE subscription_state SET status='cancel_at_period_end', updated_at=? WHERE user_id=?",
                        (_iso(now), user_id),
                    )
            elif state and state.get("last_payment_reference") == payment_ref:
                con.execute(
                    "UPDATE subscription_state SET status='refunded', updated_at=? WHERE user_id=?",
                    (_iso(now), user_id),
                )
                con.execute(
                    """UPDATE users SET status='active',plan_id='free',requested_plan_id='free',
                       billing_status='not_required' WHERE id=?""",
                    (user_id,),
                )
                outcome = "current_term_refunded_entitlement_revoked"
            else:
                outcome = "historical_payment_refunded_current_entitlement_unchanged"

            con.execute(
                """INSERT INTO subscription_refunds
                   (id,user_id,payment_reference,refund_reference,outcome,verified_at)
                   VALUES (?,?,?,?,?,?)""",
                (uuid4().hex, user_id, payment_ref, refund_ref, outcome, _iso(now)),
            )
        return {**self.status(user_id), "refund_outcome": outcome}

    def status(self, user_id: str) -> dict:
        user = self.store.get_user(user_id)
        state = self.get(user_id)
        return {
            "user": user,
            "subscription": state,
            "scheduled_transition": self.scheduled_transition(user_id),
        }

    def _apply_scheduled_transition(self, user_id: str, transition: dict, now: datetime) -> dict:
        state_status = "cancel_at_period_end" if int(transition.get("cancel_at_period_end") or 0) else "active"
        billing_status = state_status
        with self._connect() as con:
            con.execute(
                """UPDATE subscription_state SET
                   plan_id=?,status=?,billing_period=?,period_start=?,period_end=?,last_payment_reference=?,updated_at=?
                   WHERE user_id=?""",
                (
                    transition["target_plan_id"],
                    state_status,
                    transition["target_billing_period"],
                    transition["effective_at"],
                    transition["period_end"],
                    transition["payment_reference"],
                    _iso(now),
                    user_id,
                ),
            )
            con.execute(
                "UPDATE subscription_transitions SET status='applied', updated_at=? WHERE id=?",
                (_iso(now), transition["id"]),
            )
            con.execute(
                """UPDATE users SET status='active',plan_id=?,requested_plan_id=?,billing_status=?
                   WHERE id=?""",
                (
                    transition["target_plan_id"],
                    transition["target_plan_id"],
                    billing_status,
                    user_id,
                ),
            )
        return self.store.get_user(user_id) or {}

    def enforce(self, user: dict) -> dict:
        if not user:
            return user

        billing_status = user.get("billing_status") or ""
        if billing_status in {"owner_comped", "owner_override"}:
            return user

        if billing_status == "esp_comped":
            with self._connect() as con:
                con.execute(
                    """UPDATE users SET status='active',plan_id='free',requested_plan_id='free',
                       billing_status='not_required' WHERE id=?""",
                    (user["id"],),
                )
            return self.store.get_user(user["id"]) or user

        state = self.get(user["id"])
        now = _now()
        transition = self.scheduled_transition(user["id"])
        effective_at = _parse(transition.get("effective_at")) if transition else None

        if user.get("plan_id") == "free":
            if not transition or not effective_at or effective_at > now:
                return user
            applied = self._apply_scheduled_transition(user["id"], transition, now)
            applied_state = self.get(user["id"])
            applied_end = _parse(applied_state.get("period_end")) if applied_state else None
            if applied_end and applied_end > now:
                return applied or user
            user = applied or user
            state = applied_state
        else:
            end = _parse(state.get("period_end")) if state else None
            if state and state.get("status") in {"active", "cancel_at_period_end"} and end and end > now:
                return user

            if transition and effective_at and effective_at <= now:
                applied = self._apply_scheduled_transition(user["id"], transition, now)
                applied_state = self.get(user["id"])
                applied_end = _parse(applied_state.get("period_end")) if applied_state else None
                if applied_end and applied_end > now:
                    return applied or user
                user = applied or user
                state = applied_state

        with self._connect() as con:
            if state:
                terminal = "canceled" if state.get("status") == "cancel_at_period_end" else "past_due"
                con.execute(
                    "UPDATE subscription_state SET status=?, updated_at=? WHERE user_id=?",
                    (terminal, _iso(now), user["id"]),
                )
            con.execute(
                """UPDATE users SET status='active',plan_id='free',requested_plan_id='free',
                   billing_status='not_required' WHERE id=?""",
                (user["id"],),
            )
        return self.store.get_user(user["id"]) or user


__all__ = ["SubscriptionLedger", "_advance_billing_period"]
