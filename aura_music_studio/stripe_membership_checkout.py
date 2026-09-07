from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .membership_billing_periods import MembershipBillingPreferenceStore
from .native_products import BillingPeriod
from .plans import get_plan
from .stripe_billing import (
    StripeClient,
    StripeConfig,
    _clean_base_url,
    _session_user,
    _validate_checkout_user,
    accounts,
    credit_packs,
)
from .subscription_lifecycle_api import router as subscription_lifecycle_router

router = APIRouter(tags=["Stripe Membership Checkout"])
billing_preferences = MembershipBillingPreferenceStore(accounts)


class MembershipSubscriptionCheckoutRequest(BaseModel):
    plan_id: str = Field(pattern="^(base|pro)$")
    billing_period: BillingPeriod = BillingPeriod.MONTHLY


def _annual_price_env(plan_id: str) -> str:
    if plan_id == "pro":
        return "STRIPE_PRO_ANNUAL_PRICE_ID"
    raise ValueError("Annual Stripe membership checkout is available for Unlimited Pro only")


def subscription_price_id(
    config: StripeConfig,
    plan_id: str,
    billing_period: BillingPeriod | str,
) -> str:
    """Resolve a configured Stripe Price ID only after canonical price validation."""
    period = BillingPeriod(billing_period)
    plan = get_plan(plan_id)
    if plan.id == "free":
        raise ValueError("Free membership does not use Stripe subscription checkout")
    # Canonical catalogue validation deliberately rejects unsupported periods, including
    # Basic annual billing. Provider configuration can never create a new public tier/period.
    plan.price_for(period)
    if period is BillingPeriod.MONTHLY:
        return config.price_id(plan.id)
    env_name = _annual_price_env(plan.id)
    value = (os.getenv(env_name) or "").strip()
    if not value:
        raise ValueError(f"Stripe annual price id is not configured for {plan.name}")
    return value


def approved_checkout_period(user: dict[str, Any], plan_id: str, requested_period: BillingPeriod | str) -> BillingPeriod:
    """Bind a paid Checkout Session to a safe, explicit membership contract.

    Pending members may buy only the exact plan and period that ownership approved. An already
    active Basic/Pro account may not create a second Stripe subscription as an implicit upgrade,
    downgrade or billing-period change. Those operations require a dedicated provider-backed
    lifecycle flow so an existing paid period cannot be silently replaced or double-billed.
    """
    period = BillingPeriod(requested_period)
    plan = get_plan(plan_id)
    plan.price_for(period)
    status = str(user.get("status") or "")
    if status == "approved_pending_payment":
        approved = billing_preferences.approved_period_for_user(str(user.get("id") or ""), plan.id)
        if approved is not period:
            raise ValueError("Checkout billing period does not match the owner-approved membership period")
        return period

    if status == "active" and str(user.get("plan_id") or "") in {"base", "pro"}:
        raise ValueError(
            "Active paid memberships cannot create a second Stripe subscription Checkout session; "
            "use the verified subscription lifecycle for plan or billing-period changes"
        )
    return period


def _configured_periods(config: StripeConfig) -> dict[str, dict[str, bool]]:
    shared = bool(config.secret_key and config.public_base_url)
    return {
        "base": {
            "monthly": bool(shared and config.base_price_id),
            "annual": False,
        },
        "pro": {
            "monthly": bool(shared and config.pro_price_id),
            "annual": bool(shared and (os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID") or "").strip()),
        },
    }


@router.get("/billing/stripe/status")
def membership_stripe_status():
    config = StripeConfig.from_env()
    periods = _configured_periods(config)
    return {
        "provider": "stripe",
        "checkout_configured": bool(
            config.secret_key
            and config.public_base_url
            and any(any(values.values()) for values in periods.values())
        ),
        "webhook_configured": config.webhook_configured,
        "subscription_plans": ["base", "pro"],
        "subscription_billing_periods": periods,
        "credit_topups_configured": bool(credit_packs()),
        "bank_details_stored_in_application": False,
        "settlement_destination": "Configured privately in the Stripe Dashboard",
    }


@router.post("/billing/stripe/checkout/subscription")
def membership_subscription_checkout(body: MembershipSubscriptionCheckoutRequest, request: Request):
    user = _session_user(request)
    _validate_checkout_user(user, body.plan_id)
    try:
        period = approved_checkout_period(user, body.plan_id, body.billing_period)
        plan = get_plan(body.plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    config = StripeConfig.from_env()
    if not config.secret_key or not config.public_base_url:
        raise HTTPException(503, "Stripe subscription checkout is not configured")
    try:
        price_id = subscription_price_id(config, plan.id, period)
        base = _clean_base_url(config.public_base_url)
        session = StripeClient(config)._post(
            "/v1/checkout/sessions",
            {
                "mode": "subscription",
                "client_reference_id": str(user["id"]),
                "customer_email": str(user.get("email") or ""),
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": f"{base}/billing/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base}/membership/payment?stripe=cancelled",
                "metadata[user_id]": str(user["id"]),
                "metadata[plan_id]": plan.id,
                "metadata[billing_period]": period.value,
                "metadata[purchase_kind]": "subscription",
                "subscription_data[metadata][user_id]": str(user["id"]),
                "subscription_data[metadata][plan_id]": plan.id,
                "subscription_data[metadata][billing_period]": period.value,
            },
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc

    url = str(session.get("url") or "")
    if not url.startswith("https://checkout.stripe.com/"):
        raise HTTPException(502, "Stripe did not return a trusted Checkout URL")
    return {
        "provider": "stripe",
        "checkout_session_id": session.get("id"),
        "checkout_url": url,
        "plan_id": plan.id,
        "billing_period": period.value,
        "amount": str(plan.price_for(period)),
        "amount_minor": plan.price_minor_for(period),
        "currency": plan.currency,
        "automatic_activation_from_redirect": False,
        "activation_source": "verified_stripe_webhook",
        "esp_role_effect": "none",
    }


# Subscription status/cancel/refund controls share the canonical commercial router so the
# production application exposes one membership authority rather than a parallel billing stack.
router.include_router(subscription_lifecycle_router)


__all__ = [
    "MembershipSubscriptionCheckoutRequest",
    "approved_checkout_period",
    "membership_stripe_status",
    "membership_subscription_checkout",
    "router",
    "subscription_price_id",
]
