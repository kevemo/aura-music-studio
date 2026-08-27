from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .commerce_receipts import CommerceReceiptStore
from .stripe_billing import accounts, credit_packs
from .stripe_billing_hardening import hardened_stripe_webhook

router = APIRouter(tags=["Stripe Commerce Receipts"])


def _credit_transaction_exists(user_id: str, reference: str, credits: int) -> bool:
    with sqlite3.connect(accounts.db_path) as con:
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


@router.post("/billing/stripe/webhook")
async def stripe_webhook_with_commerce_receipt(request: Request):
    """Delegate to the hardened Stripe processor before persisting finance evidence.

    The delegated route performs signature verification, replay/idempotency controls and all
    subscription/credit entitlement validation. Only after it succeeds do we persist the
    amount-bearing top-up receipt. Duplicate Stripe deliveries are useful recovery attempts:
    the local credit transaction proves the original event completed without granting twice.
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
    except ValueError as exc:
        # A retry can repair receipt persistence because the underlying credit transaction
        # and Stripe event evidence are both idempotent. Fail visibly rather than understate
        # owner finance totals.
        raise HTTPException(500, f"Verified payment processed but finance receipt failed: {exc}") from exc

    if receipt is not None:
        result = dict(result)
        result["finance_receipt_recorded"] = True
        result["finance_receipt_id"] = receipt["id"]
    return result


__all__ = ["_record_verified_credit_receipt", "router", "stripe_webhook_with_commerce_receipt"]
