from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .marketplace_orders import MarketplaceOrderStore, record_verified_marketplace_payment
from .marketplace_settlement import MarketplaceSettlementStore
from .stripe_billing import StripeConfig


_STRIPE_API = "https://api.stripe.com"
_STRIPE_ID_RE = re.compile(r"^[A-Za-z0-9_]{3,128}$")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stripe_id(value: Any, *, prefix: str, field: str) -> str:
    result = str(value or "").strip()
    if not result.startswith(prefix) or not _STRIPE_ID_RE.fullmatch(result):
        raise ValueError(f"Stripe marketplace evidence has an invalid {field}")
    return result


def _minor(value: Any, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Stripe marketplace evidence has an invalid {field}") from exc
    if result < 0:
        raise ValueError(f"Stripe marketplace evidence has a negative {field}")
    return result


def _currency(value: Any) -> str:
    result = str(value or "").strip().upper()
    if len(result) != 3 or not result.isalpha():
        raise ValueError("Stripe marketplace evidence has an invalid currency")
    return result


def _stripe_get(config: StripeConfig, path: str) -> dict[str, Any]:
    if not config.secret_key:
        raise RuntimeError("Stripe secret key is not configured")
    try:
        response = httpx.get(
            f"{_STRIPE_API}{path}",
            headers={"Authorization": f"Bearer {config.secret_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Stripe marketplace evidence request failed: {type(exc).__name__}") from exc
    try:
        body = response.json()
    except Exception as exc:
        raise RuntimeError("Stripe returned non-JSON marketplace evidence") from exc
    if response.status_code >= 400:
        message = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None) or "Stripe rejected marketplace evidence retrieval"
        raise RuntimeError(str(message)[:300])
    if not isinstance(body, dict):
        raise RuntimeError("Stripe returned invalid marketplace evidence")
    return body


def _bound_order(store: MarketplaceOrderStore, *, provider: str, checkout_reference: str) -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    checkout_reference = (checkout_reference or "").strip()
    if not provider or not checkout_reference:
        raise ValueError("Marketplace provider checkout binding is required")
    con = sqlite3.connect(Path(store.db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """SELECT * FROM marketplace_orders
               WHERE provider=? AND provider_checkout_reference=?""",
            (provider, checkout_reference),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("Stripe Checkout Session has no immutable marketplace order binding")
    return dict(row)


class StripeMarketplaceFeeEvidenceStore:
    """Durable provider-reference evidence for marketplace fee/net settlement.

    No Stripe secret, customer email, payment method, card data, prompt, media or private tenant
    payload is persisted here. Only immutable provider object references and verified accounting
    facts required to reproduce the settlement decision are retained.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS stripe_marketplace_fee_evidence (
                    checkout_session_id TEXT PRIMARY KEY,
                    first_verified_event_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    payment_intent_id TEXT NOT NULL UNIQUE,
                    charge_id TEXT NOT NULL UNIQUE,
                    balance_transaction_id TEXT NOT NULL UNIQUE,
                    gross_minor INTEGER NOT NULL CHECK(gross_minor > 0),
                    provider_fee_minor INTEGER NOT NULL CHECK(provider_fee_minor >= 0),
                    net_minor INTEGER NOT NULL CHECK(net_minor >= 0),
                    currency TEXT NOT NULL,
                    verified_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_marketplace_fee_order
                    ON stripe_marketplace_fee_evidence(order_id, verified_at DESC);
                """
            )

    def by_checkout(self, checkout_session_id: str) -> dict[str, Any] | None:
        checkout_session_id = (checkout_session_id or "").strip()
        if not checkout_session_id:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_marketplace_fee_evidence WHERE checkout_session_id=?",
                (checkout_session_id,),
            ).fetchone()
        return dict(row) if row else None

    def record(
        self,
        *,
        event_id: str,
        checkout_session_id: str,
        order_id: str,
        payment_intent_id: str,
        charge_id: str,
        balance_transaction_id: str,
        gross_minor: int,
        provider_fee_minor: int,
        net_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        event_id = _stripe_id(event_id, prefix="evt_", field="event id")
        checkout_session_id = _stripe_id(checkout_session_id, prefix="cs_", field="Checkout Session id")
        payment_intent_id = _stripe_id(payment_intent_id, prefix="pi_", field="PaymentIntent id")
        charge_id = _stripe_id(charge_id, prefix="ch_", field="Charge id")
        balance_transaction_id = _stripe_id(balance_transaction_id, prefix="txn_", field="Balance Transaction id")
        order_id = (order_id or "").strip()
        if not order_id or len(order_id) > 128:
            raise ValueError("Stripe marketplace evidence has an invalid local order id")
        gross_minor = _minor(gross_minor, field="gross amount")
        provider_fee_minor = _minor(provider_fee_minor, field="provider fee")
        net_minor = _minor(net_minor, field="net amount")
        currency = _currency(currency)
        if gross_minor <= 0 or provider_fee_minor > gross_minor or net_minor != gross_minor - provider_fee_minor:
            raise ValueError("Stripe marketplace fee/net evidence is internally inconsistent")

        expected = {
            "checkout_session_id": checkout_session_id,
            "order_id": order_id,
            "payment_intent_id": payment_intent_id,
            "charge_id": charge_id,
            "balance_transaction_id": balance_transaction_id,
            "gross_minor": gross_minor,
            "provider_fee_minor": provider_fee_minor,
            "net_minor": net_minor,
            "currency": currency,
        }
        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM stripe_marketplace_fee_evidence WHERE checkout_session_id=?",
                (checkout_session_id,),
            ).fetchone()
            if existing:
                row = dict(existing)
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Stripe Checkout Session fee evidence changed after verification")
                return row
            try:
                con.execute(
                    """INSERT INTO stripe_marketplace_fee_evidence
                       (checkout_session_id,first_verified_event_id,order_id,payment_intent_id,
                        charge_id,balance_transaction_id,gross_minor,provider_fee_minor,net_minor,
                        currency,verified_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        checkout_session_id,
                        event_id,
                        order_id,
                        payment_intent_id,
                        charge_id,
                        balance_transaction_id,
                        gross_minor,
                        provider_fee_minor,
                        net_minor,
                        currency,
                        _iso(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Stripe marketplace provider references are already bound to different evidence") from exc
            row = con.execute(
                "SELECT * FROM stripe_marketplace_fee_evidence WHERE checkout_session_id=?",
                (checkout_session_id,),
            ).fetchone()
        return dict(row)


def _validate_checkout_against_order(checkout_session: dict[str, Any], order: dict[str, Any]) -> tuple[str, str]:
    session_id = _stripe_id(checkout_session.get("id"), prefix="cs_", field="Checkout Session id")
    if str(checkout_session.get("mode") or "") != "payment":
        raise ValueError("Stripe marketplace Checkout Session is not a one-time payment")
    if str(checkout_session.get("payment_status") or "") != "paid":
        raise ValueError("Stripe marketplace Checkout Session is not paid")
    metadata = checkout_session.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if str(metadata.get("purchase_kind") or "") != "marketplace":
        raise ValueError("Stripe Checkout Session is not marked as a marketplace purchase")
    if str(metadata.get("marketplace_order_id") or "") != str(order["id"]):
        raise ValueError("Stripe marketplace metadata does not match the immutable local order")
    if str(checkout_session.get("client_reference_id") or "") != str(order["buyer_user_id"]):
        raise ValueError("Stripe marketplace buyer does not match the immutable local order")
    if _minor(checkout_session.get("amount_total"), field="Checkout gross amount") != int(order["gross_minor"]):
        raise ValueError("Stripe marketplace gross amount does not match the immutable local order")
    if _currency(checkout_session.get("currency")) != str(order["currency"]):
        raise ValueError("Stripe marketplace currency does not match the immutable local order")
    payment_intent_id = _stripe_id(checkout_session.get("payment_intent"), prefix="pi_", field="PaymentIntent id")
    return session_id, payment_intent_id


def fetch_verified_stripe_fee_evidence(
    *,
    checkout_session: dict[str, Any],
    order: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    """Resolve Stripe's server-side payment chain and prove exact marketplace fee/net evidence."""

    session_id, payment_intent_id = _validate_checkout_against_order(checkout_session, order)
    gross_minor = int(order["gross_minor"])
    currency = str(order["currency"])

    payment_intent = _stripe_get(config, f"/v1/payment_intents/{payment_intent_id}")
    if _stripe_id(payment_intent.get("id"), prefix="pi_", field="PaymentIntent id") != payment_intent_id:
        raise ValueError("Stripe returned a different PaymentIntent")
    if str(payment_intent.get("status") or "") != "succeeded":
        raise ValueError("Stripe marketplace PaymentIntent has not succeeded")
    if _minor(payment_intent.get("amount_received"), field="PaymentIntent amount received") != gross_minor:
        raise ValueError("Stripe marketplace PaymentIntent amount does not match the order")
    if _currency(payment_intent.get("currency")) != currency:
        raise ValueError("Stripe marketplace PaymentIntent currency does not match the order")
    charge_id = _stripe_id(payment_intent.get("latest_charge"), prefix="ch_", field="latest Charge id")

    charge = _stripe_get(config, f"/v1/charges/{charge_id}")
    if _stripe_id(charge.get("id"), prefix="ch_", field="Charge id") != charge_id:
        raise ValueError("Stripe returned a different Charge")
    if str(charge.get("payment_intent") or "") != payment_intent_id:
        raise ValueError("Stripe Charge is not bound to the verified PaymentIntent")
    if charge.get("paid") is not True or charge.get("captured") is not True:
        raise ValueError("Stripe marketplace Charge is not paid and captured")
    if _minor(charge.get("amount"), field="Charge amount") != gross_minor:
        raise ValueError("Stripe marketplace Charge amount does not match the order")
    if _minor(charge.get("amount_captured"), field="captured amount") != gross_minor:
        raise ValueError("Stripe marketplace captured amount does not match the order")
    if _currency(charge.get("currency")) != currency:
        raise ValueError("Stripe marketplace Charge currency does not match the order")
    balance_transaction_id = _stripe_id(
        charge.get("balance_transaction"),
        prefix="txn_",
        field="Balance Transaction id",
    )

    balance_transaction = _stripe_get(config, f"/v1/balance_transactions/{balance_transaction_id}")
    if _stripe_id(balance_transaction.get("id"), prefix="txn_", field="Balance Transaction id") != balance_transaction_id:
        raise ValueError("Stripe returned a different Balance Transaction")
    if str(balance_transaction.get("source") or "") != charge_id:
        raise ValueError("Stripe Balance Transaction is not bound to the verified Charge")
    if str(balance_transaction.get("type") or "") not in {"charge", "payment"}:
        raise ValueError("Stripe Balance Transaction is not a payment/charge transaction")
    if _minor(balance_transaction.get("amount"), field="Balance Transaction gross amount") != gross_minor:
        raise ValueError("Stripe Balance Transaction gross amount does not match the order")
    if _currency(balance_transaction.get("currency")) != currency:
        raise ValueError("Stripe Balance Transaction currency does not match the order")
    provider_fee_minor = _minor(balance_transaction.get("fee"), field="provider fee")
    net_minor = _minor(balance_transaction.get("net"), field="net amount")
    if provider_fee_minor > gross_minor or net_minor != gross_minor - provider_fee_minor:
        raise ValueError("Stripe Balance Transaction fee/net does not reconcile to gross")

    return {
        "checkout_session_id": session_id,
        "payment_intent_id": payment_intent_id,
        "charge_id": charge_id,
        "balance_transaction_id": balance_transaction_id,
        "gross_minor": gross_minor,
        "provider_fee_minor": provider_fee_minor,
        "net_minor": net_minor,
        "currency": currency,
    }


def verify_and_record_stripe_marketplace_settlement(
    *,
    event_id: str,
    checkout_session: dict[str, Any],
    orders: MarketplaceOrderStore,
    settlements: MarketplaceSettlementStore,
    fee_evidence: StripeMarketplaceFeeEvidenceStore,
    config: StripeConfig,
) -> dict[str, Any]:
    """Convert signed Stripe Checkout evidence into immutable marketplace settlement facts.

    The caller is responsible for verifying the Stripe webhook signature before passing the event
    object here. The signed event still cannot dictate price, tenant, publication, creator payee or
    Mary/Kev owner provenance: those values come only from the server-bound local order.
    """

    event_id = _stripe_id(event_id, prefix="evt_", field="event id")
    session_id = _stripe_id(checkout_session.get("id"), prefix="cs_", field="Checkout Session id")
    order = _bound_order(orders, provider="stripe", checkout_reference=session_id)
    _validate_checkout_against_order(checkout_session, order)

    existing = fee_evidence.by_checkout(session_id)
    if existing:
        if str(existing["order_id"]) != str(order["id"]):
            raise ValueError("Stored Stripe fee evidence belongs to a different marketplace order")
        verified = existing
    else:
        verified = fetch_verified_stripe_fee_evidence(
            checkout_session=checkout_session,
            order=order,
            config=config,
        )
        verified = fee_evidence.record(
            event_id=event_id,
            order_id=str(order["id"]),
            **verified,
        )

    settlement = record_verified_marketplace_payment(
        orders=orders,
        settlements=settlements,
        order_id=str(order["id"]),
        provider="stripe",
        provider_checkout_reference=session_id,
        provider_order_reference=str(verified["payment_intent_id"]),
        tenant_id=str(order["tenant_id"]),
        buyer_user_id=str(order["buyer_user_id"]),
        gross_minor=int(verified["gross_minor"]),
        provider_fee_minor=int(verified["provider_fee_minor"]),
        currency=str(verified["currency"]),
    )
    return {
        "provider": "stripe",
        "marketplace_order_id": str(order["id"]),
        "provider_order_reference": str(verified["payment_intent_id"]),
        "fee_evidence": {
            "checkout_session_id": str(verified["checkout_session_id"]),
            "payment_intent_id": str(verified["payment_intent_id"]),
            "charge_id": str(verified["charge_id"]),
            "balance_transaction_id": str(verified["balance_transaction_id"]),
            "gross_minor": int(verified["gross_minor"]),
            "provider_fee_minor": int(verified["provider_fee_minor"]),
            "net_minor": int(verified["net_minor"]),
            "currency": str(verified["currency"]),
        },
        "settlement": settlement,
        "subscription_effect": "none",
        "creation_coin_effect": "none",
        "esp_role_effect": "none",
        "payout_initiated": False,
    }


__all__ = [
    "StripeMarketplaceFeeEvidenceStore",
    "fetch_verified_stripe_fee_evidence",
    "verify_and_record_stripe_marketplace_settlement",
]
