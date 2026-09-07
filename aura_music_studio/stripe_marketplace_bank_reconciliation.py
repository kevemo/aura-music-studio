from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .stripe_marketplace_payout_evidence import StripePayoutEvidenceStore, payout_evidence

_BANK_PROVIDER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_BANK_TX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}")
_CURRENCY_RE = re.compile(r"[A-Z]{3}")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: Any, field: str, *, maximum: int = 160) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"Bank reconciliation has an invalid {field}")
    return result


def _payout_id(value: Any) -> str:
    result = _bounded(value, "Stripe payout id", maximum=128)
    if not result.startswith("po_") or not re.fullmatch(r"[A-Za-z0-9_]{3,128}", result):
        raise ValueError("Bank reconciliation has an invalid Stripe payout id")
    return result


def _bank_provider(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _BANK_PROVIDER_RE.fullmatch(result):
        raise ValueError("Bank reconciliation has an invalid provider")
    return result


def _bank_transaction_id(value: Any) -> str:
    result = str(value or "").strip()
    if not _BANK_TX_RE.fullmatch(result):
        raise ValueError("Bank reconciliation has an invalid bank transaction id")
    return result


def _currency(value: Any) -> str:
    result = str(value or "").strip().upper()
    if not _CURRENCY_RE.fullmatch(result):
        raise ValueError("Bank reconciliation has an invalid currency")
    return result


def _positive_minor(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Bank reconciliation has an invalid amount") from exc
    if result <= 0:
        raise ValueError("Bank reconciliation amount must be positive")
    return result


def _canonical_bank_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Trusted bank adapter returned a non-object transaction")
    provider = _bank_provider(raw.get("provider"))
    transaction_id = _bank_transaction_id(raw.get("transaction_id"))
    status = str(raw.get("status") or "").strip().lower()
    direction = str(raw.get("direction") or "").strip().lower()
    if status != "booked":
        raise ValueError("Bank transaction is not booked")
    if direction != "credit":
        raise ValueError("Bank transaction is not an inbound credit")
    payout_reference = _payout_id(raw.get("stripe_payout_id"))
    booked_at = _bounded(raw.get("booked_at"), "booking timestamp", maximum=64)
    try:
        parsed = datetime.fromisoformat(booked_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Bank reconciliation has an invalid booking timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("Bank reconciliation booking timestamp must be timezone-aware")
    evidence = {
        "provider": provider,
        "transaction_id": transaction_id,
        "status": status,
        "direction": direction,
        "stripe_payout_id": payout_reference,
        "amount_minor": _positive_minor(raw.get("amount_minor")),
        "currency": _currency(raw.get("currency")),
        "booked_at": parsed.astimezone(timezone.utc).isoformat(),
    }
    evidence["evidence_sha256"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return evidence


class StripeMarketplaceBankReconciliationStore:
    """Immutable evidence that a provider-reconciled Stripe payout reached a bank account.

    This module deliberately stores no bank account number, sort code, routing number, IBAN,
    customer data, credentials, access tokens, balances or free-form bank descriptions. A trusted
    bank adapter must supply one normalized booked-credit transaction to the reconciliation
    function. Tests may use a fixture callback; that is not production bank connectivity.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS stripe_marketplace_bank_reconciliations (
                    payout_id TEXT PRIMARY KEY,
                    bank_provider TEXT NOT NULL,
                    bank_transaction_id TEXT NOT NULL UNIQUE,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    booked_at TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_bank_recon_transaction
                    ON stripe_marketplace_bank_reconciliations(bank_provider, bank_transaction_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def get(self, payout_id: str) -> dict[str, Any] | None:
        payout_id = _payout_id(payout_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_marketplace_bank_reconciliations WHERE payout_id=?",
                (payout_id,),
            ).fetchone()
        return dict(row) if row else None

    def record(self, payout_id: str, bank: dict[str, Any]) -> dict[str, Any]:
        payout_id = _payout_id(payout_id)
        if bank["stripe_payout_id"] != payout_id:
            raise ValueError("Bank transaction does not reference the requested Stripe payout")
        expected = {
            "bank_provider": bank["provider"],
            "bank_transaction_id": bank["transaction_id"],
            "amount_minor": bank["amount_minor"],
            "currency": bank["currency"],
            "booked_at": bank["booked_at"],
            "evidence_sha256": bank["evidence_sha256"],
        }
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM stripe_marketplace_bank_reconciliations WHERE payout_id=?",
                (payout_id,),
            ).fetchone()
            if row:
                result = dict(row)
                if any(result.get(key) != value for key, value in expected.items()):
                    raise ValueError("Bank reconciliation evidence changed after verification")
                return result
            prior = con.execute(
                "SELECT payout_id FROM stripe_marketplace_bank_reconciliations "
                "WHERE bank_transaction_id=?",
                (bank["transaction_id"],),
            ).fetchone()
            if prior and prior["payout_id"] != payout_id:
                raise ValueError("Bank transaction is already assigned to another Stripe payout")
            con.execute(
                """INSERT INTO stripe_marketplace_bank_reconciliations
                   (payout_id,bank_provider,bank_transaction_id,amount_minor,currency,booked_at,
                    evidence_sha256,reconciled_at) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    payout_id,
                    bank["provider"],
                    bank["transaction_id"],
                    bank["amount_minor"],
                    bank["currency"],
                    bank["booked_at"],
                    bank["evidence_sha256"],
                    _iso(),
                ),
            )
            row = con.execute(
                "SELECT * FROM stripe_marketplace_bank_reconciliations WHERE payout_id=?",
                (payout_id,),
            ).fetchone()
        return dict(row)


def reconcile_stripe_marketplace_payout_to_bank(
    *,
    payout_id: str,
    bank_transaction_id: str,
    fetch_bank_transaction: Callable[[str], dict[str, Any]],
    payout_store: StripePayoutEvidenceStore | None = None,
    bank_store: StripeMarketplaceBankReconciliationStore | None = None,
) -> dict[str, Any]:
    """Bind a paid, provider-reconciled Stripe payout to canonical booked bank evidence.

    `fetch_bank_transaction` is the trust boundary: production must supply an authenticated,
    read-only bank/Open-Banking adapter that returns canonical data. Browser/request payloads must
    never be passed through as if they were verified bank evidence.
    """
    payout_id = _payout_id(payout_id)
    bank_transaction_id = _bank_transaction_id(bank_transaction_id)
    payouts = payout_store or payout_evidence
    banks = bank_store or bank_reconciliation_evidence

    payout = payouts.latest(payout_id)
    if not payout:
        raise ValueError("Stripe payout has no verified provider evidence")
    if str(payout.get("status") or "") != "paid":
        raise ValueError("Stripe payout is not provider-confirmed paid")
    if not payout.get("provider_reconciled"):
        raise ValueError("Stripe payout has not completed provider reconciliation")

    raw = fetch_bank_transaction(bank_transaction_id)
    bank = _canonical_bank_evidence(raw)
    if bank["transaction_id"] != bank_transaction_id:
        raise ValueError("Trusted bank adapter returned a different transaction")
    if bank["stripe_payout_id"] != payout_id:
        raise ValueError("Bank transaction references a different Stripe payout")
    if bank["amount_minor"] != int(payout["amount_minor"]):
        raise ValueError("Bank credit amount does not match the verified Stripe payout")
    if bank["currency"] != str(payout["currency"]):
        raise ValueError("Bank credit currency does not match the verified Stripe payout")

    evidence = banks.record(payout_id, bank)
    return {
        "processed": True,
        "kind": "independent_bank_reconciliation_evidence",
        "provider": bank["provider"],
        "payout_id": payout_id,
        "bank_transaction_id": bank_transaction_id,
        "amount_minor": bank["amount_minor"],
        "currency": bank["currency"],
        "bank_reconciled": True,
        "provider_reconciled": True,
        "payout_initiated": False,
        "marketplace_allocation_mutated": False,
        "subscription_effect": "none",
        "creation_coin_effect": "none",
        "esp_role_effect": "none",
        "evidence_sha256": evidence["evidence_sha256"],
    }


bank_reconciliation_evidence = StripeMarketplaceBankReconciliationStore(payout_evidence.db_path)

__all__ = [
    "StripeMarketplaceBankReconciliationStore",
    "bank_reconciliation_evidence",
    "reconcile_stripe_marketplace_payout_to_bank",
]
