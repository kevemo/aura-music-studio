from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .audit import AuditLedger
from .csrf_tokens import CSRF_HEADER, SessionCsrfService
from .membership_api import require_admin
from .stripe_billing import StripeClient, StripeConfig, _clean_base_url, evidence_store
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


def _require_cookie_csrf(request: Request, *, action: str) -> None:
    if _bearer_token(request):
        return
    session_token = request.cookies.get(_COOKIE_NAME) or ""
    supplied = request.headers.get(CSRF_HEADER) or ""
    if not SessionCsrfService(store).verify(session_token, supplied):
        raise HTTPException(403, {
            "message": f"A valid session-bound CSRF token is required to {action}",
            "security_gate": "session_csrf",
            "csrf_token_endpoint": "/auth/csrf-token",
            "csrf_header": CSRF_HEADER,
        })


def _stripe_binding_for_user(user_id: str) -> dict[str, Any] | None:
    with evidence_store._connect() as con:
        row = con.execute("SELECT * FROM stripe_customer_bindings WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def _active_paid_period(user: dict[str, Any]) -> tuple[dict[str, Any], datetime]:
    state = subscriptions.get(str(user.get("id") or ""))
    if user.get("plan_id") == "free" or not state:
        raise HTTPException(409, "An active paid subscription is required")
    try:
        period_end = datetime.fromisoformat(str(state.get("period_end") or ""))
    except ValueError as exc:
        raise HTTPException(409, "The paid subscription period is not valid") from exc
    if period_end <= datetime.now(timezone.utc):
        raise HTTPException(409, "An active paid subscription is required")
    return state, period_end


def _require_locally_cancellable(user: dict[str, Any]) -> None:
    state, _period_end = _active_paid_period(user)
    if state.get("status") not in {"active", "cancel_at_period_end"}:
        raise HTTPException(409, "An active paid subscription is required for cancellation")


def _require_locally_resumable(user: dict[str, Any]) -> None:
    state, _period_end = _active_paid_period(user)
    transition = subscriptions.scheduled_transition(str(user.get("id") or ""))
    cancellation_scheduled = bool(
        user.get("billing_status") == "cancel_at_period_end"
        or state.get("status") == "cancel_at_period_end"
        or (transition and int(transition.get("cancel_at_period_end") or 0))
    )
    if not cancellation_scheduled:
        raise HTTPException(409, "Membership renewal is not currently scheduled for cancellation")


def _provider_subscription_identity(binding: dict[str, Any]) -> tuple[str, str]:
    subscription_id = str(binding.get("stripe_subscription_id") or "")
    customer_id = str(binding.get("stripe_customer_id") or "")
    if not subscription_id.startswith("sub_") or not customer_id.startswith("cus_"):
        raise HTTPException(503, "Stripe subscription binding is not safe for renewal changes")
    return subscription_id, customer_id


def _set_stripe_cancel_at_period_end(binding: dict[str, Any], *, enabled: bool) -> None:
    subscription_id, customer_id = _provider_subscription_identity(binding)
    config = StripeConfig.from_env()
    if not config.secret_key:
        operation = "cancellation" if enabled else "renewal resume"
        raise HTTPException(503, f"Stripe subscription {operation} is not configured")
    try:
        provider = StripeClient(config)._post(
            f"/v1/subscriptions/{subscription_id}",
            {"cancel_at_period_end": "true" if enabled else "false"},
        )
    except RuntimeError as exc:
        operation = "schedule subscription cancellation" if enabled else "resume subscription renewal"
        raise HTTPException(502, f"Stripe could not {operation}") from exc
    if (
        str(provider.get("id") or "") != subscription_id
        or str(provider.get("customer") or "") != customer_id
        or provider.get("cancel_at_period_end") is not enabled
    ):
        operation = "cancellation" if enabled else "renewal resume"
        raise HTTPException(502, f"Stripe did not confirm the bound subscription {operation}")


def _stripe_payment_method_portal(binding: dict[str, Any]) -> str:
    _subscription_id, customer_id = _provider_subscription_identity(binding)
    config = StripeConfig.from_env()
    if not config.secret_key or not config.public_base_url:
        raise HTTPException(503, "Stripe payment-method management is not configured")
    try:
        return_url = f"{_clean_base_url(config.public_base_url)}/dashboard"
    except ValueError as exc:
        raise HTTPException(503, "Stripe payment-method management has an invalid public return URL") from exc
    try:
        provider = StripeClient(config)._post(
            "/v1/billing_portal/sessions",
            {
                "customer": customer_id,
                "return_url": return_url,
                "flow_data[type]": "payment_method_update",
                "flow_data[after_completion][type]": "redirect",
                "flow_data[after_completion][redirect][return_url]": return_url,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(502, "Stripe could not create a payment-method management session") from exc
    if str(provider.get("customer") or "") != customer_id:
        raise HTTPException(502, "Stripe did not confirm the bound customer for payment-method management")
    url = str(provider.get("url") or "")
    if not url.startswith("https://billing.stripe.com/"):
        raise HTTPException(502, "Stripe did not return a trusted Billing Portal URL")
    return url


def _safe_subscription_status(status: dict[str, Any]) -> dict[str, Any]:
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
    _require_cookie_csrf(request, action="cancel membership renewal")
    user_id = str(user["id"])
    _require_locally_cancellable(user)
    binding = _stripe_binding_for_user(user_id)
    provider = "manual_or_non_stripe"
    if binding and binding.get("stripe_subscription_id"):
        _set_stripe_cancel_at_period_end(binding, enabled=True)
        provider = "stripe"
    try:
        status = subscriptions.cancel_at_period_end(user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if provider == "stripe":
        evidence_store.set_binding_status(user_id, "cancel_at_period_end")
    result = _safe_subscription_status(status)
    result.update({
        "cancellation_requested": True,
        "access_removed_immediately": False,
        "renewal_provider": provider,
        "provider_cancellation_confirmed": provider == "stripe",
    })
    return result


@router.post("/membership/subscription/resume")
def resume_membership_subscription(request: Request):
    user = _signed_in_user(request)
    _require_cookie_csrf(request, action="resume membership renewal")
    user_id = str(user["id"])
    _require_locally_resumable(user)
    binding = _stripe_binding_for_user(user_id)
    provider = "manual_or_non_stripe"
    if binding and binding.get("stripe_subscription_id"):
        _set_stripe_cancel_at_period_end(binding, enabled=False)
        provider = "stripe"
    try:
        status = subscriptions.resume_renewal(user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if provider == "stripe":
        evidence_store.set_binding_status(user_id, "active")
    result = _safe_subscription_status(status)
    result.update({
        "renewal_resumed": True,
        "access_changed_immediately": False,
        "renewal_provider": provider,
        "provider_resume_confirmed": provider == "stripe",
    })
    return result


@router.post("/membership/subscription/payment-method")
def membership_subscription_payment_method(request: Request):
    user = _signed_in_user(request)
    _require_cookie_csrf(request, action="manage membership payment method")
    user_id = str(user["id"])
    _active_paid_period(user)
    binding = _stripe_binding_for_user(user_id)
    if not binding:
        raise HTTPException(409, "This membership is not managed by Stripe")
    portal_url = _stripe_payment_method_portal(binding)
    return {
        "provider": "stripe",
        "portal_url": portal_url,
        "flow": "payment_method_update",
        "payment_method_management_only": True,
        "plan_or_billing_period_change_enabled_by_this_endpoint": False,
        "entitlement_changed": False,
        "browser_return_is_payment_proof": False,
        "esp_role_effect": "none",
    }


@router.post("/admin/membership/record-verified-refund")
def record_verified_membership_refund(payload: VerifiedRefundRequest, x_lss_admin_key: str | None = Header(default=None)):
    require_admin(x_lss_admin_key)
    if not store.get_user(payload.user_id):
        raise HTTPException(404, "User not found")
    try:
        status = subscriptions.record_verified_refund(payload.user_id, payload.payment_reference, payload.refund_reference)
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
    result.update({"recorded": True, "refund_outcome": outcome, "provider_evidence_required": True})
    return result


__all__ = [
    "VerifiedRefundRequest",
    "cancel_membership_subscription",
    "membership_subscription_payment_method",
    "membership_subscription_status",
    "record_verified_membership_refund",
    "resume_membership_subscription",
    "router",
]
