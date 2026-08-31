from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .stripe_billing import StripeConfig
from .stripe_marketplace_fee_evidence import (
    StripeMarketplaceFeeEvidenceStore,
    _currency,
    _minor,
    _stripe_get,
    _stripe_id,
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StripeMarketplacePayoutEvidenceStore:
    """Durable evidence that verified marketplace balance transactions entered a Stripe payout.

    This is provider payout-inclusion evidence, not bank-statement reconciliation and not a payout
    instruction surface. It stores no bank account details, card data, customer email, prompts,
    private tenant payload, Stripe secret, or role/entitlement state.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS stripe_marketplace_payout_evidence (
                    payout_id TEXT PRIMARY KEY,
                    first_verified_event_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    payout_status TEXT NOT NULL,
                    automatic INTEGER NOT NULL CHECK(automatic IN (0,1)),
                    reconciliation_status TEXT NOT NULL,
                    marketplace_transaction_count INTEGER NOT NULL CHECK(marketplace_transaction_count >= 0),
                    marketplace_net_minor INTEGER NOT NULL CHECK(marketplace_net_minor >= 0),
                    verified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stripe_marketplace_payout_items (
                    payout_id TEXT NOT NULL,
                    balance_transaction_id TEXT NOT NULL UNIQUE,
                    checkout_session_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    net_minor INTEGER NOT NULL CHECK(net_minor >= 0),
                    currency TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    PRIMARY KEY (payout_id, balance_transaction_id),
                    FOREIGN KEY(payout_id) REFERENCES stripe_marketplace_payout_evidence(payout_id)
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_marketplace_payout_items_order
                    ON stripe_marketplace_payout_items(order_id, verified_at DESC);
                """
            )

    def by_payout(self, payout_id: str) -> dict[str, Any] | None:
        payout_id = (payout_id or "").strip()
        if not payout_id:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_marketplace_payout_evidence WHERE payout_id=?",
                (payout_id,),
            ).fetchone()
        return dict(row) if row else None

    def items_for_payout(self, payout_id: str) -> list[dict[str, Any]]:
        payout_id = (payout_id or "").strip()
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM stripe_marketplace_payout_items
                   WHERE payout_id=? ORDER BY balance_transaction_id""",
                (payout_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def payout_for_balance_transaction(self, balance_transaction_id: str) -> dict[str, Any] | None:
        balance_transaction_id = (balance_transaction_id or "").strip()
        if not balance_transaction_id:
            return None
        with self._connect() as con:
            row = con.execute(
                """SELECT p.*, i.balance_transaction_id, i.checkout_session_id,
                          i.order_id, i.net_minor AS marketplace_item_net_minor
                   FROM stripe_marketplace_payout_items i
                   JOIN stripe_marketplace_payout_evidence p ON p.payout_id=i.payout_id
                   WHERE i.balance_transaction_id=?""",
                (balance_transaction_id,),
            ).fetchone()
        return dict(row) if row else None

    def record(
        self,
        *,
        event_id: str,
        payout_id: str,
        amount_minor: int,
        currency: str,
        payout_status: str,
        automatic: bool,
        reconciliation_status: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        event_id = _stripe_id(event_id, prefix="evt_", field="event id")
        payout_id = _stripe_id(payout_id, prefix="po_", field="Payout id")
        amount_minor = _minor(amount_minor, field="Payout amount")
        if amount_minor <= 0:
            raise ValueError("Stripe marketplace payout evidence requires a positive payout amount")
        currency = _currency(currency)
        payout_status = str(payout_status or "").strip()
        reconciliation_status = str(reconciliation_status or "").strip()
        if not automatic:
            raise ValueError("Marketplace payout inclusion evidence requires an automatic Stripe payout")
        if reconciliation_status != "completed":
            raise ValueError("Stripe payout reconciliation is not complete")
        if payout_status not in {"pending", "in_transit", "paid"}:
            raise ValueError("Stripe payout is not in a reconcilable delivery state")

        normalised_items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            txn_id = _stripe_id(item.get("balance_transaction_id"), prefix="txn_", field="Balance Transaction id")
            if txn_id in seen:
                raise ValueError("Stripe payout returned a duplicate Balance Transaction")
            seen.add(txn_id)
            checkout_session_id = _stripe_id(item.get("checkout_session_id"), prefix="cs_", field="Checkout Session id")
            order_id = str(item.get("order_id") or "").strip()
            if not order_id or len(order_id) > 128:
                raise ValueError("Stripe marketplace payout evidence has an invalid local order id")
            net_minor = _minor(item.get("net_minor"), field="marketplace net amount")
            item_currency = _currency(item.get("currency"))
            if item_currency != currency:
                raise ValueError("Stripe payout item currency does not match the payout")
            normalised_items.append(
                {
                    "balance_transaction_id": txn_id,
                    "checkout_session_id": checkout_session_id,
                    "order_id": order_id,
                    "net_minor": net_minor,
                    "currency": item_currency,
                }
            )

        marketplace_net_minor = sum(int(item["net_minor"]) for item in normalised_items)
        if marketplace_net_minor > amount_minor:
            raise ValueError("Marketplace transactions cannot exceed the verified Stripe payout amount")
        expected = {
            "payout_id": payout_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "payout_status": payout_status,
            "automatic": 1,
            "reconciliation_status": reconciliation_status,
            "marketplace_transaction_count": len(normalised_items),
            "marketplace_net_minor": marketplace_net_minor,
        }

        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM stripe_marketplace_payout_evidence WHERE payout_id=?",
                (payout_id,),
            ).fetchone()
            if existing:
                row = dict(existing)
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Stripe payout evidence changed after verification")
                existing_items = con.execute(
                    """SELECT balance_transaction_id,checkout_session_id,order_id,net_minor,currency
                       FROM stripe_marketplace_payout_items WHERE payout_id=?
                       ORDER BY balance_transaction_id""",
                    (payout_id,),
                ).fetchall()
                stored = [dict(item) for item in existing_items]
                wanted = sorted(normalised_items, key=lambda item: item["balance_transaction_id"])
                if stored != wanted:
                    raise ValueError("Stripe payout marketplace item evidence changed after verification")
                return row

            con.execute(
                """INSERT INTO stripe_marketplace_payout_evidence
                   (payout_id,first_verified_event_id,amount_minor,currency,payout_status,automatic,
                    reconciliation_status,marketplace_transaction_count,marketplace_net_minor,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    payout_id,
                    event_id,
                    amount_minor,
                    currency,
                    payout_status,
                    1,
                    reconciliation_status,
                    len(normalised_items),
                    marketplace_net_minor,
                    _iso(),
                ),
            )
            for item in normalised_items:
                try:
                    con.execute(
                        """INSERT INTO stripe_marketplace_payout_items
                           (payout_id,balance_transaction_id,checkout_session_id,order_id,net_minor,currency,verified_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            payout_id,
                            item["balance_transaction_id"],
                            item["checkout_session_id"],
                            item["order_id"],
                            item["net_minor"],
                            item["currency"],
                            _iso(),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Stripe Balance Transaction is already reconciled to another payout") from exc
            row = con.execute(
                "SELECT * FROM stripe_marketplace_payout_evidence WHERE payout_id=?",
                (payout_id,),
            ).fetchone()
        return dict(row)


def _known_marketplace_fee_evidence(
    fee_evidence: StripeMarketplaceFeeEvidenceStore,
    balance_transaction_ids: set[str],
) -> list[dict[str, Any]]:
    if not balance_transaction_ids:
        return []
    placeholders = ",".join("?" for _ in balance_transaction_ids)
    with sqlite3.connect(Path(fee_evidence.db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""SELECT checkout_session_id,order_id,balance_transaction_id,net_minor,currency
                FROM stripe_marketplace_fee_evidence
                WHERE balance_transaction_id IN ({placeholders})""",
            tuple(sorted(balance_transaction_ids)),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_verified_stripe_payout_inclusion(
    *,
    payout_id: str,
    fee_evidence: StripeMarketplaceFeeEvidenceStore,
    config: StripeConfig,
) -> dict[str, Any]:
    """Resolve canonical Stripe automatic-payout membership and retain known marketplace items.

    Stripe can only map source balance transactions to automatic payouts. A completed provider
    reconciliation proves payout inclusion; it does not prove that the destination bank statement
    has been independently matched, so this function deliberately does not claim bank finality.
    """
    payout_id = _stripe_id(payout_id, prefix="po_", field="Payout id")
    payout = _stripe_get(config, f"/v1/payouts/{quote(payout_id, safe='')}")
    if _stripe_id(payout.get("id"), prefix="po_", field="Payout id") != payout_id:
        raise ValueError("Stripe returned a different Payout")
    if payout.get("automatic") is not True:
        raise ValueError("Marketplace payout inclusion evidence requires an automatic Stripe payout")
    if str(payout.get("reconciliation_status") or "") != "completed":
        raise ValueError("Stripe payout reconciliation is not complete")
    payout_status = str(payout.get("status") or "").strip()
    if payout_status not in {"pending", "in_transit", "paid"}:
        raise ValueError("Stripe payout is not in a reconcilable delivery state")
    amount_minor = _minor(payout.get("amount"), field="Payout amount")
    if amount_minor <= 0:
        raise ValueError("Stripe payout amount must be positive")
    currency = _currency(payout.get("currency"))

    provider_transactions: dict[str, dict[str, Any]] = {}
    starting_after = ""
    for _page in range(100):
        suffix = f"&starting_after={quote(starting_after, safe='')}" if starting_after else ""
        page = _stripe_get(
            config,
            f"/v1/balance_transactions?payout={quote(payout_id, safe='')}&limit=100{suffix}",
        )
        data = page.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Stripe returned an invalid payout Balance Transaction list")
        for transaction in data:
            if not isinstance(transaction, dict):
                raise RuntimeError("Stripe returned an invalid payout Balance Transaction")
            txn_id = _stripe_id(transaction.get("id"), prefix="txn_", field="Balance Transaction id")
            if txn_id in provider_transactions:
                raise ValueError("Stripe payout pagination returned a duplicate Balance Transaction")
            provider_transactions[txn_id] = transaction
        if page.get("has_more") is not True:
            break
        if not data:
            raise RuntimeError("Stripe payout pagination did not advance")
        starting_after = _stripe_id(data[-1].get("id"), prefix="txn_", field="Balance Transaction id")
    else:
        raise RuntimeError("Stripe payout reconciliation exceeded the pagination safety limit")

    known = _known_marketplace_fee_evidence(fee_evidence, set(provider_transactions))
    items: list[dict[str, Any]] = []
    for evidence in known:
        txn_id = str(evidence["balance_transaction_id"])
        transaction = provider_transactions[txn_id]
        if _currency(transaction.get("currency")) != str(evidence["currency"]):
            raise ValueError("Stripe payout transaction currency changed from verified marketplace evidence")
        if _minor(transaction.get("net"), field="Balance Transaction net amount") != int(evidence["net_minor"]):
            raise ValueError("Stripe payout transaction net changed from verified marketplace evidence")
        items.append(dict(evidence))

    return {
        "payout_id": payout_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "payout_status": payout_status,
        "automatic": True,
        "reconciliation_status": "completed",
        "items": items,
    }


def verify_and_record_stripe_marketplace_payout(
    *,
    event_id: str,
    payout_event_object: dict[str, Any],
    fee_evidence: StripeMarketplaceFeeEvidenceStore,
    payout_evidence: StripeMarketplacePayoutEvidenceStore,
    config: StripeConfig,
) -> dict[str, Any]:
    """Record automatic-payout inclusion from canonical Stripe evidence only."""
    event_id = _stripe_id(event_id, prefix="evt_", field="event id")
    payout_id = _stripe_id(payout_event_object.get("id"), prefix="po_", field="Payout id")
    verified = fetch_verified_stripe_payout_inclusion(
        payout_id=payout_id,
        fee_evidence=fee_evidence,
        config=config,
    )
    stored = payout_evidence.record(event_id=event_id, **verified)
    return {
        "provider": "stripe",
        "payout_id": payout_id,
        "payout_status": stored["payout_status"],
        "marketplace_transaction_count": int(stored["marketplace_transaction_count"]),
        "marketplace_net_minor": int(stored["marketplace_net_minor"]),
        "currency": str(stored["currency"]),
        "provider_payout_inclusion_verified": True,
        "bank_statement_reconciled": False,
        "payout_initiated": False,
        "subscription_effect": "none",
        "creation_coin_effect": "none",
        "esp_role_effect": "none",
    }


__all__ = [
    "StripeMarketplacePayoutEvidenceStore",
    "fetch_verified_stripe_payout_inclusion",
    "verify_and_record_stripe_marketplace_payout",
]
