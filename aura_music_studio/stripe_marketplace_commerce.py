from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .marketplace_orders import MarketplaceOrderStore
from .marketplace_settlement import MarketplaceSettlementStore
from .stripe_billing import StripeConfig, _clean_base_url, _session_user, accounts
from .stripe_marketplace_fee_evidence import (
    StripeMarketplaceFeeEvidenceStore,
    _stripe_get,
    verify_and_record_stripe_marketplace_settlement,
)
from .stripe_marketplace_refund_evidence import (
    StripeMarketplaceRefundEvidenceStore,
    verify_and_record_stripe_marketplace_refund,
)


router = APIRouter(tags=["Stripe Marketplace Commerce"])
orders = MarketplaceOrderStore(accounts.db_path)
settlements = MarketplaceSettlementStore(accounts.db_path)
fee_evidence = StripeMarketplaceFeeEvidenceStore(accounts.db_path)
refund_evidence = StripeMarketplaceRefundEvidenceStore(accounts.db_path)


class MarketplaceCheckoutRequest(BaseModel):
    order_id: str = Field(min_length=8, max_length=128)


def _load_order(store: MarketplaceOrderStore, order_id: str) -> dict[str, Any]:
    order_id = (order_id or "").strip()
    if not order_id:
        raise ValueError("Marketplace checkout requires an order id")
    con = sqlite3.connect(Path(store.db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM marketplace_orders WHERE id=?", (order_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("Unknown marketplace order")
    return dict(row)


def _checkout_idempotency_key(order_id: str) -> str:
    digest = hashlib.sha256(order_id.encode("utf-8")).hexdigest()
    return f"esp-marketplace-checkout-{digest}"


def _validate_checkout_identity(session: dict[str, Any], order: dict[str, Any]) -> str:
    session_id = str(session.get("id") or "").strip()
    if not session_id.startswith("cs_"):
        raise ValueError("Stripe returned an invalid marketplace Checkout Session id")
    if str(session.get("mode") or "") != "payment":
        raise ValueError("Stripe marketplace Checkout Session is not a payment session")
    if str(session.get("client_reference_id") or "") != str(order["buyer_user_id"]):
        raise ValueError("Stripe marketplace Checkout buyer does not match the immutable order")
    metadata = session.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if str(metadata.get("purchase_kind") or "") != "marketplace":
        raise ValueError("Stripe Checkout Session is not marked as a marketplace purchase")
    if str(metadata.get("marketplace_order_id") or "") != str(order["id"]):
        raise ValueError("Stripe marketplace Checkout metadata does not match the immutable order")
    amount_total = session.get("amount_total")
    if amount_total is not None and int(amount_total) != int(order["gross_minor"]):
        raise ValueError("Stripe marketplace Checkout amount does not match the immutable order")
    currency = str(session.get("currency") or "").strip().upper()
    if currency and currency != str(order["currency"]):
        raise ValueError("Stripe marketplace Checkout currency does not match the immutable order")
    return session_id


def _create_stripe_checkout_session(
    *,
    order: dict[str, Any],
    user: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    if str(order.get("provider") or "").lower() != "stripe":
        raise ValueError("Marketplace order is not assigned to Stripe")
    if str(order.get("buyer_user_id") or "") != str(user.get("id") or ""):
        raise PermissionError("Marketplace order belongs to a different buyer")
    gross_minor = int(order.get("gross_minor") or 0)
    currency = str(order.get("currency") or "").strip().lower()
    if gross_minor <= 0 or len(currency) != 3 or not currency.isalpha():
        raise ValueError("Marketplace order has invalid immutable price data")
    if not config.secret_key:
        raise RuntimeError("Stripe secret key is not configured")
    base = _clean_base_url(config.public_base_url)
    order_id = str(order["id"])

    existing_reference = str(order.get("provider_checkout_reference") or "").strip()
    if existing_reference:
        session = _stripe_get(config, f"/v1/checkout/sessions/{existing_reference}")
        if _validate_checkout_identity(session, order) != existing_reference:
            raise ValueError("Stored marketplace Checkout binding does not match Stripe")
        return session

    data = {
        "mode": "payment",
        "client_reference_id": str(order["buyer_user_id"]),
        "customer_email": str(user.get("email") or ""),
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(gross_minor),
        "line_items[0][price_data][product_data][name]": "Elevate Souls marketplace purchase",
        "line_items[0][quantity]": "1",
        "success_url": f"{base}/billing/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/dashboard?marketplace=cancelled",
        "metadata[purchase_kind]": "marketplace",
        "metadata[marketplace_order_id]": order_id,
    }
    try:
        response = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=data,
            headers={
                "Authorization": f"Bearer {config.secret_key}",
                "Idempotency-Key": _checkout_idempotency_key(order_id),
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Stripe marketplace checkout request failed: {type(exc).__name__}") from exc
    try:
        session = response.json()
    except Exception as exc:
        raise RuntimeError("Stripe returned a non-JSON marketplace checkout response") from exc
    if response.status_code >= 400:
        message = ((session.get("error") or {}).get("message") if isinstance(session, dict) else None) or "Stripe rejected marketplace checkout"
        raise RuntimeError(str(message)[:300])
    if not isinstance(session, dict):
        raise RuntimeError("Stripe returned an invalid marketplace checkout response")
    session_id = str(session.get("id") or "").strip()
    if not session_id.startswith("cs_"):
        raise RuntimeError("Stripe did not return a trusted marketplace Checkout Session")
    return session


def create_marketplace_checkout_session(
    *,
    store: MarketplaceOrderStore,
    order_id: str,
    user: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    order = _load_order(store, order_id)
    session = _create_stripe_checkout_session(order=order, user=user, config=config)
    session_id = str(session.get("id") or "").strip()
    bound = store.bind_provider_checkout(order_id=str(order["id"]), provider_checkout_reference=session_id)
    url = str(session.get("url") or "").strip()
    if not url.startswith("https://checkout.stripe.com/"):
        raise RuntimeError("Stripe Checkout Session is no longer available for browser checkout")
    return {
        "provider": "stripe",
        "marketplace_order_id": str(bound["id"]),
        "checkout_session_id": session_id,
        "checkout_url": url,
        "automatic_settlement_from_redirect": False,
        "settlement_source": "signed_webhook_plus_verified_stripe_provider_evidence",
        "subscription_effect": "none",
        "creation_coin_effect": "none",
        "esp_role_effect": "none",
    }


@router.post("/billing/stripe/checkout/marketplace")
def marketplace_checkout(body: MarketplaceCheckoutRequest, request: Request):
    user = _session_user(request)
    if user.get("status") != "active":
        raise HTTPException(403, "Active account required for marketplace checkout")
    config = StripeConfig.from_env()
    if not config.secret_key or not config.public_base_url:
        raise HTTPException(503, "Stripe marketplace checkout is not configured")
    try:
        return create_marketplace_checkout_session(
            store=orders,
            order_id=body.order_id,
            user=user,
            config=config,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


def _checkout_reference_is_marketplace(checkout_session_id: str) -> bool:
    checkout_session_id = (checkout_session_id or "").strip()
    if not checkout_session_id:
        return False
    con = sqlite3.connect(Path(orders.db_path))
    try:
        row = con.execute(
            """SELECT 1 FROM marketplace_orders
               WHERE provider='stripe' AND provider_checkout_reference=? LIMIT 1""",
            (checkout_session_id,),
        ).fetchone()
    finally:
        con.close()
    return bool(row)


def _refund_targets_verified_marketplace(
    refund: dict[str, Any],
    config: StripeConfig | None = None,
) -> bool:
    """Recognize marketplace Refunds even when Stripe webhooks arrive out of order.

    The fast path uses already-persisted fee evidence. If the refund event arrives before the
    Checkout completion event has been processed, Stripe's Checkout Session list endpoint can
    resolve the PaymentIntent back to its Session; the Session must then match a locally bound
    marketplace order before this event is admitted to marketplace processing.
    """
    payment_intent_id = str(refund.get("payment_intent") or "").strip()
    charge_id = str(refund.get("charge") or "").strip()
    if not payment_intent_id:
        return False

    if charge_id:
        con = sqlite3.connect(Path(fee_evidence.db_path))
        try:
            row = con.execute(
                """SELECT 1 FROM stripe_marketplace_fee_evidence
                   WHERE payment_intent_id=? AND charge_id=? LIMIT 1""",
                (payment_intent_id, charge_id),
            ).fetchone()
        finally:
            con.close()
        if row:
            return True

    if config is None:
        return False
    encoded_payment_intent = quote(payment_intent_id, safe="")
    sessions = _stripe_get(
        config,
        f"/v1/checkout/sessions?payment_intent={encoded_payment_intent}&limit=2",
    )
    data = sessions.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Stripe returned an invalid Checkout Session list for refund evidence")
    for session in data:
        if not isinstance(session, dict):
            continue
        session_id = str(session.get("id") or "").strip()
        if session_id.startswith("cs_") and _checkout_reference_is_marketplace(session_id):
            return True
    return False


def is_marketplace_stripe_event(
    event_type: str,
    obj: dict[str, Any],
    config: StripeConfig | None = None,
) -> bool:
    if not isinstance(obj, dict):
        return False
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        metadata = obj.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return str(metadata.get("purchase_kind") or "") == "marketplace"
    if event_type in {"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"}:
        return _refund_targets_verified_marketplace(obj, config=config)
    return False


def process_verified_marketplace_stripe_event(
    *,
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    """Process one already-signature-verified Stripe marketplace event.

    Event metadata can identify the opaque local order only. All commercial settlement facts are
    re-established from the immutable local order plus canonical Stripe provider evidence.
    """
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        if str(obj.get("payment_status") or "") != "paid":
            return {
                "processed": False,
                "ignored": True,
                "kind": "marketplace_payment",
                "reason": "marketplace_checkout_not_paid_yet",
            }
        result = verify_and_record_stripe_marketplace_settlement(
            event_id=event_id,
            checkout_session=obj,
            orders=orders,
            settlements=settlements,
            fee_evidence=fee_evidence,
            config=config,
        )
        return {"processed": True, "kind": "marketplace_payment", **result}

    if event_type in {"refund.created", "refund.updated", "charge.refund.updated"}:
        if str(obj.get("status") or "") != "succeeded":
            return {
                "processed": False,
                "ignored": True,
                "kind": "marketplace_refund",
                "reason": f"marketplace_refund_status:{str(obj.get('status') or 'unknown')}",
            }
        result = verify_and_record_stripe_marketplace_refund(
            event_id=event_id,
            refund_event_object=obj,
            fee_evidence=fee_evidence,
            refund_evidence=refund_evidence,
            settlements=settlements,
            config=config,
        )
        return {"processed": True, "kind": "marketplace_refund", **result}

    if event_type == "refund.failed":
        return {
            "processed": False,
            "ignored": True,
            "kind": "marketplace_refund",
            "reason": "marketplace_refund_failed",
        }
    raise ValueError("Unsupported Stripe marketplace event type")


__all__ = [
    "MarketplaceCheckoutRequest",
    "create_marketplace_checkout_session",
    "fee_evidence",
    "is_marketplace_stripe_event",
    "marketplace_checkout",
    "orders",
    "process_verified_marketplace_stripe_event",
    "refund_evidence",
    "router",
    "settlements",
]
