from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .membership_billing_periods import bind_stripe_subscription, ensure_billing_period_schema, verify_catalog_payment
from .plans import BILLING_MONTH, BILLING_YEAR, get_plan, normalize_billing_period
from .stripe_billing import (
    StripeClient,
    StripeConfig,
    _billing_reason,
    _clean_base_url,
    _session_user,
    _subscription_id,
    _validate_checkout_user,
    accounts,
    evidence_store,
    subscriptions,
)

router = APIRouter(tags=["Stripe Membership Billing"])


class CanonicalSubscriptionCheckoutRequest(BaseModel):
    plan_id: str = Field(pattern="^(base|pro)$")
    billing_period: str = Field(default=BILLING_MONTH, pattern="^(month|year)$")


def stripe_membership_price_id(config: StripeConfig, plan_id: str, billing_period: str) -> str:
    period = normalize_billing_period(billing_period)
    plan = get_plan(plan_id)
    if not plan.supports_billing_period(period):
        raise ValueError(f"{plan.name} does not support {period} billing")
    if period == BILLING_MONTH:
        return config.price_id(plan.id)
    if plan.id != "pro":
        raise ValueError(f"{plan.name} does not have an annual Stripe price")
    value = (os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID") or "").strip()
    if not value:
        raise ValueError("Stripe annual price id is not configured for Unlimited Pro")
    return value


def _checkout_payload(config: StripeConfig, user: dict[str, Any], plan_id: str, billing_period: str) -> dict[str, str]:
    plan = get_plan(plan_id)
    period = normalize_billing_period(billing_period)
    price_id = stripe_membership_price_id(config, plan.id, period)
    base = _clean_base_url(config.public_base_url)
    return {
        "mode": "subscription",
        "client_reference_id": str(user["id"]),
        "customer_email": str(user.get("email") or ""),
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": f"{base}/billing/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/membership/payment?stripe=cancelled",
        "metadata[user_id]": str(user["id"]),
        "metadata[plan_id]": plan.id,
        "metadata[billing_period]": period,
        "metadata[purchase_kind]": "subscription",
        "subscription_data[metadata][user_id]": str(user["id"]),
        "subscription_data[metadata][plan_id]": plan.id,
        "subscription_data[metadata][billing_period]": period,
    }


@router.post("/billing/stripe/checkout/subscription")
def canonical_subscription_checkout(body: CanonicalSubscriptionCheckoutRequest, request: Request):
    user = _session_user(request)
    _validate_checkout_user(user, body.plan_id)
    plan = get_plan(body.plan_id)
    period = normalize_billing_period(body.billing_period)
    if not plan.supports_billing_period(period):
        raise HTTPException(400, f"{plan.name} does not support {period} billing")
    config = StripeConfig.from_env()
    if not config.secret_key or not config.public_base_url:
        raise HTTPException(503, "Stripe subscription checkout is not configured")
    try:
        session = StripeClient(config)._post("/v1/checkout/sessions", _checkout_payload(config, user, plan.id, period))
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
        "billing_period": period,
        "amount_minor": plan.price_minor(period),
        "currency": plan.currency,
        "automatic_activation_from_redirect": False,
        "activation_source": "verified_stripe_webhook",
        "esp_role_effect": "none",
    }


def _begin_event(event: dict[str, Any], raw: bytes) -> tuple[dict[str, Any], dict[str, Any] | None]:
    evidence = evidence_store.begin_event(event, raw)
    if evidence.get("duplicate") and str(evidence.get("processing_status") or "") in {"processed", "ignored"}:
        return evidence, {
            "received": True,
            "duplicate": True,
            "event_id": evidence["event_id"],
            "status": evidence["processing_status"],
        }
    return evidence, None


def _membership_metadata(obj: dict[str, Any]) -> dict[str, Any]:
    metadata = obj.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _binding_for_object(obj: dict[str, Any]) -> dict[str, Any] | None:
    ensure_billing_period_schema(accounts.db_path)
    return evidence_store.binding(
        subscription_id=_subscription_id(obj) or str(obj.get("subscription") or ""),
        customer_id=str(obj.get("customer") or ""),
    )


def _validate_paid_amount(obj: dict[str, Any], plan_id: str, billing_period: str, *, amount_field: str) -> None:
    plan = get_plan(plan_id)
    period = normalize_billing_period(billing_period)
    if str(obj.get("currency") or "").upper() != plan.currency:
        raise ValueError("Stripe currency does not match the configured membership currency")
    try:
        amount = int(obj.get(amount_field))
    except (TypeError, ValueError) as exc:
        raise ValueError("Stripe event has no valid paid amount") from exc
    if amount != plan.price_minor(period):
        raise ValueError("Stripe paid amount does not match the canonical membership price and period")


def process_verified_membership_event(event: dict[str, Any], raw: bytes) -> dict[str, Any] | None:
    """Process membership billing after the caller has verified the Stripe signature.

    Returns ``None`` for non-membership Stripe events so the existing commerce handler can
    continue. Entitlement changes occur only from verified paid checkout/invoice state.
    Provider-side subscription updates alone never grant or extend access.
    """
    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "").strip()
    obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}
    if not event_id or not isinstance(obj, dict):
        return None

    metadata = _membership_metadata(obj)
    purchase_kind = str(metadata.get("purchase_kind") or "")

    if event_type == "checkout.session.completed" and purchase_kind == "subscription":
        evidence, duplicate = _begin_event(event, raw)
        if duplicate is not None:
            return duplicate
        try:
            user_id = str(metadata.get("user_id") or obj.get("client_reference_id") or "").strip()
            if not user_id:
                raise ValueError("Stripe Checkout Session has no bound user id")
            user = accounts.get_user(user_id)
            if not user:
                raise ValueError("Stripe Checkout Session references an unknown user")
            if str(obj.get("payment_status") or "") != "paid":
                raise ValueError("Stripe Checkout Session is not paid")
            plan_id = str(metadata.get("plan_id") or "")
            period = normalize_billing_period(str(metadata.get("billing_period") or BILLING_MONTH))
            plan = get_plan(plan_id)
            if plan.id == "free" or not plan.supports_billing_period(period):
                raise ValueError("Stripe Checkout references an unsupported membership price period")
            if user.get("status") == "approved_pending_payment" and user.get("requested_plan_id") != plan.id:
                raise ValueError("Stripe plan does not match the owner-approved requested plan")
            _validate_paid_amount(obj, plan.id, period, amount_field="amount_total")
            customer_id = str(obj.get("customer") or "")
            subscription_id = str(obj.get("subscription") or "")
            status = verify_catalog_payment(
                subscriptions,
                user_id,
                plan.id,
                f"stripe:checkout:{obj.get('id')}",
                billing_period=period,
            )
            bind_stripe_subscription(
                accounts.db_path,
                user_id=user_id,
                customer_id=customer_id,
                subscription_id=subscription_id,
                plan_id=plan.id,
                billing_period=period,
                status="active",
            )
            result = {
                "received": True,
                "event_id": event_id,
                "processed": True,
                "kind": "subscription",
                "plan_id": plan.id,
                "billing_period": period,
                "user_id": user_id,
                "subscription": status["subscription"],
            }
            evidence_store.finish_event(event_id, "processed")
            return result
        except Exception as exc:
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise

    if event_type in {"invoice.paid", "invoice.payment_failed"}:
        binding = _binding_for_object(obj)
        reason = _billing_reason(obj)
        if not binding:
            return None
        evidence, duplicate = _begin_event(event, raw)
        if duplicate is not None:
            return duplicate
        try:
            period = normalize_billing_period(str(binding.get("billing_period") or BILLING_MONTH))
            if event_type == "invoice.payment_failed":
                evidence_store.set_binding_status(binding["user_id"], "payment_failed")
                evidence_store.finish_event(event_id, "processed")
                return {
                    "received": True,
                    "event_id": event_id,
                    "processed": True,
                    "kind": "payment_failed",
                    "billing_period": period,
                    "access_removed_immediately": False,
                }
            if reason == "subscription_create":
                evidence_store.finish_event(event_id, "ignored")
                return {
                    "received": True,
                    "event_id": event_id,
                    "processed": False,
                    "ignored": True,
                    "reason": "initial_invoice_is_covered_by_checkout_completion",
                }
            if reason != "subscription_cycle":
                evidence_store.finish_event(event_id, "ignored")
                return {
                    "received": True,
                    "event_id": event_id,
                    "processed": False,
                    "ignored": True,
                    "reason": f"unsupported_invoice_reason:{reason or 'unknown'}",
                }
            if str(obj.get("status") or "").lower() != "paid":
                raise ValueError("Stripe renewal invoice is not paid")
            _validate_paid_amount(obj, str(binding["plan_id"]), period, amount_field="amount_paid")
            status = verify_catalog_payment(
                subscriptions,
                str(binding["user_id"]),
                str(binding["plan_id"]),
                f"stripe:invoice:{obj.get('id')}",
                billing_period=period,
            )
            evidence_store.set_binding_status(binding["user_id"], "active")
            evidence_store.finish_event(event_id, "processed")
            return {
                "received": True,
                "event_id": event_id,
                "processed": True,
                "kind": "subscription_renewal",
                "user_id": binding["user_id"],
                "plan_id": binding["plan_id"],
                "billing_period": period,
                "subscription": status["subscription"],
            }
        except Exception as exc:
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise

    if event_type in {"customer.subscription.deleted", "customer.subscription.updated"}:
        binding = _binding_for_object(obj)
        if not binding:
            return None
        evidence, duplicate = _begin_event(event, raw)
        if duplicate is not None:
            return duplicate
        if event_type == "customer.subscription.deleted":
            evidence_store.set_binding_status(binding["user_id"], "cancelled")
            evidence_store.finish_event(event_id, "processed")
            return {
                "received": True,
                "event_id": event_id,
                "processed": True,
                "kind": "subscription_cancelled",
                "access_removed_immediately": False,
                "access_policy": "retain_until_verified_paid_period_expires",
            }
        # Upgrade/downgrade mutations do not change local entitlement without a paid invoice.
        evidence_store.finish_event(event_id, "ignored")
        return {
            "received": True,
            "event_id": event_id,
            "processed": False,
            "ignored": True,
            "reason": "subscription_change_requires_verified_paid_invoice",
            "entitlement_changed": False,
        }

    if event_type in {"refund.created", "refund.updated", "charge.refunded"} and purchase_kind == "subscription":
        # A refund is never interpreted as a fresh payment or upgrade. Where Stripe metadata
        # makes the membership binding explicit, mark it for reconciliation and preserve the
        # already-paid period until a deliberate refund/cancellation policy action is verified.
        binding = _binding_for_object(obj)
        if not binding:
            return None
        evidence, duplicate = _begin_event(event, raw)
        if duplicate is not None:
            return duplicate
        evidence_store.set_binding_status(binding["user_id"], "refund_review")
        evidence_store.finish_event(event_id, "processed")
        return {
            "received": True,
            "event_id": event_id,
            "processed": True,
            "kind": "subscription_refund_review",
            "entitlement_changed": False,
            "access_removed_immediately": False,
        }

    return None


__all__ = [
    "CanonicalSubscriptionCheckoutRequest",
    "canonical_subscription_checkout",
    "process_verified_membership_event",
    "router",
    "stripe_membership_price_id",
]
