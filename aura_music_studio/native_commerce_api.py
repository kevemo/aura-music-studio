from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .native_billing import NativeEntitlementLedger
from .native_paypal import NativePayPalError, NativePayPalPaymentVerifier
from .native_paypal_lifecycle import NativePayPalLifecycleVerifier

router = APIRouter()

_MAX_WEBHOOK_BYTES = 1_000_000
_PAYMENT_EVENT = "INVOICING.INVOICE.PAID"
_LIFECYCLE_EVENTS = {
    "INVOICING.INVOICE.CANCELLED",
    "INVOICING.INVOICE.REFUNDED",
}

_store = AccountStore()
native_entitlements = NativeEntitlementLedger(_store.db_path)


def _payment_verifier() -> NativePayPalPaymentVerifier:
    # Construct lazily so production fails closed on a missing native metadata secret
    # when the payment boundary is actually invoked, without breaking unrelated startup.
    return NativePayPalPaymentVerifier()


def _lifecycle_verifier() -> NativePayPalLifecycleVerifier:
    return NativePayPalLifecycleVerifier()


def _duplicate_event(exc: ValueError) -> bool:
    message = str(exc).lower()
    return "already been processed" in message


@router.post("/billing/native/paypal/webhook")
async def native_paypal_webhook(request: Request):
    """Accept only provider-authenticated native-product PayPal lifecycle evidence.

    The route never trusts browser return state, client prices, user IDs or product IDs.
    It inspects the untrusted event type only to select the strict verifier; that verifier
    then authenticates the PayPal transmission and independently re-loads the exact
    authoritative invoice before the entitlement ledger can mutate native access.
    """

    raw = await request.body()
    if not raw:
        raise HTTPException(400, "PayPal webhook body is required")
    if len(raw) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "PayPal webhook body exceeds the accepted size")

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "PayPal webhook payload is invalid JSON") from exc
    if not isinstance(envelope, dict):
        raise HTTPException(400, "PayPal webhook payload must be an object")

    event_type = str(envelope.get("event_type") or "")
    headers = {str(key): str(value) for key, value in request.headers.items()}

    try:
        if event_type == _PAYMENT_EVENT:
            receipt = native_entitlements.process_verified_event(
                raw_event=raw,
                headers=headers,
                verifier=_payment_verifier(),
            )
            return {
                "accepted": True,
                "kind": "payment",
                "event_id": receipt.event_id,
                "product_id": receipt.product_id,
            }

        if event_type in _LIFECYCLE_EVENTS:
            receipt = native_entitlements.process_verified_lifecycle_event(
                raw_event=raw,
                headers=headers,
                verifier=_lifecycle_verifier(),
            )
            return {
                "accepted": True,
                "kind": receipt.event_type,
                "event_id": receipt.event_id,
                "product_id": receipt.product_id,
            }

        raise HTTPException(400, "Unsupported PayPal native-product event type")
    except HTTPException:
        raise
    except NativePayPalError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        # Provider retries are normal. A previously committed event is acknowledged so
        # PayPal does not repeatedly redeliver it; all other ledger validation failures
        # remain fail-closed and visible to the provider as rejected input.
        if _duplicate_event(exc):
            return {"accepted": True, "duplicate": True}
        raise HTTPException(400, str(exc)) from exc
