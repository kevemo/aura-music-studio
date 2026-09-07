from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentReversalStore:
    """Verified, append-only finance evidence for Stripe refunds.

    PaymentIntent correlation is accepted only after an already-verified local Stripe
    Creation Coin receipt exists. Refund evidence never mutates membership, ESP roles or
    Creation Coin balances; any commercial reversal policy must be implemented separately.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
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
                CREATE TABLE IF NOT EXISTS stripe_payment_correlations (
                    payment_intent_id TEXT PRIMARY KEY,
                    receipt_reference TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    purchase_kind TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_payment_correlations_user
                    ON stripe_payment_correlations(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS verified_payment_adjustments (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    adjustment_kind TEXT NOT NULL,
                    provider_adjustment_id TEXT NOT NULL UNIQUE,
                    payment_intent_id TEXT NOT NULL,
                    receipt_reference TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_verified_adjustments_verified
                    ON verified_payment_adjustments(verified_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_verified_adjustments_receipt
                    ON verified_payment_adjustments(receipt_reference, status);

                CREATE TABLE IF NOT EXISTS unmatched_payment_adjustments (
                    provider_adjustment_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    adjustment_kind TEXT NOT NULL,
                    payment_intent_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    stripe_event_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_unmatched_adjustments_verified
                    ON unmatched_payment_adjustments(verified_at DESC, provider_adjustment_id DESC);
                """
            )

    @staticmethod
    def _currency(value: str) -> str:
        currency = (value or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Payment reversal requires a three-letter currency")
        return currency

    @staticmethod
    def _provider_id(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()[:180]
        if isinstance(value, dict):
            return str(value.get("id") or "").strip()[:180]
        return ""

    def bind_credit_payment(
        self,
        *,
        payment_intent_id: str,
        receipt_reference: str,
        user_id: str,
        amount_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        payment_intent_id = self._provider_id(payment_intent_id)
        receipt_reference = (receipt_reference or "").strip()[:220]
        user_id = (user_id or "").strip()
        amount_minor = int(amount_minor)
        currency = self._currency(currency)
        if not payment_intent_id or not receipt_reference or not user_id or amount_minor <= 0:
            raise ValueError("Stripe payment correlation requires payment, receipt, user and positive amount")

        with self._connect() as con:
            receipt_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='commerce_receipts'"
            ).fetchone()
            if not receipt_table:
                raise ValueError("Stripe payment correlation requires a verified local receipt")
            receipt = con.execute(
                """SELECT provider,kind,reference,user_id,amount_minor,currency,status
                   FROM commerce_receipts WHERE reference=?""",
                (receipt_reference,),
            ).fetchone()
            if not receipt:
                raise ValueError("Stripe payment correlation requires a verified local receipt")
            if (
                str(receipt["provider"]) != "stripe"
                or str(receipt["kind"]) != "credit_topup"
                or str(receipt["status"]) != "paid"
                or str(receipt["user_id"]) != user_id
                or int(receipt["amount_minor"]) != amount_minor
                or str(receipt["currency"]).upper() != currency
            ):
                raise ValueError("Stripe payment correlation does not match the verified local receipt")

            existing = con.execute(
                "SELECT * FROM stripe_payment_correlations WHERE payment_intent_id=? OR receipt_reference=?",
                (payment_intent_id, receipt_reference),
            ).fetchone()
            if existing:
                row = dict(existing)
                expected = {
                    "payment_intent_id": payment_intent_id,
                    "receipt_reference": receipt_reference,
                    "user_id": user_id,
                    "purchase_kind": "credit_topup",
                    "amount_minor": amount_minor,
                    "currency": currency,
                }
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Stripe payment correlation was reused with different purchase data")
                return row

            con.execute(
                """INSERT INTO stripe_payment_correlations
                   (payment_intent_id,receipt_reference,user_id,purchase_kind,amount_minor,currency,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (payment_intent_id, receipt_reference, user_id, "credit_topup", amount_minor, currency, _iso()),
            )
            row = con.execute(
                "SELECT * FROM stripe_payment_correlations WHERE payment_intent_id=?",
                (payment_intent_id,),
            ).fetchone()
        return dict(row)

    def correlation(self, payment_intent_id: str) -> dict[str, Any] | None:
        clean = self._provider_id(payment_intent_id)
        if not clean:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_payment_correlations WHERE payment_intent_id=?",
                (clean,),
            ).fetchone()
        return dict(row) if row else None

    def _record_unmatched_refund(
        self,
        *,
        refund_id: str,
        payment_intent_id: str,
        amount_minor: int,
        currency: str,
        stripe_event_id: str,
    ) -> dict[str, Any]:
        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM unmatched_payment_adjustments WHERE provider_adjustment_id=?",
                (refund_id,),
            ).fetchone()
            if existing:
                row = dict(existing)
                if (
                    row["payment_intent_id"] != payment_intent_id
                    or int(row["amount_minor"]) != amount_minor
                    or row["currency"] != currency
                ):
                    raise ValueError("Unmatched Stripe refund id was reused with different financial data")
                row["linked"] = False
                return row
            con.execute(
                """INSERT INTO unmatched_payment_adjustments
                   (provider_adjustment_id,provider,adjustment_kind,payment_intent_id,
                    amount_minor,currency,status,verified_at,stripe_event_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    refund_id,
                    "stripe",
                    "refund",
                    payment_intent_id,
                    amount_minor,
                    currency,
                    "unmatched_verified_refund",
                    _iso(),
                    (stripe_event_id or "")[:180] or None,
                ),
            )
            row = con.execute(
                "SELECT * FROM unmatched_payment_adjustments WHERE provider_adjustment_id=?",
                (refund_id,),
            ).fetchone()
        result = dict(row)
        result["linked"] = False
        return result

    def record_stripe_refund(self, event: dict[str, Any]) -> dict[str, Any] | None:
        event_type = str(event.get("type") or "")
        if event_type not in {"refund.created", "refund.updated"}:
            return None
        data = event.get("data")
        refund = (data or {}).get("object") if isinstance(data, dict) else None
        if not isinstance(refund, dict):
            raise ValueError("Stripe refund event has no refund object")

        # Delayed refunds may be created before completion. Only successful provider evidence
        # changes verified net receipts; a later refund.updated event can record success.
        if str(refund.get("status") or "").strip().lower() != "succeeded":
            return None

        refund_id = self._provider_id(refund.get("id"))
        payment_intent_id = self._provider_id(refund.get("payment_intent"))
        try:
            amount_minor = int(refund.get("amount") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Successful Stripe refund has no valid amount") from exc
        currency = self._currency(str(refund.get("currency") or ""))
        if not refund_id or not payment_intent_id or amount_minor <= 0:
            raise ValueError("Successful Stripe refund is missing id, PaymentIntent or positive amount")

        correlation = self.correlation(payment_intent_id)
        if correlation is None:
            return self._record_unmatched_refund(
                refund_id=refund_id,
                payment_intent_id=payment_intent_id,
                amount_minor=amount_minor,
                currency=currency,
                stripe_event_id=str(event.get("id") or ""),
            )
        if currency != correlation["currency"]:
            raise ValueError("Stripe refund currency does not match the correlated purchase")
        if amount_minor > int(correlation["amount_minor"]):
            raise ValueError("Stripe refund exceeds the correlated purchase amount")

        metadata = {
            "stripe_event_id": str(event.get("id") or "")[:180],
            "reason": str(refund.get("reason") or "")[:120],
        }
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"))

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM verified_payment_adjustments WHERE provider_adjustment_id=?",
                (refund_id,),
            ).fetchone()
            if existing:
                row = dict(existing)
                expected = {
                    "payment_intent_id": payment_intent_id,
                    "receipt_reference": correlation["receipt_reference"],
                    "user_id": correlation["user_id"],
                    "amount_minor": amount_minor,
                    "currency": currency,
                    "status": "succeeded",
                }
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Stripe refund id was reused with different financial data")
                row["linked"] = True
                return row

            total_row = con.execute(
                """SELECT COALESCE(SUM(amount_minor),0) AS total
                   FROM verified_payment_adjustments
                   WHERE receipt_reference=? AND adjustment_kind='refund' AND status='succeeded'""",
                (correlation["receipt_reference"],),
            ).fetchone()
            cumulative = int(total_row["total"] or 0) + amount_minor
            if cumulative > int(correlation["amount_minor"]):
                raise ValueError("Cumulative Stripe refunds exceed the correlated purchase amount")

            adjustment_id = uuid4().hex
            con.execute(
                """INSERT INTO verified_payment_adjustments
                   (id,provider,adjustment_kind,provider_adjustment_id,payment_intent_id,
                    receipt_reference,user_id,amount_minor,currency,status,verified_at,metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    adjustment_id,
                    "stripe",
                    "refund",
                    refund_id,
                    payment_intent_id,
                    correlation["receipt_reference"],
                    correlation["user_id"],
                    amount_minor,
                    currency,
                    "succeeded",
                    _iso(),
                    encoded,
                ),
            )
            con.execute(
                "DELETE FROM unmatched_payment_adjustments WHERE provider_adjustment_id=?",
                (refund_id,),
            )
            row = con.execute(
                "SELECT * FROM verified_payment_adjustments WHERE id=?",
                (adjustment_id,),
            ).fetchone()
        result = dict(row)
        result["linked"] = True
        return result

    def summary(self, *, limit: int = 100) -> dict[str, Any]:
        """Return complete refund totals plus bounded recent evidence rows."""
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,provider,adjustment_kind,provider_adjustment_id,payment_intent_id,
                          receipt_reference,user_id,amount_minor,currency,status,verified_at
                   FROM verified_payment_adjustments
                   WHERE adjustment_kind='refund' AND status='succeeded'
                   ORDER BY verified_at DESC,id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            unmatched_rows = con.execute(
                """SELECT provider_adjustment_id,payment_intent_id,amount_minor,currency,status,verified_at
                   FROM unmatched_payment_adjustments
                   WHERE adjustment_kind='refund'
                   ORDER BY verified_at DESC,provider_adjustment_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            totals = con.execute(
                """SELECT currency,COALESCE(SUM(amount_minor),0) AS total
                   FROM verified_payment_adjustments
                   WHERE adjustment_kind='refund' AND status='succeeded'
                   GROUP BY currency"""
            ).fetchall()
            unmatched_totals = con.execute(
                """SELECT currency,COALESCE(SUM(amount_minor),0) AS total
                   FROM unmatched_payment_adjustments
                   WHERE adjustment_kind='refund'
                   GROUP BY currency"""
            ).fetchall()
            unmatched_count = int(
                con.execute(
                    """SELECT COUNT(*) AS total FROM unmatched_payment_adjustments
                       WHERE adjustment_kind='refund'"""
                ).fetchone()["total"]
                or 0
            )

        return {
            "verified_refunds_minor": {
                str(row["currency"]): int(row["total"] or 0) for row in totals
            },
            "recent_verified_refunds": [dict(row) for row in rows],
            "unmatched_verified_refunds_minor": {
                str(row["currency"]): int(row["total"] or 0) for row in unmatched_totals
            },
            "unmatched_verified_refund_count": unmatched_count,
            "recent_unmatched_verified_refunds": [dict(row) for row in unmatched_rows],
        }


__all__ = ["PaymentReversalStore"]
