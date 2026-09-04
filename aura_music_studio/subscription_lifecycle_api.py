from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .audit import AuditLedger
from .membership_api import require_admin
from .stripe_billing import StripeClient, StripeConfig, evidence_store
from .subscriptions import SubscriptionLedger

router = APIRouter(tags=["Membership Subscription Lifecycle"])
store = AccountStore()
subscriptions = SubscriptionLedger(store)
audit = AuditLedger(store)
_COOKIE_NAME = "lss_session"


class VerifiedRefundRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    payment_reference: str = Field(min_length=3, max_length=200)
    refund_reference: str = Field(min_length=3, max_length=200)


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(_COOKIE_NAME)


def _signed_in_user(request: Request) -> dict[str, Any]:
    user = store.resolve_session(_session_token(request))
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def _stripe_binding_for_user(user_id: str) -> dict[str, Any] | None:
    """Resolve the server-owned Stripe binding without accepting a provider id from the browser."""
    with evidence_store._connect() as con:
        row = con.execute(
            "SELECT * FROM stripe_customer_bindings WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def _require_locally_cancellable(user: dict[str, Any]) -> None:
    state = subscriptions.get(str(user.get("id") or ""))
    if user.get("plan_id") == "free" or not state:
        raise HTTPException(409, "An active paid subscription is required for cancellation")
    if state.get("status") not in {"active", "cancel_at_period_end"}:
        raise HTTPException(409, "An active paid subscription is required for cancellation")
    try:
        period_end = datetime.fromisoformat(str(state.get("period_end") or ""))
    except ValueError as exc:
        raise HTTPException(409, "The paid subscription period is not valid") from exc
    if period_end <= datetime.now(timezone.utc):
        raise HTTPException(409, "An active paid subscription is required for cancellation")


def _schedule_stripe_cancellation(user_id: str, binding: dict[str, Any]) -> None:
    """Schedule provider cancellation before mutating local renewal state.

    The subscription/customer ids are read only from the server-side binding. Local state is
    left unchanged if Stripe configuration, transport, identity, or response verification fails.
    """
    subscription_id = str(binding.get("stripe_subscription_id") or "")
    customer_id = str(binding.get("stripe_customer_id") or "")
    if not subscription_id.startswith("sub_") or not customer_id.startswith("cus_"):
        raise HTTPException(503, "Stripe subscription binding is not safe for cancellation")

    config = StripeConfig.from_env()
    if not config.secret_key:
        raise HTTPException(503, "Stripe subscription cancellation is not configured")
    try:
        provider = StripeClient(config)._post(
            f"/v1/subscriptions/{subscription_id}",
            {"cancel_at_period_end": "true"},
        )
    except RuntimeError as exc:
        raise HTTPException(502, "Stripe could not schedule subscription cancellation") from exc

    if (
        str(provider.get("id") or "") != subscription_id
        or str(provider.get("customer") or "") != customer_id
        or provider.get("cancel_at_period_end") is not True
    ):
        raise HTTPException(502, "Stripe did not confirm the bound subscription cancellation")


def _safe_subscription_status(status: dict[str, Any]) -> dict[str, Any]:
    """Return lifecycle state without leaking provider/payment references."""
    user = dict(status.get("user") or {})
    state = dict(status.get("subscription") or {})
    transition = dict(status.get("scheduled_transition") or {})

    public_state = None
    if state:
        public_state = {
            "plan_id": state.get("plan_id"),
            "status": state.get("status"),
            "billing_period": state.get("billing_period"),
            "period_start": state.get("period_start"),
            "period_end": state.get("period_end"),
        }

    public_transition = None
    if transition:
        public_transition = {
            "target_plan_id": transition.get("target_plan_id"),
            "target_billing_period": transition.get("target_billing_period"),
            "effective_at": transition.get("effective_at"),
            "period_end": transition.get("period_end"),
            "status": transition.get("status"),
            "cancel_at_period_end": bool(transition.get("cancel_at_period_end")),
        }

    cancellation_scheduled = bool(
        user.get("billing_status") == "cancel_at_period_end"
        or (public_state and public_state.get("status") == "cancel_at_period_end")
        or (public_transition and public_transition.get("cancel_at_period_end"))
    )
    return {
        "user_id": user.get("id"),
        "account_status": user.get("status"),
        "plan_id": user.get("plan_id") or "free",
        "billing_status": user.get("billing_status"),
        "subscription": public_state,
        "scheduled_transition": public_transition,
        "cancel_at_period_end": cancellation_scheduled,
        "browser_redirect_is_payment_or_refund_proof": False,
        "esp_role_effect": "none",
    }


@router.get("/membership/subscription")
def membership_subscription_status(request: Request):
    user = _signed_in_user(request)
    return _safe_subscription_status(subscriptions.status(str(user["id"])))


@router.post("/membership/subscription/cancel")
def cancel_membership_subscription(request: Request):
    user = _signed_in_user(request)
    user_id = str(user["id"])
    _require_locally_cancellable(user)

    binding = _stripe_binding_for_user(user_id)
    provider = "manual_or_non_stripe"
    if binding and binding.get("stripe_subscription_id"):
        _schedule_stripe_cancellation(user_id, binding)
        provider = "stripe"

    try:
        status = subscriptions.cancel_at_period_end(user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    if provider == "stripe":
        evidence_store.set_binding_status(user_id, "cancel_at_period_end")

    result = _safe_subscription_status(status)
    result.update(
        {
            "cancellation_requested": True,
            "access_removed_immediately": False,
            "renewal_provider": provider,
            "provider_cancellation_confirmed": provider == "stripe",
        }
    )
    return result


@router.post("/admin/membership/record-verified-refund")
def record_verified_membership_refund(
    payload: VerifiedRefundRequest,
    x_lss_admin_key: str | None = Header(default=None),
):
    """Record refund evidence only after provider/owner verification has already occurred.

    This endpoint does not treat a browser redirect, client assertion, or unverified provider
    payload as refund evidence. The caller must be ESP-admin-authorized and supply the exact
    verified payment/refund references being reconciled.
    """
    require_admin(x_lss_admin_key)
    if not store.get_user(payload.user_id):
        raise HTTPException(404, "User not found")
    try:
        status = subscriptions.record_verified_refund(
            payload.user_id,
            payload.payment_reference,
            payload.refund_reference,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    outcome = str(status.get("refund_outcome") or "")
    audit.append(
        actor="ESP admin API",
        action="subscription_verified_refund_recorded",
        subject_user_id=payload.user_id,
        details={
            "outcome": outcome,
            "payment_reference_last4": payload.payment_reference[-4:],
            "refund_reference_last4": payload.refund_reference[-4:],
            "provider_evidence_required": True,
            "browser_redirect_is_refund_proof": False,
        },
    )
    result = _safe_subscription_status(status)
    result.update(
        {
            "recorded": True,
            "refund_outcome": outcome,
            "provider_evidence_required": True,
        }
    )
    return result


__all__ = [
    "VerifiedRefundRequest",
    "cancel_membership_subscription",
    "membership_subscription_status",
    "record_verified_membership_refund",
    "router",
]
