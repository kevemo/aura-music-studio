from __future__ import annotations

import os
from dataclasses import dataclass

from .plans import BILLING_MONTH, BILLING_YEAR, get_plan, normalize_billing_period


@dataclass(frozen=True)
class PaymentOption:
    plan_id: str
    billing_period: str
    provider: str
    amount: str
    amount_minor: int
    currency: str
    payment_url: str | None
    mode: str
    configured: bool
    automatic_activation: bool
    note: str

    @property
    def amount_usd(self) -> str:
        """Deprecated compatibility alias for pre-GBP callers."""
        return self.amount

    def public_dict(self) -> dict:
        data = self.__dict__.copy()
        data["amount_usd"] = self.amount  # compatibility only; currency is authoritative
        return data


DEFAULT_BASE_PAYPAL_URL = "https://www.paypal.com/invoice/p/#8MW58LYURC584SWJ"
DEFAULT_PRO_PAYPAL_URL = "https://www.paypal.com/invoice/p/#678LURGCLH77JDGH"


def _paypal_url(plan_id: str, billing_period: str) -> str | None:
    period = normalize_billing_period(billing_period)
    if plan_id == "base" and period == BILLING_MONTH:
        return (os.getenv("LSS_PAYPAL_BASE_URL") or DEFAULT_BASE_PAYPAL_URL).strip() or None
    if plan_id == "pro" and period == BILLING_MONTH:
        return (os.getenv("LSS_PAYPAL_PRO_URL") or DEFAULT_PRO_PAYPAL_URL).strip() or None
    if plan_id == "pro" and period == BILLING_YEAR:
        # No annual invoice link is invented. Owners can configure one explicitly, while
        # Stripe remains able to provide the canonical annual subscription checkout.
        return (os.getenv("LSS_PAYPAL_PRO_ANNUAL_URL") or "").strip() or None
    return None


def payment_option(plan_id: str, billing_period: str = BILLING_MONTH) -> PaymentOption | None:
    plan = get_plan(plan_id)
    if plan.id == "free":
        return None
    period = normalize_billing_period(billing_period)
    if not plan.supports_billing_period(period):
        return None

    url = _paypal_url(plan.id, period)
    return PaymentOption(
        plan_id=plan.id,
        billing_period=period,
        provider="paypal",
        amount=str(plan.price(period)),
        amount_minor=plan.price_minor(period),
        currency=plan.currency,
        payment_url=url,
        mode="manual_invoice_link",
        configured=bool(url),
        automatic_activation=False,
        note=(
            "A configured PayPal URL is a manual invoice/payment link only. The Command Center must not treat a browser "
            "return as proof of payment. A verified provider transaction or explicit owner/admin verification is required "
            "before activating or extending a paid plan."
        ),
    )


def public_payment_options() -> list[dict]:
    result: list[dict] = []
    for plan_id in ("base", "pro"):
        plan = get_plan(plan_id)
        for period in (BILLING_MONTH, BILLING_YEAR):
            if not plan.supports_billing_period(period):
                continue
            option = payment_option(plan_id, period)
            if option:
                result.append(option.public_dict())
    return result


def payment_instructions(plan_id: str, billing_period: str = BILLING_MONTH) -> dict:
    plan = get_plan(plan_id)
    if plan.id == "free":
        return {
            "plan": "free",
            "billing_period": None,
            "payment_required": False,
            "amount": "0.00",
            "amount_minor": 0,
            "currency": plan.currency,
            "display_amount": "Free",
            "next_status": "active_after_owner_approval",
        }
    period = normalize_billing_period(billing_period)
    if not plan.supports_billing_period(period):
        raise ValueError(f"{plan.name} does not support {period} billing")
    option = payment_option(plan.id, period)
    amount = plan.price(period)
    return {
        "plan": plan.id,
        "billing_period": period,
        "payment_required": True,
        "amount": str(amount),
        "amount_minor": plan.price_minor(period),
        "currency": plan.currency,
        "display_amount": f"{plan.currency_symbol}{amount}",
        # Deprecated compatibility alias. Do not infer USD from this key; use currency.
        "amount_usd": str(amount),
        "provider": "paypal",
        "url": option.payment_url if option else None,
        "provider_configured": bool(option and option.configured),
        "verification": "manual_or_verified_provider_event",
        "automatic_activation": False,
        "next_status": "active_after_payment_verification",
    }
