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


def _record_verified_credit_receipt(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("type") or "") != "checkout.session.completed":
        return None
    data = event.get("data")
    obj = (data or {}).get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict):
        return None
    metadata = obj.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if str(metadata.get("purchase_kind") or "") != "credit_topup":
        return None

    user_id = str(metadata.get("user_id") or obj.get("client_reference_id") or "").strip()
    pack_id = str(metadata.get("credit_pack_id") or "").strip()
    session_id = str(obj.get("id") or "").strip()
    payment_intent_id = str(obj.get("payment_intent") or "").strip()
    pack = credit_packs().get(pack_id)
    if not user_id or not session_id or pack is None:
        raise ValueError("Verified Stripe credit event is missing its local purchase binding")
    if not payment_intent_id:
        raise ValueError("Verified Stripe credit event is missing its PaymentIntent correlation")
    if str(obj.get("payment_status") or "") != "paid":
        raise ValueError("Stripe credit receipt cannot be recorded for an unpaid Checkout Session")
    if int(obj.get("amount_total") or -1) != pack.amount_minor:
        raise ValueError("Stripe credit receipt amount does not match the configured pack")
    if str(obj.get("currency") or "").upper() != pack.currency:
        raise ValueError("Stripe credit receipt currency does not match the configured pack")

    reference = f"stripe:checkout:{session_id}"
    if not _credit_transaction_exists(user_id, reference, pack.credits):
        raise ValueError("Stripe credit receipt has no matching verified local credit purchase")

    receipt = CommerceReceiptStore(accounts.db_path).record(
        provider="stripe",
        kind="credit_topup",
        reference=reference,
        user_id=user_id,
        pack_id=pack.id,
        amount_minor=pack.amount_minor,
        currency=pack.currency,
        units=pack.credits,
        status="paid",
        metadata={
            "stripe_event_id": str(event.get("id") or "")[:180],
            "stripe_payment_intent_id": payment_intent_id[:180],
        },
    )
    PaymentReversalStore(accounts.db_path).bind_credit_payment(
        payment_intent_id=payment_intent_id,
        receipt_reference=reference,
        user_id=user_id,
        amount_minor=pack.amount_minor,
        currency=pack.currency,
    )
    return receipt


@router.post("/billing/stripe/webhook")
async def stripe_webhook_with_commerce_receipt(request: Request):
    """Delegate through Stripe verification before persisting finance evidence.

    The hardened/base Stripe handlers remain authoritative for signatures, replay protection,
    subscription activation and credit grants. This overlay only records amount-bearing finance
    evidence after that verified path succeeds. Successful refund events can reduce owner net
    receipt reporting, but cannot mutate membership, ESP roles or wallet balances.
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
        refund = PaymentReversalStore(accounts.db_path).record_stripe_refund(event)
    except ValueError as exc:
        # Stripe retries can repair an interrupted finance-evidence write because both the base
        # event ledger and these finance records are idempotent. Fail visibly instead of silently
        # overstating owner finance totals.
        raise HTTPException(500, f"Verified Stripe event processed but finance evidence failed: {exc}") from exc

    response = dict(result)
    if receipt is not None:
        response["finance_receipt_recorded"] = True
        response["finance_receipt_id"] = receipt["id"]
    if refund is not None:
        response["finance_refund_evidence"] = True
        response["finance_refund_linked"] = bool(refund.get("linked"))
        response["finance_refund_status"] = refund.get("status")
        if refund.get("linked"):
            response["finance_adjustment_id"] = refund.get("id")
    return response


__all__ = ["_record_verified_credit_receipt", "router", "stripe_webhook_with_commerce_receipt"]
