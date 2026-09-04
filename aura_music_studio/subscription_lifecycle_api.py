from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .audit import AuditLedger
from .csrf_tokens import CSRF_HEADER, SessionCsrfService
from .membership_api import require_admin
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


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        value = auth[7:].strip()
        return value or None
    return None


def _session_token(request: Request) -> str | None:
    return _bearer_token(request) or request.cookies.get(_COOKIE_NAME)


def _signed_in_user(request: Request) -> dict[str, Any]:
    user = store.resolve_session(_session_token(request))
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def _require_cookie_csrf(request: Request) -> None:
    """Require session-bound CSRF proof for cookie-authenticated cancellation writes."""
    if _bearer_token(request):
        return
    session_token = request.cookies.get(_COOKIE_NAME) or ""
    supplied = request.headers.get(CSRF_HEADER) or ""
    if not SessionCsrfService(store).verify(session_token, supplied):
        raise HTTPException(
            403,
            {
                "message": "A valid session-bound CSRF token is required to cancel membership",
                "security_gate": "session_csrf",
                "csrf_token_endpoint": "/auth/csrf-token",
                "csrf_header": CSRF_HEADER,
            },
        )


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
    _require_cookie_csrf(request)
    try:
        status = subscriptions.cancel_at_period_end(str(user["id"]))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    result = _safe_subscription_status(status)
    result["cancellation_requested"] = True
    result["access_removed_immediately"] = False
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
