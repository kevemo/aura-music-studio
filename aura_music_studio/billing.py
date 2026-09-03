from __future__ import annotations

import os
from dataclasses import dataclass

from .native_products import BillingPeriod
from .plans import get_plan


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


def _period(value: BillingPeriod | str) -> BillingPeriod:
    try:
        return BillingPeriod(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported billing period: {value}") from exc


def payment_option(
    plan_id: str,
    billing_period: BillingPeriod | str = BillingPeriod.MONTHLY,
) -> PaymentOption | None:
    plan = get_plan(plan_id)
    period = _period(billing_period)

    # Resolve through the authoritative plan catalogue first. Unsupported combinations
    # fail closed here rather than allowing a payment route to invent a price.
    amount_value = plan.price_for(period)
    amount_minor = plan.price_minor_for(period)
    if plan.id == "free":
        return None

    if plan.id == "base":
        if period is BillingPeriod.MONTHLY:
            url = (os.getenv("LSS_PAYPAL_BASE_URL") or DEFAULT_BASE_PAYPAL_URL).strip()
        else:
            # Annual Basic is canonical at £59.99/year but must never reuse the monthly
            # fixed-price invoice. A dedicated annual route is required.
            url = (os.getenv("LSS_PAYPAL_BASE_ANNUAL_URL") or "").strip()
            if not url:
                raise ValueError("Annual Basic PayPal route is not configured")
    elif plan.id == "pro":
        if period is BillingPeriod.MONTHLY:
            url = (os.getenv("LSS_PAYPAL_PRO_URL") or DEFAULT_PRO_PAYPAL_URL).strip()
        else:
            # Never reuse a monthly fixed-price invoice for an annual purchase. Annual
            # PayPal presentation remains unavailable until an owner configures a dedicated
            # £99 route or a verified provider checkout owns the flow end to end.
            url = (os.getenv("LSS_PAYPAL_PRO_ANNUAL_URL") or "").strip()
            if not url:
                raise ValueError("Annual Unlimited Pro PayPal route is not configured")
    else:
        raise ValueError(f"No payment route configured for plan {plan.id}")

    if not url:
        raise ValueError(f"Payment route is not configured for {plan.name}")

    return PaymentOption(
        plan_id=plan.id,
        billing_period=period.value,
        provider="paypal",
        amount=str(amount_value),
        amount_minor=amount_minor,
        currency=plan.currency,
        payment_url=url,
        mode="manual_invoice_link",
        automatic_activation=False,
        note=(
            "The current PayPal URL is configured as a manual invoice/payment link. "
            "The Command Center must not treat a browser return as proof of payment. "
            "A verified provider transaction or explicit owner/admin verification is required before activating a paid plan."
        ),
    )


def public_payment_options() -> list[dict]:
    """Return currently configured default monthly manual-payment routes.

    Annual Basic and Unlimited Pro prices remain visible through the canonical plan catalogue.
    Annual manual PayPal routes are intentionally omitted from this default projection and are
    returned only when explicitly requested after their dedicated URLs are configured.
    """
    result = []
    for plan_id in ("base", "pro"):
        option = payment_option(plan_id, BillingPeriod.MONTHLY)
        if option:
            result.append(option.public_dict())
    return result


def payment_instructions(
    plan_id: str,
    billing_period: BillingPeriod | str = BillingPeriod.MONTHLY,
) -> dict:
    plan = get_plan(plan_id)
    period = _period(billing_period)
    amount_value = plan.price_for(period)
    amount_minor = plan.price_minor_for(period)

    if plan.id == "free":
        return {
            "plan": "free",
            "billing_period": period.value,
            "payment_required": False,
            "amount": str(amount_value),
            "amount_minor": amount_minor,
            "currency": plan.currency,
            "display_amount": "Free",
            "next_status": "active_after_owner_approval",
        }

    option = payment_option(plan.id, period)
    return {
        "plan": plan.id,
        "billing_period": period.value,
        "payment_required": True,
        "amount": str(amount_value),
        "amount_minor": amount_minor,
        "currency": plan.currency,
        "display_amount": plan.display_price_for(period),
        # Deprecated compatibility alias. Do not infer USD from this key; use currency.
        "amount_usd": str(amount_value),
        "provider": "paypal",
        "url": option.payment_url if option else None,
        "verification": "manual_or_verified_provider_event",
        "automatic_activation": False,
        "next_status": "active_after_payment_verification",
    }
