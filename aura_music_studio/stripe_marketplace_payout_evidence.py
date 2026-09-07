from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .stripe_billing import StripeConfig, accounts
from .stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore, _stripe_get

PAYOUT_EVENT_TYPES = frozenset(
    {
        "payout.created",
        "payout.updated",
        "payout.paid",
        "payout.failed",
        "payout.canceled",
        "payout.reconciliation_completed",
    }
)
_PAYOUT_STATUSES = frozenset({"paid", "pending", "in_transit", "canceled", "failed"})
_RECON_STATUSES = frozenset({"completed", "in_progress", "not_applicable"})


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stripe_id(value: Any, prefix: str, field: str) -> str:
    result = str(value or "").strip()
    if not result.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_]{3,128}", result):
        raise ValueError(f"Stripe payout evidence has an invalid {field}")
    return result


def _optional_id(value: Any, prefix: str, field: str) -> str | None:
    return None if value in (None, "") else _stripe_id(value, prefix, field)


def _integer(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Stripe payout evidence has an invalid {field}") from exc


def _currency(value: Any) -> str:
    result = str(value or "").strip().upper()
    if len(result) != 3 or not result.isalpha():
        raise ValueError("Stripe payout evidence has an invalid currency")
    return result


def _payout(payout: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payout, dict) or str(payout.get("object") or "") != "payout":
        raise ValueError("Stripe payout evidence requires a Payout object")
    amount = _integer(payout.get("amount"), "payout amount")
    arrival = _integer(payout.get("arrival_date"), "arrival date")
    status = str(payout.get("status") or "").strip()
    recon = str(payout.get("reconciliation_status") or "").strip()
    automatic = payout.get("automatic")
    if amount <= 0 or arrival < 0:
        raise ValueError("Stripe payout evidence has invalid payout amount/date")
    if status not in _PAYOUT_STATUSES:
        raise ValueError("Stripe payout evidence has an invalid payout status")
    if recon not in _RECON_STATUSES:
        raise ValueError("Stripe payout evidence has an invalid reconciliation status")
    if not isinstance(automatic, bool):
        raise ValueError("Stripe payout evidence has an invalid automatic flag")
    return {
        "payout_id": _stripe_id(payout.get("id"), "po_", "Payout id"),
        "amount_minor": amount,
        "currency": _currency(payout.get("currency")),
        "status": status,
        "automatic": int(automatic),
        "arrival_date": arrival,
        "reconciliation_status": recon,
        "balance_transaction_id": _optional_id(
            payout.get("balance_transaction"), "txn_", "payout Balance Transaction id"
        ),
        "failure_balance_transaction_id": _optional_id(
            payout.get("failure_balance_transaction"),
            "txn_",
            "failure Balance Transaction id",
        ),
        "failure_code": str(payout.get("failure_code") or "").strip()[:120] or None,
    }


class StripePayoutEvidenceStore:
    """Stripe provider payout evidence. Independent bank proof is intentionally out of scope."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS stripe_payout_events (
                    event_id TEXT PRIMARY KEY,
                    payout_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    automatic INTEGER NOT NULL CHECK(automatic IN (0,1)),
                    arrival_date INTEGER NOT NULL CHECK(arrival_date >= 0),
                    reconciliation_status TEXT NOT NULL,
                    balance_transaction_id TEXT,
                    failure_balance_transaction_id TEXT,
                    failure_code TEXT,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_payout_events_payout
                    ON stripe_payout_events(payout_id, recorded_at DESC);
                CREATE TABLE IF NOT EXISTS stripe_payout_reconciliations (
                    payout_id TEXT PRIMARY KEY,
                    verified_event_id TEXT NOT NULL,
                    transaction_set_sha256 TEXT NOT NULL,
                    transaction_count INTEGER NOT NULL CHECK(transaction_count >= 0),
                    marketplace_transaction_count INTEGER NOT NULL
                        CHECK(marketplace_transaction_count >= 0),
                    marketplace_contribution_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    provider_reconciled_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stripe_payout_marketplace_memberships (
                    payout_id TEXT NOT NULL,
                    balance_transaction_id TEXT NOT NULL,
                    evidence_kind TEXT NOT NULL,
                    checkout_session_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    payment_intent_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    provider_net_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    verified_event_id TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    PRIMARY KEY(payout_id,balance_transaction_id)
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_payout_marketplace_order
                    ON stripe_payout_marketplace_memberships(order_id, verified_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def record_event(self, event_id: str, event_type: str, payout: dict[str, Any]) -> dict[str, Any]:
        event_id = _stripe_id(event_id, "evt_", "event id")
        if event_type not in PAYOUT_EVENT_TYPES:
            raise ValueError("Unsupported Stripe payout event type")
        p = _payout(payout)
        expected = {"payout_id": p["payout_id"], "event_type": event_type, **{
            key: p[key] for key in (
                "status", "amount_minor", "currency", "automatic", "arrival_date",
                "reconciliation_status", "balance_transaction_id",
                "failure_balance_transaction_id", "failure_code",
            )
        }}
        with self._connect() as con:
            row = con.execute("SELECT * FROM stripe_payout_events WHERE event_id=?", (event_id,)).fetchone()
            if row:
                result = dict(row)
                if any(result.get(k) != v for k, v in expected.items()):
                    raise ValueError("Stripe payout event changed after verification")
                return result
            con.execute(
                """INSERT INTO stripe_payout_events
                   (event_id,payout_id,event_type,status,amount_minor,currency,automatic,
                    arrival_date,reconciliation_status,balance_transaction_id,
                    failure_balance_transaction_id,failure_code,recorded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, p["payout_id"], event_type, p["status"], p["amount_minor"],
                 p["currency"], p["automatic"], p["arrival_date"], p["reconciliation_status"],
                 p["balance_transaction_id"], p["failure_balance_transaction_id"],
                 p["failure_code"], _iso()),
            )
            row = con.execute("SELECT * FROM stripe_payout_events WHERE event_id=?", (event_id,)).fetchone()
        return dict(row)

    def reconciliation(self, payout_id: str) -> dict[str, Any] | None:
        payout_id = _stripe_id(payout_id, "po_", "Payout id")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_payout_reconciliations WHERE payout_id=?", (payout_id,)
            ).fetchone()
        return dict(row) if row else None

    def memberships(self, payout_id: str) -> list[dict[str, Any]]:
        payout_id = _stripe_id(payout_id, "po_", "Payout id")
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM stripe_payout_marketplace_memberships
                   WHERE payout_id=? ORDER BY balance_transaction_id""", (payout_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def latest(self, payout_id: str) -> dict[str, Any] | None:
        payout_id = _stripe_id(payout_id, "po_", "Payout id")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_payout_events WHERE payout_id=? ORDER BY rowid DESC LIMIT 1",
                (payout_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        recon = self.reconciliation(payout_id)
        result["provider_reconciled"] = bool(recon)
        result["bank_reconciled"] = False
        result["marketplace_transaction_count"] = int(
            recon["marketplace_transaction_count"] if recon else 0
        )
        result["marketplace_contribution_minor"] = int(
            recon["marketplace_contribution_minor"] if recon else 0
        )
        return result

    def record_reconciliation(
        self,
        event_id: str,
        payout: dict[str, Any],
        transactions: list[dict[str, Any]],
        fee_evidence: StripeMarketplaceFeeEvidenceStore,
    ) -> dict[str, Any]:
        event_id = _stripe_id(event_id, "evt_", "event id")
        p = _payout(payout)
        if not p["automatic"]:
            raise ValueError("Stripe cannot provider-reconcile a manual payout")
        if p["reconciliation_status"] != "completed":
            raise ValueError("Stripe payout reconciliation is not completed")

        ids: list[str] = []
        seen: set[str] = set()
        matches: list[dict[str, Any]] = []
        contribution = 0
        with sqlite3.connect(Path(fee_evidence.db_path)) as source:
            source.row_factory = sqlite3.Row
            for txn in transactions:
                if not isinstance(txn, dict):
                    raise ValueError("Stripe payout reconciliation returned a non-object transaction")
                txn_id = _stripe_id(txn.get("id"), "txn_", "Balance Transaction id")
                if txn_id in seen:
                    raise ValueError("Stripe payout reconciliation repeated a Balance Transaction")
                seen.add(txn_id)
                ids.append(txn_id)

                payment = source.execute(
                    "SELECT * FROM stripe_marketplace_fee_evidence WHERE balance_transaction_id=?",
                    (txn_id,),
                ).fetchone()
                if payment:
                    e = dict(payment)
                    net = _validate_payment_txn(txn, e, p["currency"])
                    matches.append(_membership("payment", txn_id, e, e["charge_id"], net))
                    contribution += net
                    continue

                try:
                    refund = source.execute(
                        """SELECT r.*, f.order_id FROM stripe_marketplace_refund_evidence r
                           JOIN stripe_marketplace_fee_evidence f
                             ON f.payment_intent_id=r.payment_intent_id AND f.charge_id=r.charge_id
                           WHERE r.refund_balance_transaction_id=?""",
                        (txn_id,),
                    ).fetchone()
                except sqlite3.OperationalError:
                    refund = None
                if refund:
                    e = dict(refund)
                    net = _validate_refund_txn(txn, e, p["currency"])
                    matches.append(_membership("refund", txn_id, e, e["refund_id"], net))
                    contribution += net

        digest = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()
        expected = {
            "transaction_set_sha256": digest,
            "transaction_count": len(ids),
            "marketplace_transaction_count": len(matches),
            "marketplace_contribution_minor": contribution,
            "currency": p["currency"],
        }
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_payout_reconciliations WHERE payout_id=?", (p["payout_id"],)
            ).fetchone()
            if row:
                result = dict(row)
                if any(result.get(k) != v for k, v in expected.items()):
                    raise ValueError("Stripe payout reconciliation changed after completion")
                return result
            con.execute(
                """INSERT INTO stripe_payout_reconciliations
                   (payout_id,verified_event_id,transaction_set_sha256,transaction_count,
                    marketplace_transaction_count,marketplace_contribution_minor,currency,
                    provider_reconciled_at) VALUES (?,?,?,?,?,?,?,?)""",
                (p["payout_id"], event_id, digest, len(ids), len(matches), contribution,
                 p["currency"], _iso()),
            )
            for e in matches:
                con.execute(
                    """INSERT INTO stripe_payout_marketplace_memberships
                       (payout_id,balance_transaction_id,evidence_kind,checkout_session_id,
                        order_id,payment_intent_id,source_id,provider_net_minor,currency,
                        verified_event_id,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (p["payout_id"], e["balance_transaction_id"], e["evidence_kind"],
                     e["checkout_session_id"], e["order_id"], e["payment_intent_id"],
                     e["source_id"], e["provider_net_minor"], e["currency"], event_id, _iso()),
                )
            row = con.execute(
                "SELECT * FROM stripe_payout_reconciliations WHERE payout_id=?", (p["payout_id"],)
            ).fetchone()
        return dict(row)


def _membership(kind: str, txn_id: str, evidence: dict[str, Any], source_id: str, net: int) -> dict[str, Any]:
    return {
        "evidence_kind": kind,
        "balance_transaction_id": txn_id,
        "checkout_session_id": evidence["checkout_session_id"],
        "order_id": evidence["order_id"],
        "payment_intent_id": evidence["payment_intent_id"],
        "source_id": source_id,
        "provider_net_minor": net,
        "currency": evidence["currency"],
    }


def _validate_payment_txn(txn: dict[str, Any], e: dict[str, Any], payout_currency: str) -> int:
    if str(txn.get("type") or "") not in {"charge", "payment"}:
        raise ValueError("Marketplace payout payment is not a charge/payment Balance Transaction")
    if str(txn.get("source") or "") != str(e["charge_id"]):
        raise ValueError("Marketplace payout payment source does not match verified Charge")
    expected = (int(e["gross_minor"]), int(e["provider_fee_minor"]), int(e["net_minor"]))
    actual = tuple(_integer(txn.get(k), f"payment {k}") for k in ("amount", "fee", "net"))
    if actual != expected:
        raise ValueError("Marketplace payout payment financial evidence changed")
    if _currency(txn.get("currency")) != str(e["currency"]) or str(e["currency"]) != payout_currency:
        raise ValueError("Marketplace payout payment currency evidence changed")
    return actual[2]


def _validate_refund_txn(txn: dict[str, Any], e: dict[str, Any], payout_currency: str) -> int:
    if str(txn.get("type") or "") not in {"refund", "payment_refund"}:
        raise ValueError("Marketplace payout refund is not a refund Balance Transaction")
    if str(txn.get("source") or "") != str(e["refund_id"]):
        raise ValueError("Marketplace payout refund source does not match verified Refund")
    expected = (
        int(e["provider_balance_amount_minor"]),
        int(e["provider_balance_fee_minor"]),
        int(e["provider_balance_net_minor"]),
    )
    actual = tuple(_integer(txn.get(k), f"refund {k}") for k in ("amount", "fee", "net"))
    if actual != expected:
        raise ValueError("Marketplace payout refund financial evidence changed")
    if _currency(txn.get("currency")) != str(e["currency"]) or str(e["currency"]) != payout_currency:
        raise ValueError("Marketplace payout refund currency evidence changed")
    return actual[2]


def _fetch_transactions(config: StripeConfig, payout_id: str) -> list[dict[str, Any]]:
    payout_id = _stripe_id(payout_id, "po_", "Payout id")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = ""
    for _ in range(100):
        path = f"/v1/balance_transactions?payout={quote(payout_id, safe='')}&limit=100"
        if cursor:
            path += f"&starting_after={quote(cursor, safe='')}"
        page = _stripe_get(config, path)
        data = page.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Stripe returned an invalid payout Balance Transaction list")
        for item in data:
            if not isinstance(item, dict):
                raise RuntimeError("Stripe returned a non-object payout Balance Transaction")
            txn_id = _stripe_id(item.get("id"), "txn_", "Balance Transaction id")
            if txn_id in seen:
                raise RuntimeError("Stripe repeated a payout Balance Transaction across pages")
            seen.add(txn_id)
            result.append(item)
        if not page.get("has_more"):
            return result
        if not data:
            raise RuntimeError("Stripe payout pagination reported more results without a cursor")
        cursor = _stripe_id(data[-1].get("id"), "txn_", "Balance Transaction id")
    raise RuntimeError("Stripe payout reconciliation exceeded the safe pagination limit")


def process_verified_stripe_payout_event(
    *,
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
    config: StripeConfig,
    payout_store: StripePayoutEvidenceStore | None = None,
    fee_store: StripeMarketplaceFeeEvidenceStore | None = None,
) -> dict[str, Any]:
    """Process an already-signature-verified Stripe payout without claiming bank proof."""
    if event_type not in PAYOUT_EVENT_TYPES:
        raise ValueError("Unsupported Stripe payout event type")
    store = payout_store or payout_evidence
    fees = fee_store or marketplace_fee_evidence
    signed = _payout(obj)
    store.record_event(event_id, event_type, obj)

    if event_type == "payout.reconciliation_completed":
        canonical = _stripe_get(config, f"/v1/payouts/{quote(signed['payout_id'], safe='')}")
        current = _payout(canonical)
        if current["payout_id"] != signed["payout_id"]:
            raise ValueError("Stripe returned a different Payout during reconciliation")
        for field in ("amount_minor", "currency", "automatic"):
            if current[field] != signed[field]:
                raise ValueError("Stripe Payout changed immutable reconciliation facts")
        if not current["automatic"]:
            raise ValueError("Stripe automatic payout reconciliation cannot use a manual payout")
        if current["reconciliation_status"] == "in_progress":
            raise RuntimeError("Stripe payout reconciliation is still in progress")
        if current["reconciliation_status"] != "completed":
            raise ValueError("Stripe payout cannot enumerate provider reconciliation transactions")
        recon = store.record_reconciliation(
            event_id, canonical, _fetch_transactions(config, signed["payout_id"]), fees
        )
        status = current["status"]
    else:
        recon = store.reconciliation(signed["payout_id"])
        status = signed["status"]

    return {
        "processed": True,
        "kind": "provider_payout",
        "provider": "stripe",
        "payout_id": signed["payout_id"],
        "provider_status": status,
        "provider_reconciled": bool(recon),
        "marketplace_transaction_count": int(
            recon["marketplace_transaction_count"] if recon else 0
        ),
        "marketplace_contribution_minor": int(
            recon["marketplace_contribution_minor"] if recon else 0
        ),
        "currency": signed["currency"],
        "bank_reconciled": False,
        "marketplace_allocation_mutated": False,
    }


payout_evidence = StripePayoutEvidenceStore(accounts.db_path)
marketplace_fee_evidence = StripeMarketplaceFeeEvidenceStore(accounts.db_path)

__all__ = [
    "PAYOUT_EVENT_TYPES",
    "StripePayoutEvidenceStore",
    "marketplace_fee_evidence",
    "payout_evidence",
    "process_verified_stripe_payout_event",
]
