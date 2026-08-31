from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .marketplace_settlement import MarketplaceSettlementStore
from .stripe_billing import StripeConfig
from .stripe_marketplace_fee_evidence import (
    StripeMarketplaceFeeEvidenceStore,
    _currency,
    _iso,
    _stripe_get,
    _stripe_id,
)


def _signed_minor(value: Any, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Stripe marketplace refund evidence has an invalid {field}") from exc


def _positive_minor(value: Any, *, field: str) -> int:
    result = _signed_minor(value, field=field)
    if result <= 0:
        raise ValueError(f"Stripe marketplace refund evidence requires a positive {field}")
    return result


def _nonnegative_minor(value: Any, *, field: str) -> int:
    result = _signed_minor(value, field=field)
    if result < 0:
        raise ValueError(f"Stripe marketplace refund evidence requires a non-negative {field}")
    return result


def _original_fee_evidence(
    store: StripeMarketplaceFeeEvidenceStore,
    *,
    payment_intent_id: str,
    charge_id: str,
) -> dict[str, Any]:
    con = sqlite3.connect(Path(store.db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """SELECT * FROM stripe_marketplace_fee_evidence
               WHERE payment_intent_id=? AND charge_id=?""",
            (payment_intent_id, charge_id),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("Stripe refund does not reference a verified marketplace payment")
    return dict(row)


class StripeMarketplaceRefundEvidenceStore:
    """Append-only provider evidence for successful Stripe marketplace refunds.

    ``customer_refund_minor`` is the amount returned to the customer. The marketplace settlement
    ledger is based on the original *net* proceeds after provider fees, so this store separately
    computes an immutable proportional ``settlement_reversal_minor``. A full gross refund always
    reverses the full original verified net allocation; partial refunds converge deterministically
    to that value across multiple successful refunds.
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
                CREATE TABLE IF NOT EXISTS stripe_marketplace_refund_evidence (
                    refund_id TEXT PRIMARY KEY,
                    first_verified_event_id TEXT NOT NULL,
                    checkout_session_id TEXT NOT NULL,
                    payment_intent_id TEXT NOT NULL,
                    charge_id TEXT NOT NULL,
                    refund_balance_transaction_id TEXT NOT NULL UNIQUE,
                    customer_refund_minor INTEGER NOT NULL CHECK(customer_refund_minor > 0),
                    provider_balance_amount_minor INTEGER NOT NULL,
                    provider_balance_fee_minor INTEGER NOT NULL,
                    provider_balance_net_minor INTEGER NOT NULL,
                    settlement_reversal_minor INTEGER NOT NULL CHECK(settlement_reversal_minor >= 0),
                    currency TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    settlement_recorded_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_marketplace_refund_payment
                    ON stripe_marketplace_refund_evidence(payment_intent_id, verified_at ASC);
                """
            )

    def by_refund(self, refund_id: str) -> dict[str, Any] | None:
        refund_id = (refund_id or "").strip()
        if not refund_id:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_marketplace_refund_evidence WHERE refund_id=?",
                (refund_id,),
            ).fetchone()
        return dict(row) if row else None

    def record(
        self,
        *,
        event_id: str,
        refund_id: str,
        checkout_session_id: str,
        payment_intent_id: str,
        charge_id: str,
        refund_balance_transaction_id: str,
        customer_refund_minor: int,
        provider_balance_amount_minor: int,
        provider_balance_fee_minor: int,
        provider_balance_net_minor: int,
        currency: str,
        original_gross_minor: int,
        original_net_minor: int,
    ) -> dict[str, Any]:
        event_id = _stripe_id(event_id, prefix="evt_", field="event id")
        refund_id = _stripe_id(refund_id, prefix="re_", field="Refund id")
        checkout_session_id = _stripe_id(
            checkout_session_id,
            prefix="cs_",
            field="Checkout Session id",
        )
        payment_intent_id = _stripe_id(payment_intent_id, prefix="pi_", field="PaymentIntent id")
        charge_id = _stripe_id(charge_id, prefix="ch_", field="Charge id")
        refund_balance_transaction_id = _stripe_id(
            refund_balance_transaction_id,
            prefix="txn_",
            field="refund Balance Transaction id",
        )
        customer_refund_minor = _positive_minor(customer_refund_minor, field="customer refund amount")
        provider_balance_amount_minor = _signed_minor(
            provider_balance_amount_minor,
            field="provider balance amount",
        )
        provider_balance_fee_minor = _signed_minor(
            provider_balance_fee_minor,
            field="provider balance fee",
        )
        provider_balance_net_minor = _signed_minor(
            provider_balance_net_minor,
            field="provider balance net",
        )
        currency = _currency(currency)
        original_gross_minor = _positive_minor(original_gross_minor, field="original gross amount")
        original_net_minor = _nonnegative_minor(original_net_minor, field="original net amount")

        if provider_balance_amount_minor != -customer_refund_minor:
            raise ValueError("Stripe refund Balance Transaction amount does not match the customer refund")
        if provider_balance_net_minor != provider_balance_amount_minor - provider_balance_fee_minor:
            raise ValueError("Stripe refund Balance Transaction fee/net does not reconcile")
        if provider_balance_net_minor >= 0:
            raise ValueError("Stripe refund Balance Transaction does not reduce the provider balance")
        if original_net_minor > original_gross_minor:
            raise ValueError("Original marketplace fee evidence has invalid gross/net values")

        expected_provider = {
            "refund_id": refund_id,
            "checkout_session_id": checkout_session_id,
            "payment_intent_id": payment_intent_id,
            "charge_id": charge_id,
            "refund_balance_transaction_id": refund_balance_transaction_id,
            "customer_refund_minor": customer_refund_minor,
            "provider_balance_amount_minor": provider_balance_amount_minor,
            "provider_balance_fee_minor": provider_balance_fee_minor,
            "provider_balance_net_minor": provider_balance_net_minor,
            "currency": currency,
        }

        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM stripe_marketplace_refund_evidence WHERE refund_id=?",
                (refund_id,),
            ).fetchone()
            if existing:
                row = dict(existing)
                if any(row.get(key) != value for key, value in expected_provider.items()):
                    raise ValueError("Stripe Refund evidence changed after verification")
                con.commit()
                return row

            prior = con.execute(
                """SELECT COALESCE(SUM(customer_refund_minor),0),
                          COALESCE(SUM(settlement_reversal_minor),0)
                   FROM stripe_marketplace_refund_evidence
                   WHERE payment_intent_id=?""",
                (payment_intent_id,),
            ).fetchone()
            prior_customer_refund = int(prior[0])
            prior_planned_reversal = int(prior[1])
            cumulative_customer_refund = prior_customer_refund + customer_refund_minor
            if cumulative_customer_refund > original_gross_minor:
                raise ValueError("Verified Stripe refunds exceed the original marketplace gross amount")

            if cumulative_customer_refund == original_gross_minor:
                target_reversal = original_net_minor
            else:
                target_reversal = (
                    original_net_minor * cumulative_customer_refund
                ) // original_gross_minor
            settlement_reversal_minor = target_reversal - prior_planned_reversal
            if settlement_reversal_minor < 0:
                raise ValueError("Stripe marketplace refund reversal plan moved backwards")

            try:
                con.execute(
                    """INSERT INTO stripe_marketplace_refund_evidence
                       (refund_id,first_verified_event_id,checkout_session_id,payment_intent_id,
                        charge_id,refund_balance_transaction_id,customer_refund_minor,
                        provider_balance_amount_minor,provider_balance_fee_minor,
                        provider_balance_net_minor,settlement_reversal_minor,currency,verified_at,
                        settlement_recorded_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                    (
                        refund_id,
                        event_id,
                        checkout_session_id,
                        payment_intent_id,
                        charge_id,
                        refund_balance_transaction_id,
                        customer_refund_minor,
                        provider_balance_amount_minor,
                        provider_balance_fee_minor,
                        provider_balance_net_minor,
                        settlement_reversal_minor,
                        currency,
                        _iso(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "Stripe refund provider references are already bound to different evidence"
                ) from exc
            row = con.execute(
                "SELECT * FROM stripe_marketplace_refund_evidence WHERE refund_id=?",
                (refund_id,),
            ).fetchone()
            con.commit()
            return dict(row)
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def mark_settlement_recorded(self, refund_id: str) -> dict[str, Any]:
        refund_id = _stripe_id(refund_id, prefix="re_", field="Refund id")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_marketplace_refund_evidence WHERE refund_id=?",
                (refund_id,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown Stripe marketplace refund evidence")
            if row["settlement_recorded_at"] is None:
                con.execute(
                    """UPDATE stripe_marketplace_refund_evidence
                       SET settlement_recorded_at=? WHERE refund_id=?""",
                    (_iso(), refund_id),
                )
            row = con.execute(
                "SELECT * FROM stripe_marketplace_refund_evidence WHERE refund_id=?",
                (refund_id,),
            ).fetchone()
        return dict(row)


def _fetch_verified_refund(
    *,
    refund_id: str,
    fee_evidence: StripeMarketplaceFeeEvidenceStore,
    config: StripeConfig,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    refund_id = _stripe_id(refund_id, prefix="re_", field="Refund id")
    refund = _stripe_get(config, f"/v1/refunds/{refund_id}")
    if _stripe_id(refund.get("id"), prefix="re_", field="Refund id") != refund_id:
        raise ValueError("Stripe returned a different Refund")
    if str(refund.get("status") or "") != "succeeded":
        raise ValueError("Stripe marketplace Refund has not succeeded")

    payment_intent_id = _stripe_id(
        refund.get("payment_intent"),
        prefix="pi_",
        field="PaymentIntent id",
    )
    charge_id = _stripe_id(refund.get("charge"), prefix="ch_", field="Charge id")
    original = _original_fee_evidence(
        fee_evidence,
        payment_intent_id=payment_intent_id,
        charge_id=charge_id,
    )
    currency = _currency(refund.get("currency"))
    if currency != str(original["currency"]):
        raise ValueError("Stripe marketplace Refund currency does not match the verified payment")
    customer_refund_minor = _positive_minor(refund.get("amount"), field="customer refund amount")

    balance_transaction_id = _stripe_id(
        refund.get("balance_transaction"),
        prefix="txn_",
        field="refund Balance Transaction id",
    )
    balance_transaction = _stripe_get(
        config,
        f"/v1/balance_transactions/{balance_transaction_id}",
    )
    if (
        _stripe_id(
            balance_transaction.get("id"),
            prefix="txn_",
            field="refund Balance Transaction id",
        )
        != balance_transaction_id
    ):
        raise ValueError("Stripe returned a different refund Balance Transaction")
    if str(balance_transaction.get("source") or "") != refund_id:
        raise ValueError("Stripe refund Balance Transaction is not bound to the verified Refund")
    if str(balance_transaction.get("type") or "") not in {"refund", "payment_refund"}:
        raise ValueError("Stripe Balance Transaction is not a refund transaction")
    if _currency(balance_transaction.get("currency")) != currency:
        raise ValueError("Stripe refund Balance Transaction currency does not match the Refund")

    provider_amount = _signed_minor(balance_transaction.get("amount"), field="provider balance amount")
    provider_fee = _signed_minor(balance_transaction.get("fee"), field="provider balance fee")
    provider_net = _signed_minor(balance_transaction.get("net"), field="provider balance net")
    if provider_amount != -customer_refund_minor:
        raise ValueError("Stripe refund Balance Transaction amount does not match the Refund")
    if provider_net != provider_amount - provider_fee or provider_net >= 0:
        raise ValueError("Stripe refund Balance Transaction fee/net does not reconcile")

    return refund, balance_transaction, original


def verify_and_record_stripe_marketplace_refund(
    *,
    event_id: str,
    refund_event_object: dict[str, Any],
    fee_evidence: StripeMarketplaceFeeEvidenceStore,
    refund_evidence: StripeMarketplaceRefundEvidenceStore,
    settlements: MarketplaceSettlementStore,
    config: StripeConfig,
) -> dict[str, Any]:
    """Verify a signed Stripe refund event against provider state and reverse net allocation.

    The caller must verify the webhook signature before invoking this function. Only the opaque
    Refund id is taken from the event object; Stripe's canonical Refund and Balance Transaction
    are retrieved server-side before any marketplace allocation is reversed.
    """

    event_id = _stripe_id(event_id, prefix="evt_", field="event id")
    refund_id = _stripe_id(refund_event_object.get("id"), prefix="re_", field="Refund id")

    existing = refund_evidence.by_refund(refund_id)
    if existing:
        evidence_row = existing
    else:
        refund, balance_transaction, original = _fetch_verified_refund(
            refund_id=refund_id,
            fee_evidence=fee_evidence,
            config=config,
        )
        evidence_row = refund_evidence.record(
            event_id=event_id,
            refund_id=refund_id,
            checkout_session_id=str(original["checkout_session_id"]),
            payment_intent_id=str(original["payment_intent_id"]),
            charge_id=str(original["charge_id"]),
            refund_balance_transaction_id=str(refund["balance_transaction"]),
            customer_refund_minor=int(refund["amount"]),
            provider_balance_amount_minor=int(balance_transaction["amount"]),
            provider_balance_fee_minor=int(balance_transaction["fee"]),
            provider_balance_net_minor=int(balance_transaction["net"]),
            currency=str(original["currency"]),
            original_gross_minor=int(original["gross_minor"]),
            original_net_minor=int(original["net_minor"]),
        )

    reversal_minor = int(evidence_row["settlement_reversal_minor"])
    reversal = None
    if reversal_minor > 0:
        reversal = settlements.record_verified_reversal(
            provider="stripe",
            provider_reversal_reference=refund_id,
            provider_order_reference=str(evidence_row["payment_intent_id"]),
            amount_minor=reversal_minor,
            currency=str(evidence_row["currency"]),
        )
    evidence_row = refund_evidence.mark_settlement_recorded(refund_id)

    return {
        "provider": "stripe",
        "refund_id": refund_id,
        "payment_intent_id": str(evidence_row["payment_intent_id"]),
        "customer_refund_minor": int(evidence_row["customer_refund_minor"]),
        "provider_balance_impact_minor": int(evidence_row["provider_balance_net_minor"]),
        "settlement_reversal_minor": reversal_minor,
        "currency": str(evidence_row["currency"]),
        "settlement_reversal": reversal,
        "settlement_recorded": bool(evidence_row["settlement_recorded_at"]),
        "subscription_effect": "none",
        "creation_coin_effect": "none",
        "esp_role_effect": "none",
        "payout_initiated": False,
        "bank_reconciled": False,
    }


__all__ = [
    "StripeMarketplaceRefundEvidenceStore",
    "verify_and_record_stripe_marketplace_refund",
]
