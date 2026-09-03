from __future__ import annotations

import os
from dataclasses import dataclass

from .plans import BILLING_MONTHLY, get_plan


@dataclass(frozen=True)
class PaymentOption:
    plan_id: str
    billing_period: str
    provider: str
    amount: str
    amount_minor: int
    currency: str
    payment_url: str
    mode: str
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


def payment_option(plan_id: str, billing_period: str = BILLING_MONTHLY) -> PaymentOption | None:
    plan = get_plan(plan_id)
    if plan.id == "free":
        return None

    price = plan.price_for_period(billing_period)
    period = (billing_period or "").strip().lower()

    if plan.id == "base":
        url = os.getenv("LSS_PAYPAL_BASE_URL", DEFAULT_BASE_PAYPAL_URL)
    elif plan.id == "pro":
        if period == "annual":
            url = os.getenv("LSS_PAYPAL_PRO_ANNUAL_URL", "").strip()
            if not url:
                raise ValueError("Annual Pro payment route is not configured")
        else:
            url = os.getenv("LSS_PAYPAL_PRO_URL", DEFAULT_PRO_PAYPAL_URL)
    else:
        raise ValueError(f"No payment route configured for plan {plan.id}")

    return PaymentOption(
        plan_id=plan.id,
        billing_period=period,
        provider="paypal",
        amount=str(price),
        amount_minor=plan.price_minor(period),
        currency=plan.currency,
        payment_url=url,
        mode="manual_invoice_link",
        automatic_activation=False,
        note=(
            "The current PayPal URL is configured as a manual invoice/payment link. "
            "Pulsar-Frequency House must not treat a browser return as proof of payment. "
            "A verified provider transaction or explicit owner/admin verification is required before activating a paid plan."
        ),
    )


def public_payment_options() -> list[dict]:
    result = []
    for plan_id in ("base", "pro"):
        option = payment_option(plan_id, BILLING_MONTHLY)
        if option:
            result.append(option.public_dict())
    # Annual Pro is intentionally advertised only when an explicit server-side payment route exists.
    if os.getenv("LSS_PAYPAL_PRO_ANNUAL_URL", "").strip():
        result.append(payment_option("pro", "annual").public_dict())
    return result


def payment_instructions(plan_id: str, billing_period: str = BILLING_MONTHLY) -> dict:
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
    period = (billing_period or "").strip().lower()
    price = plan.price_for_period(period)
    option = payment_option(plan.id, period)
    return {
        "plan": plan.id,
        "billing_period": period,
        "payment_required": True,
        "amount": str(price),
        "amount_minor": plan.price_minor(period),
        "currency": plan.currency,
        "display_amount": f"{plan.currency_symbol}{price}",
        # Deprecated compatibility alias. Do not infer USD from this key; use currency.
        "amount_usd": str(price),
        "provider": "paypal",
        "url": option.payment_url if option else None,
        "verification": "manual_or_verified_provider_event",
        "automatic_activation": False,
        "next_status": "active_after_payment_verification",
    }
