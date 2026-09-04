from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .accounts import AccountStore
from .native_products import BillingPeriod
from .plans import get_plan
from .subscriptions import SubscriptionLedger


@dataclass(frozen=True)
class PaymentHistoryRecord:
    receipt_reference: str
    payment_reference: str
    plan_id: str
    plan_name: str
    billing_period: str
    amount: str
    amount_minor: int | None
    currency: str
    display_amount: str
    period_start: str
    period_end: str
    verified_at: str
    record_status: str
    canonical_catalogue_match: bool
    receipt_scope: str = "esp_internal_billing_record"
    provider_invoice_url: None = None
    browser_return_is_payment_proof: bool = False
    independent_provider_settlement_evidence_presented: bool = False


@dataclass(frozen=True)
class RefundHistoryRecord:
    receipt_reference: str
    payment_reference: str
    refund_reference: str
    outcome: str
    outcome_label: str
    verified_at: str
    amount: None = None
    amount_known: bool = False
    receipt_scope: str = "esp_internal_billing_record"
    provider_invoice_url: None = None
    independent_provider_settlement_evidence_presented: bool = False


_REFUND_LABELS = {
    "future_transition_refunded_current_term_preserved": "Future paid term refunded; current paid term preserved",
    "current_term_refunded_entitlement_revoked": "Current paid term refunded; paid entitlement revoked",
    "historical_payment_refunded_current_entitlement_unchanged": "Historical payment refunded; current entitlement unchanged",
}


def _receipt(prefix: str, row_id: Any) -> str:
    clean = "".join(ch for ch in str(row_id or "") if ch.isalnum()).upper()
    return f"ESP-{prefix}-{clean[:12] or 'RECORDED'}"


def _display_amount(amount: str, currency: str) -> str:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return f"{currency} {amount}".strip()
    if currency == "GBP":
        return f"£{value:.2f}"
    return f"{currency} {value:.2f}".strip()


def _plan_name(plan_id: str) -> str:
    try:
        return get_plan(plan_id).name
    except (KeyError, ValueError):
        return plan_id


def _catalogue_match(row: sqlite3.Row) -> bool:
    try:
        plan = get_plan(str(row["plan_id"]))
        period = BillingPeriod(str(row["billing_period"]))
        stored_amount = Decimal(str(row["amount"] or row["amount_usd"]))
        return (
            plan.currency == str(row["currency"] or "")
            and plan.price_for(period) == stored_amount
            and plan.price_minor_for(period) == int(row["amount_minor"])
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False


def _safe_subscription(state: dict | None) -> dict | None:
    if not state:
        return None
    plan_id = str(state.get("plan_id") or "free")
    return {
        "plan_id": plan_id,
        "plan_name": _plan_name(plan_id),
        "status": str(state.get("status") or "unknown"),
        "billing_period": state.get("billing_period"),
        "period_start": state.get("period_start"),
        "period_end": state.get("period_end"),
        "cancel_at_period_end": state.get("status") == "cancel_at_period_end",
    }


def _safe_transition(transition: dict | None) -> dict | None:
    if not transition:
        return None
    plan_id = str(transition.get("target_plan_id") or "free")
    return {
        "target_plan_id": plan_id,
        "target_plan_name": _plan_name(plan_id),
        "target_billing_period": transition.get("target_billing_period"),
        "effective_at": transition.get("effective_at"),
        "period_end": transition.get("period_end"),
        "status": transition.get("status"),
        "cancel_at_period_end": bool(int(transition.get("cancel_at_period_end") or 0)),
    }


class BillingHistoryService:
    """Read-only, customer-safe projection of the authoritative subscription ledger.

    This view never changes entitlement, never accepts a caller-supplied account id, never
    exposes authentication material or webhook payloads, and never fabricates provider invoice
    URLs. A recorded verified payment/refund is an ESP ledger fact; this read model deliberately
    does not claim to independently re-verify provider settlement when history is viewed.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self.ledger = SubscriptionLedger(self.store)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.store.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def for_user(self, user_id: str) -> dict:
        user = self.store.get_user(user_id)
        if not user:
            raise ValueError("Account not found")

        with self._connect() as con:
            payments = con.execute(
                """SELECT id,plan_id,payment_reference,amount_usd,amount,amount_minor,currency,
                          billing_period,period_start,period_end,verified_at
                   FROM subscription_payments
                   WHERE user_id=? ORDER BY verified_at DESC,id DESC""",
                (user_id,),
            ).fetchall()
            refunds = con.execute(
                """SELECT id,payment_reference,refund_reference,outcome,verified_at
                   FROM subscription_refunds
                   WHERE user_id=? ORDER BY verified_at DESC,id DESC""",
                (user_id,),
            ).fetchall()

        payment_records = []
        for row in payments:
            amount = str(row["amount"] or row["amount_usd"] or "0")
            currency = str(row["currency"] or "GBP")
            payment_records.append(
                asdict(
                    PaymentHistoryRecord(
                        receipt_reference=_receipt("PAY", row["id"]),
                        payment_reference=str(row["payment_reference"]),
                        plan_id=str(row["plan_id"]),
                        plan_name=_plan_name(str(row["plan_id"])),
                        billing_period=str(row["billing_period"]),
                        amount=amount,
                        amount_minor=int(row["amount_minor"]) if row["amount_minor"] is not None else None,
                        currency=currency,
                        display_amount=_display_amount(amount, currency),
                        period_start=str(row["period_start"]),
                        period_end=str(row["period_end"]),
                        verified_at=str(row["verified_at"]),
                        record_status="verified_recorded",
                        canonical_catalogue_match=_catalogue_match(row),
                    )
                )
            )

        refund_records = [
            asdict(
                RefundHistoryRecord(
                    receipt_reference=_receipt("REF", row["id"]),
                    payment_reference=str(row["payment_reference"]),
                    refund_reference=str(row["refund_reference"]),
                    outcome=str(row["outcome"]),
                    outcome_label=_REFUND_LABELS.get(str(row["outcome"]), "Verified refund recorded"),
                    verified_at=str(row["verified_at"]),
                )
            )
            for row in refunds
        ]

        plan_id = str(user.get("plan_id") or "free")
        return {
            "account": {
                "status": str(user.get("status") or "unknown"),
                "billing_status": str(user.get("billing_status") or "unknown"),
                "plan_id": plan_id,
                "plan_name": _plan_name(plan_id),
            },
            "subscription": _safe_subscription(self.ledger.get(user_id)),
            "scheduled_transition": _safe_transition(self.ledger.scheduled_transition(user_id)),
            "payments": payment_records,
            "refunds": refund_records,
            "payment_count": len(payment_records),
            "refund_count": len(refund_records),
            "read_only": True,
            "customer_scoped": True,
            "browser_return_is_payment_proof": False,
            "independent_provider_settlement_evidence_presented": False,
            "provider_invoice_urls_fabricated": False,
        }


__all__ = ["BillingHistoryService", "PaymentHistoryRecord", "RefundHistoryRecord"]
