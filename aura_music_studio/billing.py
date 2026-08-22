from __future__ import annotations

import os
from dataclasses import dataclass

from .plans import get_plan


@dataclass(frozen=True)
class PaymentOption:
    plan_id: str
    provider: str
    amount_usd: str
    payment_url: str
    mode: str
    automatic_activation: bool
    note: str


DEFAULT_BASE_PAYPAL_URL = "https://www.paypal.com/invoice/p/#8MW58LYURC584SWJ"
DEFAULT_PRO_PAYPAL_URL = "https://www.paypal.com/invoice/p/#678LURGCLH77JDGH"


def payment_option(plan_id: str) -> PaymentOption | None:
    plan = get_plan(plan_id)
    if plan.id == "free":
        return None

    if plan.id == "base":
        url = os.getenv("LSS_PAYPAL_BASE_URL", DEFAULT_BASE_PAYPAL_URL)
    elif plan.id == "pro":
        url = os.getenv("LSS_PAYPAL_PRO_URL", DEFAULT_PRO_PAYPAL_URL)
    else:
        raise ValueError(f"No payment route configured for plan {plan.id}")

    return PaymentOption(
        plan_id=plan.id,
        provider="paypal",
        amount_usd=str(plan.monthly_price_usd),
        payment_url=url,
        mode="manual_invoice_link",
        automatic_activation=False,
        note=(
            "Current PayPal URL is configured as a manual invoice/payment link. "
            "The Live Sound Studio must not treat a browser return as proof of payment. "
            "A verified PayPal transaction or owner/admin confirmation is required before activating the paid plan."
        ),
    )


def public_payment_options() -> list[dict]:
    result = []
    for plan_id in ("base", "pro"):
        option = payment_option(plan_id)
        if option:
            result.append(option.__dict__.copy())
    return result


def payment_instructions(plan_id: str) -> dict:
    plan = get_plan(plan_id)
    if plan.id == "free":
        return {
            "plan": "free",
            "payment_required": False,
            "next_status": "active_after_owner_approval",
        }
    option = payment_option(plan.id)
    return {
        "plan": plan.id,
        "payment_required": True,
        "amount_usd": str(plan.monthly_price_usd),
        "provider": "paypal",
        "url": option.payment_url if option else None,
        "verification": "manual_or_verified_paypal_event",
        "next_status": "active_after_payment_verification",
    }
