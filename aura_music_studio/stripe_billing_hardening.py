from __future__ import annotations

import json
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .plans import get_plan
from .stripe_billing import (
    StripeConfig,
    _billing_reason,
    _subscription_id,
    evidence_store,
    stripe_webhook as base_stripe_webhook,
    verify_webhook_signature,
)

router = APIRouter(tags=["Stripe Billing Security"])


def validate_subscription_cycle_invoice(invoice: dict[str, Any], binding: dict[str, Any]) -> None:
    """Fail closed before a recurring Stripe invoice is allowed to extend access.

    The signed event must still describe the exact locally-bound subscription/customer and
    the exact configured plan price. This prevents a dashboard-side price/coupon/configuration
    drift from silently extending a different or underpaid local entitlement.
    """
    plan = get_plan(str(binding.get("plan_id") or ""))
    if plan.id == "free":
        raise ValueError("Free plan cannot be renewed from Stripe")
    if str(invoice.get("status") or "").lower() != "paid":
        raise ValueError("Stripe renewal invoice is not paid")
    if str(invoice.get("currency") or "").upper() != plan.currency:
        raise ValueError("Stripe renewal invoice currency does not match the subscription")
    try:
        amount_paid = int(invoice.get("amount_paid"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Stripe renewal invoice has no valid paid amount") from exc
    if amount_paid != plan.monthly_price_minor:
        raise ValueError("Stripe renewal invoice paid amount does not match the configured membership price")

    expected_customer = str(binding.get("stripe_customer_id") or "")
    actual_customer = str(invoice.get("customer") or "")
    if not expected_customer or actual_customer != expected_customer:
        raise ValueError("Stripe renewal invoice customer does not match the local subscription binding")

    expected_subscription = str(binding.get("stripe_subscription_id") or "")
    actual_subscription = _subscription_id(invoice)
    if not expected_subscription or actual_subscription != expected_subscription:
        raise ValueError("Stripe renewal invoice subscription does not match the local binding")


@router.get("/billing/stripe/success", response_class=HTMLResponse)
def hardened_stripe_success(session_id: str = ""):
    """Render the informational Stripe return page without reflecting untrusted HTML."""
    safe = escape((session_id or "")[:120], quote=True)
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Payment received</title></head><body style='font-family:system-ui;background:#08050d;color:#fff;padding:36px'>"
        "<main style='max-width:680px;margin:auto'><h1>Thank you.</h1>"
        "<p>Stripe has returned you to the Command Center. Access or credits are confirmed only after the signed Stripe webhook is verified.</p>"
        f"<p style='opacity:.7'>Checkout session: {safe}</p><p><a href='/dashboard' style='color:#f4c873'>Return to dashboard</a></p></main></body></html>"
    )


@router.post("/billing/stripe/webhook")
async def hardened_stripe_webhook(request: Request):
    """Preflight renewal invoices, then delegate to the idempotent Stripe processor.

    Checkout, credit and non-renewal events remain owned by the base Stripe processor. The
    request body is cached by Starlette, so the delegated handler verifies the same raw bytes
    and signature again before it mutates any billing state.
    """
    config = StripeConfig.from_env()
    if not config.webhook_configured:
        raise HTTPException(503, "Stripe webhook verification is not configured")
    raw = await request.body()
    try:
        verify_webhook_signature(raw, request.headers.get("stripe-signature", ""), config.webhook_secret)
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid Stripe event")

    if str(event.get("type") or "") == "invoice.paid":
        obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}
        if isinstance(obj, dict) and _billing_reason(obj) == "subscription_cycle":
            binding = evidence_store.binding(
                subscription_id=_subscription_id(obj),
                customer_id=str(obj.get("customer") or ""),
            )
            if not binding:
                raise HTTPException(400, "Stripe renewal invoice has no local subscription binding")
            try:
                validate_subscription_cycle_invoice(obj, binding)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

    return await base_stripe_webhook(request)


__all__ = ["hardened_stripe_success", "router", "validate_subscription_cycle_invoice"]
