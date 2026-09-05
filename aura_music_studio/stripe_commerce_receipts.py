from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .commerce_receipts import CommerceReceiptStore
from .payment_reversals import PaymentReversalStore
from .stripe_billing import accounts, credit_packs
from .stripe_billing_hardening import hardened_stripe_webhook

router = APIRouter(tags=["Stripe Commerce Receipts"])
reversals = PaymentReversalStore(accounts.db_path)


def _credit_transaction_exists(user_id: str, reference: str, credits: int) -> bool:
    with sqlite3.connect(accounts.db_path) as con:
        table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credit_transactions'"
        ).fetchone()
        if not table:
            # A missing credit ledger means there cannot be verified local purchase evidence.
            # Treat this as a clean negative result so receipt creation fails closed with the
            # domain-specific validation error rather than leaking a SQLite migration error.
            return False
        row = con.execute(
            """SELECT id FROM credit_transactions
               WHERE user_id=? AND reference=? AND kind='purchase' AND amount=?""",
            (user_id, reference, int(credits)),
        ).fetchone()
    return row is not None


def _provider_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:180]
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()[:180]
    return ""


def _event_object(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    obj = (data or {}).get("object") if isinstance(data, dict) else None
    return obj if isinstance(obj, dict) else {}


def _record_verified_credit_receipt(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("type") or "") != "checkout.session.completed":
        return None
    obj = _event_object(event)
    if not obj:
        return None
    metadata = obj.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if str(metadata.get("purchase_kind") or "") != "credit_topup":
        return None

    user_id = str(metadata.get("user_id") or obj.get("client_reference_id") or "").strip()
    pack_id = str(metadata.get("credit_pack_id") or "").strip()
    session_id = str(obj.get("id") or "").strip()
    pack = credit_packs().get(pack_id)
    if not user_id or not session_id or pack is None:
        raise ValueError("Verified Stripe credit event is missing its local purchase binding")
    if str(obj.get("payment_status") or "") != "paid":
        raise ValueError("Stripe credit receipt cannot be recorded for an unpaid Checkout Session")
    if int(obj.get("amount_total") or -1) != pack.amount_minor:
        raise ValueError("Stripe credit receipt amount does not match the configured pack")
    if str(obj.get("currency") or "").upper() != pack.currency:
        raise ValueError("Stripe credit receipt currency does not match the configured pack")

    reference = f"stripe:checkout:{session_id}"
    if not _credit_transaction_exists(user_id, reference, pack.credits):
        raise ValueError("Stripe credit receipt has no matching verified local credit purchase")

    return CommerceReceiptStore(accounts.db_path).record(
        provider="stripe",
        kind="credit_topup",
        reference=reference,
        user_id=user_id,
        pack_id=pack.id,
        amount_minor=pack.amount_minor,
        currency=pack.currency,
        units=pack.credits,
        status="paid",
        metadata={"stripe_event_id": str(event.get("id") or "")[:180]},
    )


def _bind_credit_refund_correlation(
    event: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any] | None:
    obj = _event_object(event)
    payment_intent_id = _provider_id(obj.get("payment_intent"))
    if not payment_intent_id:
        # The paid receipt remains valid finance evidence even if Stripe did not include a
        # PaymentIntent reference in this delivery. Any later refund will remain explicitly
        # unmatched rather than being guessed onto a member purchase.
        return None
    return reversals.bind_credit_payment(
        payment_intent_id=payment_intent_id,
        receipt_reference=str(receipt["reference"]),
        user_id=str(receipt["user_id"]),
        amount_minor=int(receipt["amount_minor"]),
        currency=str(receipt["currency"]),
    )


@router.post("/billing/stripe/webhook")
async def stripe_webhook_with_commerce_receipt(request: Request):
    """Persist finance evidence only after the hardened Stripe processor succeeds.

    The delegated route performs signature verification, replay/idempotency controls and all
    subscription/Creation Coin entitlement validation. Duplicate Stripe deliveries remain useful
    repair attempts: already-created local purchases and append-only finance rows make the outer
    receipt/refund layer idempotent without granting Coins twice.
    """
    result = await hardened_stripe_webhook(request)
    if not isinstance(result, dict):
        return result

    raw = await request.body()
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # This should already have been rejected by the delegated verified handler.
        raise HTTPException(400, "Invalid Stripe webhook JSON") from exc
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid Stripe webhook event")

    try:
        receipt = _record_verified_credit_receipt(event)
        correlation = _bind_credit_refund_correlation(event, receipt) if receipt is not None else None
        refund = reversals.record_stripe_refund(event)
    except (TypeError, ValueError) as exc:
        # Stripe can retry a signed event. Existing entitlement, receipt and adjustment ledgers are
        # all idempotent, so failing visibly is safer than silently understating owner finance.
        raise HTTPException(500, f"Verified payment processed but finance evidence failed: {exc}") from exc

    output = dict(result)
    if receipt is not None:
        output["finance_receipt_recorded"] = True
        output["finance_receipt_id"] = receipt["id"]
        output["refund_correlation_recorded"] = correlation is not None
    if refund is not None:
        output["finance_refund_recorded"] = True
        output["finance_refund_linked"] = bool(refund.get("linked"))
        output["finance_reconciliation_required"] = not bool(refund.get("linked"))
        output["wallet_effect"] = "none"
        output["subscription_effect"] = "none"
        output["esp_role_effect"] = "none"
    return output


__all__ = [
    "_bind_credit_refund_correlation",
    "_record_verified_credit_receipt",
    "reversals",
    "router",
    "stripe_webhook_with_commerce_receipt",
]
