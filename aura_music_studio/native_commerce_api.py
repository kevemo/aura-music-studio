from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .accounts import AccountStore
from .native_billing import NativeEntitlementLedger
from .native_paypal import (
    NativePayPalError,
    NativePayPalInvoiceCreator,
    NativePayPalPaymentVerifier,
)
from .native_paypal_lifecycle import NativePayPalLifecycleVerifier
from .native_products import BillingPeriod, get_native_product

router = APIRouter()

_MAX_WEBHOOK_BYTES = 1_000_000
_PAYMENT_EVENT = "INVOICING.INVOICE.PAID"
_LIFECYCLE_EVENTS = {
    "INVOICING.INVOICE.CANCELLED",
    "INVOICING.INVOICE.REFUNDED",
}
_MEMBER_COOKIE = "lss_session"

_store = AccountStore()
native_entitlements = NativeEntitlementLedger(_store.db_path)


class NativeCheckoutRequest(BaseModel):
    """User-selectable native checkout fields only; money and identity are server-owned."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    billing_period: BillingPeriod
    founding_offer: bool = False


def _payment_verifier() -> NativePayPalPaymentVerifier:
    # Construct lazily so production fails closed on a missing native metadata secret
    # when the payment boundary is actually invoked, without breaking unrelated startup.
    return NativePayPalPaymentVerifier()


def _lifecycle_verifier() -> NativePayPalLifecycleVerifier:
    return NativePayPalLifecycleVerifier()


def _checkout_creator() -> NativePayPalInvoiceCreator:
    # Checkout configuration is deliberately lazy for the same reason as webhook verification:
    # unrelated site startup remains available, while the native commerce boundary fails closed.
    return NativePayPalInvoiceCreator()


def _checkout_member(request: Request) -> dict:
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization[7:].strip()
        if authorization.lower().startswith("bearer ")
        else request.cookies.get(_MEMBER_COOKIE)
    )
    member = _store.resolve_session(token)
    if not member:
        raise HTTPException(401, "Authenticated member session required")
    if str(member.get("status") or "").strip().lower() not in {"active", "owner"}:
        raise HTTPException(403, "An active Command Center account is required for native checkout")
    if not str(member.get("email") or "").strip():
        raise HTTPException(409, "The member account requires a billing email before native checkout")
    return member


def _duplicate_event(exc: ValueError) -> bool:
    message = str(exc).lower()
    return "already been processed" in message


@router.post("/billing/native/paypal/checkout")
def native_paypal_checkout(payload: NativeCheckoutRequest, request: Request):
    """Create and send a canonical native-product PayPal invoice for the signed-in member.

    Browser input may select only the product, billing period and explicit founding-offer flag.
    User identity and billing email are recovered from the authenticated account. Product name,
    currency and amount are recomputed from ``native_products.py`` inside the invoice creator.
    Payment never activates access here: only the verified PayPal webhook can mutate entitlement.
    """

    member = _checkout_member(request)
    try:
        product = get_native_product(payload.product_id)
        # Validate period/offer compatibility before creating any provider resource.
        product.price_minor_for(payload.billing_period, founding_offer=payload.founding_offer)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if payload.founding_offer and native_entitlements.has_product_purchase(member["id"], product.id):
        raise HTTPException(409, "Founding pricing is only available for the first product term")

    try:
        creator = _checkout_creator()
    except NativePayPalError as exc:
        raise HTTPException(503, "Native PayPal checkout is not configured") from exc

    try:
        invoice = creator.create_and_send(
            user_id=member["id"],
            billing_email=member["email"],
            product_id=product.id,
            billing_period=payload.billing_period,
            founding_offer=payload.founding_offer,
        )
    except NativePayPalError as exc:
        message = str(exc)
        status_code = 502 if "failed" in message.lower() else 400
        raise HTTPException(status_code, message) from exc

    return {
        "created": True,
        "payment_state": "awaiting_provider_payment",
        **invoice,
    }


@router.post("/billing/native/paypal/webhook", include_in_schema=False)
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


def _compose_into_hardened_billing_router() -> None:
    """Attach native commerce before the production app mounts its hardened billing router.

    The production entrypoint performs extensive late router composition. Native commerce is
    deliberately attached to the already-reliable hardened billing router at module import time,
    before that billing router is copied into the FastAPI application. A private marker makes the
    operation idempotent across repeated imports/reloads and prevents duplicate payment routes.
    """

    from .stripe_billing_hardening import router as hardened_billing_router

    marker = "_native_commerce_router_composed"
    if getattr(hardened_billing_router, marker, False):
        return
    hardened_billing_router.include_router(router)
    setattr(hardened_billing_router, marker, True)


_compose_into_hardened_billing_router()
