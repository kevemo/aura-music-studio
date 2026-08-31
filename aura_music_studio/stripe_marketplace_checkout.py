from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .marketplace_orders import MarketplaceOrderStore
from .stripe_billing import StripeConfig, _clean_base_url, _session_user, accounts
from .stripe_marketplace_fee_evidence import _stripe_get


router = APIRouter(prefix="/billing/stripe", tags=["Stripe Marketplace"])
orders = MarketplaceOrderStore(accounts.db_path)


class MarketplaceCheckoutRequest(BaseModel):
    order_id: str = Field(min_length=8, max_length=128)


def _load_order(store: MarketplaceOrderStore, order_id: str) -> dict[str, Any]:
    """Read the immutable local order snapshot without accepting browser commercial facts."""
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


def _validate_bound_session(session: dict[str, Any], order: dict[str, Any]) -> None:
    """Prove a previously bound Stripe Session still represents this immutable order."""
    expected_session_id = str(order.get("provider_checkout_reference") or "").strip()
    if str(session.get("id") or "").strip() != expected_session_id or not expected_session_id.startswith("cs_"):
        raise ValueError("Stored marketplace Checkout binding does not match Stripe")
    if str(session.get("mode") or "") != "payment":
        raise ValueError("Stored marketplace Checkout Session is not a payment session")
    if str(session.get("client_reference_id") or "") != str(order.get("buyer_user_id") or ""):
        raise ValueError("Stored marketplace Checkout buyer does not match the immutable order")
    metadata = session.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if str(metadata.get("purchase_kind") or "") != "marketplace":
        raise ValueError("Stored Stripe Session is not a marketplace purchase")
    if str(metadata.get("marketplace_order_id") or "") != str(order.get("id") or ""):
        raise ValueError("Stored marketplace Checkout metadata does not match the immutable order")
    amount_total = session.get("amount_total")
    if amount_total is not None and int(amount_total) != int(order.get("gross_minor") or 0):
        raise ValueError("Stored marketplace Checkout amount does not match the immutable order")
    currency = str(session.get("currency") or "").strip().upper()
    if currency and currency != str(order.get("currency") or "").strip().upper():
        raise ValueError("Stored marketplace Checkout currency does not match the immutable order")


def create_stripe_marketplace_session(
    *,
    order: dict[str, Any],
    user: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    """Create or safely recover Checkout from the immutable order only.

    Stripe metadata contains only the opaque local order id. Price, tenant, publication,
    creator payee and Mary/Kev catalogue provenance remain server-authoritative local facts.
    The deterministic Stripe idempotency key closes the provider-create/local-bind crash window,
    while an existing binding is retrieved and revalidated instead of creating a second Session.
    """
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
        _validate_bound_session(session, order)
        return session

    data = {
        "mode": "payment",
        "client_reference_id": str(order["buyer_user_id"]),
        "customer_email": str(user.get("email") or ""),
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(gross_minor),
        "line_items[0][price_data][product_data][name]": "Elevate Souls Productions marketplace purchase",
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
        raise RuntimeError(f"Stripe request failed: {type(exc).__name__}") from exc
    try:
        body = response.json()
    except Exception as exc:
        raise RuntimeError("Stripe returned a non-JSON marketplace checkout response") from exc
    if response.status_code >= 400:
        message = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None) or "Stripe rejected marketplace checkout"
        raise RuntimeError(str(message)[:300])
    if not isinstance(body, dict):
        raise RuntimeError("Stripe returned an invalid marketplace checkout response")
    session_id = str(body.get("id") or "").strip()
    url = str(body.get("url") or "").strip()
    if not session_id.startswith("cs_") or not url.startswith("https://checkout.stripe.com/"):
        raise RuntimeError("Stripe did not return a trusted marketplace Checkout session")
    return body


@router.post("/checkout/marketplace")
def create_marketplace_checkout(body: MarketplaceCheckoutRequest, request: Request):
    user = _session_user(request)
    if user.get("status") != "active":
        raise HTTPException(403, "Active account required for marketplace checkout")
    try:
        order = _load_order(orders, body.order_id)
        session = create_stripe_marketplace_session(
            order=order,
            user=user,
            config=StripeConfig.from_env(),
        )
        bound = orders.bind_provider_checkout(
            order_id=str(order["id"]),
            provider_checkout_reference=str(session["id"]),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    url = str(session.get("url") or "").strip()
    if not url.startswith("https://checkout.stripe.com/"):
        raise HTTPException(409, "Marketplace Checkout Session is no longer available for browser checkout")
    return {
        "provider": "stripe",
        "marketplace_order_id": bound["id"],
        "checkout_session_id": session["id"],
        "checkout_url": url,
        "automatic_settlement_from_redirect": False,
        "settlement_source": "verified_stripe_provider_evidence",
        "subscription_effect": "none",
        "creation_coin_effect": "none",
        "esp_role_effect": "none",
    }


__all__ = [
    "MarketplaceCheckoutRequest",
    "create_marketplace_checkout",
    "create_stripe_marketplace_session",
    "router",
]
