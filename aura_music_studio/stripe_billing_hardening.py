from __future__ import annotations

import json
import os
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .creation_coin_catalog import validate_creation_coin_packs
from .marketplace_accounting import router as marketplace_account_router
from .native_products import BillingPeriod
from .plans import get_plan
from .stripe_billing import (
    CreditCheckoutRequest,
    StripeClient,
    StripeConfig,
    _billing_reason,
    _clean_base_url,
    _session_user,
    _subscription_id,
    _validate_checkout_user,
    accounts,
    create_credit_checkout as base_create_credit_checkout,
    credit_packs,
    evidence_store,
    stripe_webhook as base_stripe_webhook,
    subscriptions,
    verify_webhook_signature,
)
from .stripe_creation_coins import (
    creation_coin_catalog as base_creation_coin_catalog,
    creation_coin_storefront as base_creation_coin_storefront,
)
from .stripe_marketplace_checkout import (
    MarketplaceCheckoutRequest,
    create_marketplace_checkout as base_create_marketplace_checkout,
)
from .stripe_marketplace_payout_evidence import (
    PAYOUT_EVENT_TYPES,
    process_verified_stripe_payout_event,
)
from .stripe_marketplace_webhooks import (
    is_marketplace_stripe_event,
    process_verified_marketplace_stripe_event,
)

router = APIRouter(tags=["Stripe Billing Security"])
router.include_router(marketplace_account_router)
_REFUND_EVENT_TYPES = frozenset({"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"})


class HardenedSubscriptionCheckoutRequest(BaseModel):
    plan_id: str = Field(pattern="^(base|pro)$")
    billing_period: BillingPeriod = BillingPeriod.MONTHLY


def _subscription_price_id(config: StripeConfig, plan_id: str, billing_period: BillingPeriod | str) -> str:
    period = BillingPeriod(billing_period)
    plan = get_plan(plan_id)
    plan.price_for(period)
    if period is BillingPeriod.MONTHLY:
        return config.price_id(plan.id)
    if plan.id != "pro":
        raise ValueError(f"Annual billing is not available for plan: {plan.id}")
    value = (os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID") or "").strip()
    if not value:
        raise ValueError("Stripe annual price id is not configured for Unlimited Pro")
    return value


def _subscription_duplicate_response(evidence: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    prior_status = str(evidence.get("processing_status") or "")
    if evidence.get("duplicate") and prior_status in {"processed", "ignored"}:
        return {
            "received": True,
            "duplicate": True,
            "event_id": event_id,
            "status": prior_status,
            "subscription": True,
        }
    return None


def validate_subscription_cycle_invoice(invoice: dict[str, Any], binding: dict[str, Any]) -> None:
    """Fail closed before a recurring Stripe invoice is allowed to extend access."""
    plan = get_plan(str(binding.get("plan_id") or ""))
    if plan.id == "free":
        raise ValueError("Free plan cannot be renewed from Stripe")
    try:
        period = BillingPeriod(str(binding.get("billing_period") or BillingPeriod.MONTHLY.value))
        expected_amount = plan.price_minor_for(period)
    except ValueError as exc:
        raise ValueError("Stripe subscription binding has an unsupported billing period") from exc
    if str(invoice.get("status") or "").lower() != "paid":
        raise ValueError("Stripe renewal invoice is not paid")
    if str(invoice.get("currency") or "").upper() != plan.currency:
        raise ValueError("Stripe renewal invoice currency does not match the subscription")
    try:
        amount_paid = int(invoice.get("amount_paid"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Stripe renewal invoice has no valid paid amount") from exc
    if amount_paid != expected_amount:
        raise ValueError("Stripe renewal invoice paid amount does not match the configured membership price")

    expected_customer = str(binding.get("stripe_customer_id") or "")
    actual_customer = str(invoice.get("customer") or "")
    if not expected_customer or actual_customer != expected_customer:
        raise ValueError("Stripe renewal invoice customer does not match the local subscription binding")

    expected_subscription = str(binding.get("stripe_subscription_id") or "")
    actual_subscription = _subscription_id(invoice)
    if not expected_subscription or actual_subscription != expected_subscription:
        raise ValueError("Stripe renewal invoice subscription does not match the local binding")


def _validated_creation_coin_packs():
    try:
        return validate_creation_coin_packs(credit_packs())
    except ValueError as exc:
        raise HTTPException(503, "Creation Coin catalogue is not safely configured") from exc


def _require_signed_in_user(request: Request) -> dict[str, Any]:
    return _session_user(request)


def _begin_marketplace_event(event: dict[str, Any], raw: bytes) -> dict[str, Any]:
    try:
        return evidence_store.begin_event(event, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _terminal_duplicate_response(evidence: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    prior_status = str(evidence.get("processing_status") or "")
    if evidence.get("duplicate") and prior_status in {"processed", "ignored"}:
        return {
            "received": True,
            "duplicate": True,
            "event_id": event_id,
            "status": prior_status,
            "marketplace": True,
        }
    return None


def _terminal_payout_duplicate_response(
    evidence: dict[str, Any], event_id: str
) -> dict[str, Any] | None:
    prior_status = str(evidence.get("processing_status") or "")
    if evidence.get("duplicate") and prior_status in {"processed", "ignored"}:
        return {
            "received": True,
            "duplicate": True,
            "event_id": event_id,
            "status": prior_status,
            "provider_payout": True,
            "bank_reconciled": False,
        }
    return None


@router.get("/billing/stripe/status")
def hardened_stripe_status():
    config = StripeConfig.from_env()
    annual_pro = bool(
        config.secret_key
        and config.public_base_url
        and (os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID") or "").strip()
    )
    return {
        "provider": "stripe",
        "checkout_configured": config.checkout_configured,
        "webhook_configured": config.webhook_configured,
        "subscription_plans": ["base", "pro"],
        "subscription_billing_periods": {
            "base": {"monthly": bool(config.secret_key and config.public_base_url and config.base_price_id), "annual": False},
            "pro": {"monthly": bool(config.secret_key and config.public_base_url and config.pro_price_id), "annual": annual_pro},
        },
        "credit_topups_configured": bool(credit_packs()),
        "bank_details_stored_in_application": False,
        "settlement_destination": "Configured privately in the Stripe Dashboard",
    }


@router.post("/billing/stripe/checkout/subscription")
def hardened_subscription_checkout(body: HardenedSubscriptionCheckoutRequest, request: Request):
    user = _session_user(request)
    _validate_checkout_user(user, body.plan_id)
    config = StripeConfig.from_env()
    if not config.secret_key or not config.public_base_url:
        raise HTTPException(503, "Stripe subscription checkout is not configured")
    try:
        period = BillingPeriod(body.billing_period)
        plan = get_plan(body.plan_id)
        price_id = _subscription_price_id(config, plan.id, period)
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
        "amount_minor": plan.price_minor_for(period),
        "currency": plan.currency,
        "automatic_activation_from_redirect": False,
        "activation_source": "verified_stripe_webhook",
        "esp_role_effect": "none",
    }


@router.get("/billing/creation-coins/catalog")
def hardened_creation_coin_catalog(request: Request):
    _require_signed_in_user(request)
    _validated_creation_coin_packs()
    response = base_creation_coin_catalog(request)
    if isinstance(response, dict):
        try:
            validate_creation_coin_packs(
                {str(index): pack for index, pack in enumerate(response.get("packs") or [])}
            )
        except ValueError as exc:
            raise HTTPException(503, "Creation Coin catalogue changed during request processing") from exc
    return response


@router.get("/billing/creation-coins", response_class=HTMLResponse)
def hardened_creation_coin_storefront(request: Request):
    _require_signed_in_user(request)
    _validated_creation_coin_packs()
    return base_creation_coin_storefront(request)


@router.post("/billing/stripe/checkout/credits")
def hardened_credit_checkout(body: CreditCheckoutRequest, request: Request):
    _require_signed_in_user(request)
    _validated_creation_coin_packs()
    return base_create_credit_checkout(body, request)


@router.post("/billing/stripe/checkout/marketplace")
def hardened_marketplace_checkout(body: MarketplaceCheckoutRequest, request: Request):
    return base_create_marketplace_checkout(body, request)


@router.get("/billing/stripe/success", response_class=HTMLResponse)
def hardened_stripe_success(session_id: str = ""):
    safe = escape((session_id or "")[:120], quote=True)
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Payment received</title></head><body style='font-family:system-ui;background:#08050d;color:#fff;padding:36px'>"
        "<main style='max-width:680px;margin:auto'><h1>Thank you.</h1>"
        "<p>Stripe has returned you to the Command Center. Access, Creation Coins and marketplace settlement are confirmed only after signed Stripe webhook and server-side provider evidence are verified.</p>"
        f"<p style='opacity:.7'>Checkout session: {safe}</p><p><a href='/dashboard' style='color:#f4c873'>Return to dashboard</a></p></main></body></html>"
    )


@router.post("/billing/stripe/webhook")
async def hardened_stripe_webhook(request: Request):
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

    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}

    if event_type in PAYOUT_EVENT_TYPES:
        payout_event_evidence = _begin_marketplace_event(event, raw)
        duplicate = _terminal_payout_duplicate_response(payout_event_evidence, event_id)
        if duplicate is not None:
            return duplicate
        if not isinstance(obj, dict):
            evidence_store.finish_event(event_id, "failed", "Stripe payout event object is not a dictionary")
            raise HTTPException(400, "Stripe payout event object is not a dictionary")
        try:
            result = process_verified_stripe_payout_event(event_id=event_id, event_type=event_type, obj=obj, config=config)
        except RuntimeError as exc:
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise HTTPException(502, str(exc)) from exc
        except ValueError as exc:
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise HTTPException(400, str(exc)) from exc
        evidence_store.finish_event(event_id, "processed")
        return {"received": True, "event_id": event_id, "provider_payout": True, **result}

    marketplace_event = False
    if isinstance(obj, dict):
        try:
            marketplace_event = is_marketplace_stripe_event(event_type, obj, config=config)
        except RuntimeError as exc:
            if event_type not in _REFUND_EVENT_TYPES:
                raise HTTPException(502, str(exc)) from exc
            failed_evidence = _begin_marketplace_event(event, raw)
            duplicate = _terminal_duplicate_response(failed_evidence, event_id)
            if duplicate is not None:
                return duplicate
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise HTTPException(502, str(exc)) from exc

    if marketplace_event:
        marketplace_evidence = _begin_marketplace_event(event, raw)
        duplicate = _terminal_duplicate_response(marketplace_evidence, event_id)
        if duplicate is not None:
            return duplicate
        try:
            result = process_verified_marketplace_stripe_event(event_id=event_id, event_type=event_type, obj=obj, config=config)
        except RuntimeError as exc:
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise HTTPException(502, str(exc)) from exc
        except (ValueError, PermissionError) as exc:
            evidence_store.finish_event(event_id, "failed", str(exc))
            raise HTTPException(400, str(exc)) from exc
        status = "processed" if result.get("processed") else "ignored"
        evidence_store.finish_event(event_id, status)
        return {"received": True, "event_id": event_id, "marketplace": True, **result}

    if event_type == "checkout.session.completed" and isinstance(obj, dict):
        metadata = obj.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        purchase_kind = str(metadata.get("purchase_kind") or "")
        if purchase_kind == "credit_topup":
            _validated_creation_coin_packs()
        elif purchase_kind == "subscription" and str(metadata.get("billing_period") or "") == BillingPeriod.ANNUAL.value:
            try:
                event_evidence = evidence_store.begin_event(event, raw)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            duplicate = _subscription_duplicate_response(event_evidence, event_id)
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
                plan = get_plan(str(metadata.get("plan_id") or ""))
                if plan.id == "free":
                    raise ValueError("Free plan cannot be activated from Stripe")
                if user.get("status") == "approved_pending_payment" and user.get("requested_plan_id") != plan.id:
                    raise ValueError("Stripe plan does not match the owner-approved requested plan")
                if int(obj.get("amount_total") or -1) != plan.price_minor_for(BillingPeriod.ANNUAL):
                    raise ValueError("Stripe Checkout amount does not match the configured annual membership price")
                if str(obj.get("currency") or "").upper() != plan.currency:
                    raise ValueError("Stripe Checkout currency does not match the membership currency")
                customer_id = str(obj.get("customer") or "")
                subscription_id = str(obj.get("subscription") or "")
                status = subscriptions.verify_payment(
                    user_id,
                    plan.id,
                    f"stripe:checkout:{obj.get('id')}",
                    billing_period=BillingPeriod.ANNUAL,
                )
                evidence_store.bind_subscription(user_id, customer_id, subscription_id, plan.id, "active")
                evidence_store.finish_event(event_id, "processed")
                return {
                    "received": True,
                    "event_id": event_id,
                    "processed": True,
                    "kind": "subscription",
                    "plan_id": plan.id,
                    "billing_period": BillingPeriod.ANNUAL.value,
                    "user_id": user_id,
                    "subscription": status["subscription"],
                }
            except Exception as exc:
                evidence_store.finish_event(event_id, "failed", str(exc))
                raise HTTPException(400, str(exc)) from exc

    if event_type == "invoice.paid" and isinstance(obj, dict) and _billing_reason(obj) == "subscription_cycle":
        binding = evidence_store.binding(
            subscription_id=_subscription_id(obj),
            customer_id=str(obj.get("customer") or ""),
        )
        if not binding:
            raise HTTPException(400, "Stripe renewal invoice has no local subscription binding")
        state = subscriptions.get(str(binding.get("user_id") or "")) or {}
        period_value = str(state.get("billing_period") or BillingPeriod.MONTHLY.value)
        binding_for_validation = {**binding, "billing_period": period_value}
        try:
            validate_subscription_cycle_invoice(obj, binding_for_validation)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        if period_value == BillingPeriod.ANNUAL.value:
            try:
                event_evidence = evidence_store.begin_event(event, raw)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            duplicate = _subscription_duplicate_response(event_evidence, event_id)
            if duplicate is not None:
                return duplicate
            try:
                plan = get_plan(str(binding.get("plan_id") or ""))
                status = subscriptions.verify_payment(
                    str(binding["user_id"]),
                    plan.id,
                    f"stripe:invoice:{obj.get('id')}",
                    billing_period=BillingPeriod.ANNUAL,
                )
                evidence_store.set_binding_status(str(binding["user_id"]), "active")
                evidence_store.finish_event(event_id, "processed")
                return {
                    "received": True,
                    "event_id": event_id,
                    "processed": True,
                    "kind": "subscription_renewal",
                    "user_id": binding["user_id"],
                    "plan_id": plan.id,
                    "billing_period": BillingPeriod.ANNUAL.value,
                    "subscription": status["subscription"],
                }
            except Exception as exc:
                evidence_store.finish_event(event_id, "failed", str(exc))
                raise HTTPException(400, str(exc)) from exc

    return await base_stripe_webhook(request)


__all__ = [
    "HardenedSubscriptionCheckoutRequest",
    "hardened_creation_coin_catalog",
    "hardened_creation_coin_storefront",
    "hardened_credit_checkout",
    "hardened_marketplace_checkout",
    "hardened_stripe_status",
    "hardened_subscription_checkout",
    "hardened_stripe_success",
    "hardened_stripe_webhook",
    "router",
    "validate_subscription_cycle_invoice",
]
