from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .native_products import BillingPeriod
from .plans import get_plan
from .stripe_billing import StripeClient, StripeConfig, accounts, evidence_store, subscriptions
from .subscription_lifecycle_api import _require_cookie_csrf, _signed_in_user, _stripe_binding_for_user

router = APIRouter(tags=["Membership Subscription Plan Changes"])
audit = AuditLedger(accounts)


class MembershipPlanChangeRequest(BaseModel):
    target_plan_id: str = Field(pattern="^(base|pro)$")


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


def _provider_time(value: Any) -> datetime:
    try:
        stamp = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Stripe subscription schedule did not return a valid phase timestamp") from exc
    if stamp <= 0:
        raise ValueError("Stripe subscription schedule phase timestamp is invalid")
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


def _phase_price_id(phase: dict[str, Any]) -> str:
    items = phase.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return ""
    price = items[0].get("price")
    if isinstance(price, str):
        return price
    if isinstance(price, dict):
        return str(price.get("id") or "")
    return ""


class StripeMembershipPlanChangeStore:
    """Durable provider plan-change intent without granting entitlement before payment.

    A scheduled row means Stripe confirmed a future price schedule. It is deliberately
    separate from ``subscription_transitions``: those rows represent an already verified
    paid future term, while this table represents only future provider billing intent.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or accounts.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS stripe_membership_plan_changes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    stripe_customer_id TEXT NOT NULL,
                    stripe_subscription_id TEXT NOT NULL,
                    stripe_schedule_id TEXT NOT NULL UNIQUE,
                    current_plan_id TEXT NOT NULL,
                    current_billing_period TEXT NOT NULL,
                    target_plan_id TEXT NOT NULL,
                    target_billing_period TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    applied_payment_reference TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_membership_plan_change_user
                    ON stripe_membership_plan_changes(user_id,status,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_stripe_membership_plan_change_subscription
                    ON stripe_membership_plan_changes(stripe_subscription_id,status);
                """
            )

    def _row(self, user_id: str, *, status: str = "scheduled") -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM stripe_membership_plan_changes
                   WHERE user_id=? AND status=? ORDER BY created_at DESC LIMIT 1""",
                (user_id, status),
            ).fetchone()
        return dict(row) if row else None

    def _reconcile_verified_renewal(self, row: dict[str, Any]) -> dict[str, Any]:
        """Mark a provider schedule applied only after the authoritative ledger has payment proof."""
        user_id = str(row.get("user_id") or "")
        target_plan_id = str(row.get("target_plan_id") or "")
        transition = subscriptions.scheduled_transition(user_id)
        payment_reference = ""
        if transition and str(transition.get("target_plan_id") or "") == target_plan_id:
            candidate = str(transition.get("payment_reference") or "")
            if candidate.startswith("stripe:invoice:"):
                payment_reference = candidate
        if not payment_reference:
            state = subscriptions.get(user_id) or {}
            if str(state.get("plan_id") or "") == target_plan_id:
                candidate = str(state.get("last_payment_reference") or "")
                if candidate.startswith("stripe:invoice:"):
                    payment_reference = candidate
        if not payment_reference:
            return row

        with self._connect() as con:
            con.execute(
                """UPDATE stripe_membership_plan_changes
                   SET status='applied',applied_payment_reference=?,updated_at=?
                   WHERE id=? AND status='scheduled'""",
                (payment_reference, _iso(), row["id"]),
            )
            updated = con.execute("SELECT * FROM stripe_membership_plan_changes WHERE id=?", (row["id"],)).fetchone()
        return dict(updated) if updated else row

    def pending_for_user(self, user_id: str) -> dict[str, Any] | None:
        row = self._row(user_id)
        if not row:
            return None
        reconciled = self._reconcile_verified_renewal(row)
        return reconciled if reconciled.get("status") == "scheduled" else None

    def record_scheduled(
        self,
        *,
        user_id: str,
        customer_id: str,
        subscription_id: str,
        schedule_id: str,
        current_plan_id: str,
        target_plan_id: str,
        effective_at: datetime,
    ) -> dict[str, Any]:
        now = _iso()
        with self._connect() as con:
            existing = con.execute(
                "SELECT id FROM stripe_membership_plan_changes WHERE user_id=? AND status='scheduled'",
                (user_id,),
            ).fetchone()
            if existing:
                raise ValueError("A Stripe membership plan change is already scheduled")
            row_id = uuid4().hex
            con.execute(
                """INSERT INTO stripe_membership_plan_changes
                   (id,user_id,stripe_customer_id,stripe_subscription_id,stripe_schedule_id,
                    current_plan_id,current_billing_period,target_plan_id,target_billing_period,
                    effective_at,status,applied_payment_reference,created_at,updated_at)
                   VALUES (?,?,?,?,?,?, 'monthly',?, 'monthly',?,'scheduled',NULL,?,?)""",
                (
                    row_id,
                    user_id,
                    customer_id,
                    subscription_id,
                    schedule_id,
                    current_plan_id,
                    target_plan_id,
                    _iso(effective_at),
                    now,
                    now,
                ),
            )
            row = con.execute("SELECT * FROM stripe_membership_plan_changes WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else {}

    def mark_cancelled(self, user_id: str, schedule_id: str) -> None:
        with self._connect() as con:
            changed = con.execute(
                """UPDATE stripe_membership_plan_changes
                   SET status='cancelled',updated_at=?
                   WHERE user_id=? AND stripe_schedule_id=? AND status='scheduled'""",
                (_iso(), user_id, schedule_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Scheduled Stripe membership plan change was not found")


plan_changes = StripeMembershipPlanChangeStore()


def _release_schedule(client: StripeClient, schedule_id: str) -> bool:
    try:
        released = client._post(f"/v1/subscription_schedules/{schedule_id}/release", {})
    except RuntimeError:
        return False
    return str(released.get("id") or "") == schedule_id and str(released.get("status") or "") == "released"


def _require_monthly_active_change(user: dict[str, Any], target_plan_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    user_id = str(user.get("id") or "")
    current_plan_id = str(user.get("plan_id") or "")
    if user.get("status") != "active" or current_plan_id not in {"base", "pro"}:
        raise HTTPException(409, "An active paid Basic or Unlimited Pro membership is required")
    if target_plan_id == current_plan_id:
        raise HTTPException(409, "The requested membership plan is already active")

    state = subscriptions.get(user_id) or {}
    if state.get("status") != "active":
        if state.get("status") == "cancel_at_period_end":
            raise HTTPException(409, "Remove the scheduled cancellation before changing membership plan")
        raise HTTPException(409, "An active paid subscription term is required")
    try:
        current_period = BillingPeriod(str(state.get("billing_period") or ""))
    except ValueError as exc:
        raise HTTPException(409, "The current billing period is not valid") from exc
    if current_period is not BillingPeriod.MONTHLY:
        raise HTTPException(
            409,
            "Monthly/yearly interval changes require the dedicated Stripe interval-change flow and are not enabled here",
        )
    if subscriptions.scheduled_transition(user_id):
        raise HTTPException(409, "A verified paid subscription transition is already scheduled")
    if plan_changes.pending_for_user(user_id):
        raise HTTPException(409, "A Stripe membership plan change is already scheduled")

    target = get_plan(target_plan_id)
    target.price_for(BillingPeriod.MONTHLY)
    binding = _stripe_binding_for_user(user_id)
    if not binding:
        raise HTTPException(409, "This membership has no Stripe subscription binding")
    if str(binding.get("status") or "") not in {"active", "payment_failed"}:
        raise HTTPException(409, "The Stripe subscription binding is not active")
    if str(binding.get("plan_id") or "") != current_plan_id:
        raise HTTPException(409, "Stripe binding plan does not match the active membership")
    subscription_id = str(binding.get("stripe_subscription_id") or "")
    customer_id = str(binding.get("stripe_customer_id") or "")
    if not subscription_id.startswith("sub_") or not customer_id.startswith("cus_"):
        raise HTTPException(409, "Stripe subscription binding is not safe for a plan change")
    return state, binding


def _schedule_provider_plan_change(
    *,
    user: dict[str, Any],
    target_plan_id: str,
    state: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    config = StripeConfig.from_env()
    if not config.secret_key:
        raise HTTPException(503, "Stripe membership plan changes are not configured")
    current_plan_id = str(user["plan_id"])
    subscription_id = str(binding["stripe_subscription_id"])
    customer_id = str(binding["stripe_customer_id"])
    try:
        current_price_id = config.price_id(current_plan_id)
        target_price_id = config.price_id(target_plan_id)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc

    client = StripeClient(config)
    try:
        created = client._post("/v1/subscription_schedules", {"from_subscription": subscription_id})
    except RuntimeError as exc:
        raise HTTPException(502, "Stripe could not create a subscription schedule") from exc

    schedule_id = str(created.get("id") or "")
    if not schedule_id.startswith("sub_sched_") or str(created.get("subscription") or "") != subscription_id:
        raise HTTPException(502, "Stripe returned an invalid subscription schedule")
    current_phase = created.get("current_phase")
    if not isinstance(current_phase, dict):
        _release_schedule(client, schedule_id)
        raise HTTPException(502, "Stripe did not return the current subscription phase")
    try:
        phase_start = _provider_time(current_phase.get("start_date"))
        phase_end = _provider_time(current_phase.get("end_date"))
    except ValueError as exc:
        _release_schedule(client, schedule_id)
        raise HTTPException(502, str(exc)) from exc
    if phase_end <= datetime.now(timezone.utc) or phase_end <= phase_start:
        _release_schedule(client, schedule_id)
        raise HTTPException(502, "Stripe returned an invalid current subscription phase")

    payload = {
        "end_behavior": "release",
        "phases[0][start_date]": str(int(phase_start.timestamp())),
        "phases[0][end_date]": str(int(phase_end.timestamp())),
        "phases[0][items][0][price]": current_price_id,
        "phases[0][items][0][quantity]": "1",
        "phases[0][proration_behavior]": "none",
        "phases[1][start_date]": str(int(phase_end.timestamp())),
        "phases[1][items][0][price]": target_price_id,
        "phases[1][items][0][quantity]": "1",
        "phases[1][iterations]": "1",
        "phases[1][proration_behavior]": "none",
        "metadata[user_id]": str(user["id"]),
        "metadata[current_plan_id]": current_plan_id,
        "metadata[target_plan_id]": target_plan_id,
        "metadata[billing_period]": BillingPeriod.MONTHLY.value,
        "metadata[purchase_kind]": "membership_plan_change",
    }
    try:
        updated = client._post(f"/v1/subscription_schedules/{schedule_id}", payload)
    except RuntimeError as exc:
        _release_schedule(client, schedule_id)
        raise HTTPException(502, "Stripe could not schedule the membership plan change") from exc

    phases = updated.get("phases")
    target_phase = phases[-1] if isinstance(phases, list) and phases and isinstance(phases[-1], dict) else {}
    if (
        str(updated.get("id") or "") != schedule_id
        or str(updated.get("subscription") or "") != subscription_id
        or _phase_price_id(target_phase) != target_price_id
    ):
        _release_schedule(client, schedule_id)
        raise HTTPException(502, "Stripe did not confirm the requested future membership price")

    try:
        pending = plan_changes.record_scheduled(
            user_id=str(user["id"]),
            customer_id=customer_id,
            subscription_id=subscription_id,
            schedule_id=schedule_id,
            current_plan_id=current_plan_id,
            target_plan_id=target_plan_id,
            effective_at=phase_end,
        )
        # The Stripe binding describes which canonical plan the provider will bill at the next
        # subscription-cycle invoice. Local creative entitlement remains on the current plan until
        # that signed paid invoice reaches SubscriptionLedger.verify_payment().
        evidence_store.bind_subscription(
            str(user["id"]), customer_id, subscription_id, target_plan_id, "plan_change_scheduled"
        )
    except Exception as exc:
        _release_schedule(client, schedule_id)
        try:
            plan_changes.mark_cancelled(str(user["id"]), schedule_id)
        except ValueError:
            pass
        evidence_store.bind_subscription(str(user["id"]), customer_id, subscription_id, current_plan_id, "active")
        raise HTTPException(500, "The Stripe plan change could not be recorded safely") from exc

    audit.append(
        actor=f"member:{user['id']}",
        action="stripe_membership_plan_change_scheduled",
        subject_user_id=str(user["id"]),
        details={
            "current_plan_id": current_plan_id,
            "target_plan_id": target_plan_id,
            "billing_period": BillingPeriod.MONTHLY.value,
            "effective_at": pending.get("effective_at"),
            "provider_schedule_id": schedule_id,
            "entitlement_changed_immediately": False,
        },
    )
    return pending


@router.get("/membership/subscription/change")
def membership_plan_change_status(request: Request):
    user = _signed_in_user(request)
    pending = plan_changes.pending_for_user(str(user["id"]))
    return {
        "scheduled": bool(pending),
        "current_plan_id": user.get("plan_id"),
        "target_plan_id": pending.get("target_plan_id") if pending else None,
        "billing_period": pending.get("target_billing_period") if pending else None,
        "effective_at": pending.get("effective_at") if pending else None,
        "entitlement_changed_immediately": False,
        "activation_source": "verified_paid_stripe_renewal_invoice" if pending else None,
        "esp_role_effect": "none",
    }


@router.post("/membership/subscription/change")
def schedule_membership_plan_change(body: MembershipPlanChangeRequest, request: Request):
    user = _signed_in_user(request)
    _require_cookie_csrf(request)
    state, binding = _require_monthly_active_change(user, body.target_plan_id)
    pending = _schedule_provider_plan_change(
        user=user,
        target_plan_id=body.target_plan_id,
        state=state,
        binding=binding,
    )
    target = get_plan(body.target_plan_id)
    return {
        "scheduled": True,
        "provider": "stripe",
        "current_plan_id": user.get("plan_id"),
        "target_plan_id": target.id,
        "target_plan_name": target.name,
        "billing_period": BillingPeriod.MONTHLY.value,
        "effective_at": pending.get("effective_at"),
        "entitlement_changed_immediately": False,
        "activation_source": "verified_paid_stripe_renewal_invoice",
        "browser_redirect_is_payment_proof": False,
        "esp_role_effect": "none",
    }


@router.post("/membership/subscription/change/cancel")
def cancel_membership_plan_change(request: Request):
    user = _signed_in_user(request)
    _require_cookie_csrf(request)
    user_id = str(user["id"])
    pending = plan_changes.pending_for_user(user_id)
    if not pending:
        raise HTTPException(409, "No Stripe membership plan change is scheduled")
    config = StripeConfig.from_env()
    if not config.secret_key:
        raise HTTPException(503, "Stripe membership plan changes are not configured")
    schedule_id = str(pending["stripe_schedule_id"])
    if not _release_schedule(StripeClient(config), schedule_id):
        raise HTTPException(502, "Stripe did not confirm release of the membership plan change schedule")
    try:
        plan_changes.mark_cancelled(user_id, schedule_id)
        evidence_store.bind_subscription(
            user_id,
            str(pending["stripe_customer_id"]),
            str(pending["stripe_subscription_id"]),
            str(pending["current_plan_id"]),
            "active",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.append(
        actor=f"member:{user_id}",
        action="stripe_membership_plan_change_cancelled",
        subject_user_id=user_id,
        details={
            "current_plan_id": pending.get("current_plan_id"),
            "target_plan_id": pending.get("target_plan_id"),
            "provider_schedule_id": schedule_id,
            "entitlement_changed": False,
        },
    )
    return {
        "cancelled": True,
        "provider": "stripe",
        "current_plan_id": user.get("plan_id"),
        "entitlement_changed": False,
        "esp_role_effect": "none",
    }


__all__ = [
    "MembershipPlanChangeRequest",
    "StripeMembershipPlanChangeStore",
    "cancel_membership_plan_change",
    "membership_plan_change_status",
    "plan_changes",
    "router",
    "schedule_membership_plan_change",
]
