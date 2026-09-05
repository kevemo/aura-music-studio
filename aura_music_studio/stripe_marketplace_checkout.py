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


def _validated_bound_session(session: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless a retrieved Stripe Session still matches the immutable local order."""
    expected_session_id = str(order.get("provider_checkout_reference") or "").strip()
    session_id = str(session.get("id") or "").strip()
    if not expected_session_id.startswith("cs_") or session_id != expected_session_id:
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")
    if str(session.get("mode") or "") != "payment":
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")
    if str(session.get("client_reference_id") or "") != str(order.get("buyer_user_id") or ""):
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")

    metadata = session.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if str(metadata.get("purchase_kind") or "") != "marketplace":
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")
    if str(metadata.get("marketplace_order_id") or "") != str(order.get("id") or ""):
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")

    try:
        amount_total = int(session.get("amount_total"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated") from exc
    if amount_total != int(order.get("gross_minor") or 0):
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")
    if str(session.get("currency") or "").strip().upper() != str(order.get("currency") or "").strip().upper():
        raise RuntimeError("Stored marketplace Checkout binding could not be revalidated")

    url = str(session.get("url") or "").strip()
    if not url.startswith("https://checkout.stripe.com/"):
        raise RuntimeError("Stored marketplace Checkout Session is no longer available")
    return session


def create_stripe_marketplace_session(
    *,
    order: dict[str, Any],
    user: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    """Create or safely recover Checkout from the immutable local order only.

    Stripe metadata contains only the opaque local order id. Price, tenant, publication,
    creator payee and Mary/Kev catalogue provenance remain server-authoritative local facts.
    The deterministic Stripe idempotency key closes the create-session/local-bind crash window;
    an already-bound retry re-fetches and fully revalidates the same provider Session.
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

    existing_reference = str(order.get("provider_checkout_reference") or "").strip()
    if existing_reference:
        if not existing_reference.startswith("cs_") or len(existing_reference) > 128 or not existing_reference.replace("_", "").isalnum():
            raise RuntimeError("Stored marketplace Checkout binding is invalid")
        session = _stripe_get(config, f"/v1/checkout/sessions/{existing_reference}")
        return _validated_bound_session(session, order)

    base = _clean_base_url(config.public_base_url)
    order_id = str(order["id"])
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

    return {
        "provider": "stripe",
        "marketplace_order_id": bound["id"],
        "checkout_session_id": session["id"],
        "checkout_url": session["url"],
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
