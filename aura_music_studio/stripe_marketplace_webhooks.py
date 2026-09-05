from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .marketplace_orders import MarketplaceOrderStore
from .marketplace_settlement import MarketplaceSettlementStore
from .stripe_billing import StripeConfig, accounts
from .stripe_marketplace_fee_evidence import (
    StripeMarketplaceFeeEvidenceStore,
    _stripe_get,
    verify_and_record_stripe_marketplace_settlement,
)
from .stripe_marketplace_refund_evidence import (
    StripeMarketplaceRefundEvidenceStore,
    verify_and_record_stripe_marketplace_refund,
)


orders = MarketplaceOrderStore(accounts.db_path)
settlements = MarketplaceSettlementStore(accounts.db_path)
fee_evidence = StripeMarketplaceFeeEvidenceStore(accounts.db_path)
refund_evidence = StripeMarketplaceRefundEvidenceStore(accounts.db_path)

_PAYMENT_EVENTS = frozenset({"checkout.session.completed", "checkout.session.async_payment_succeeded"})
_REFUND_EVENTS = frozenset({"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"})


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
    *,
    config: StripeConfig | None = None,
) -> bool:
    """Classify a Stripe Refund without trusting browser or webhook metadata alone.

    The fast path requires already-verified marketplace fee evidence. If Stripe delivers a refund
    before the Checkout completion event has been processed, resolve its PaymentIntent back to a
    Checkout Session through Stripe and require that Session to be immutably bound to a local
    marketplace order before admitting the refund to marketplace settlement processing.
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
    *,
    config: StripeConfig | None = None,
) -> bool:
    if not isinstance(obj, dict):
        return False
    if event_type in _PAYMENT_EVENTS:
        metadata = obj.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return str(metadata.get("purchase_kind") or "") == "marketplace"
    if event_type in _REFUND_EVENTS:
        return _refund_targets_verified_marketplace(obj, config=config)
    return False


def process_verified_marketplace_stripe_event(
    *,
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
    config: StripeConfig,
) -> dict[str, Any]:
    """Apply one already-signature-verified marketplace event from provider evidence only."""
    if event_type in _PAYMENT_EVENTS:
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
    "fee_evidence",
    "is_marketplace_stripe_event",
    "orders",
    "process_verified_marketplace_stripe_event",
    "refund_evidence",
    "settlements",
]
