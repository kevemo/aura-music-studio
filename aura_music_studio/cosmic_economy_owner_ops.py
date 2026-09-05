from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .accounts import AccountStore
from .audit import AuditLedger
from .cosmic_economy import CosmicEconomy, EconomyError, _iso


class EconomyOwnerOperations:
    """Owner/Admin backend operations over the canonical Chat 5 economy tables.

    This module does not calculate creator cash entitlement, taxes, fees, profit or
    recognised revenue. Unknown commercial/accounting values stay unknown.
    """

    def __init__(self, economy: CosmicEconomy):
        self.economy = economy

    def list_risk_cases(self, *, status: str | None = "open", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM economy_risk_cases"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.economy._connect() as con:
            rows = con.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["evidence"] = json.loads(item.pop("evidence_json"))
            except Exception:
                item["evidence"] = {}
                item.pop("evidence_json", None)
            result.append(item)
        return result

    def review_risk_case(
        self,
        case_id: str,
        *,
        reviewer: str,
        decision: str,
        reason: str,
        appeal_status: str | None = None,
    ) -> dict[str, Any]:
        decision = (decision or "").strip().lower()
        if decision not in {"allow", "monitor", "hold", "block", "closed"}:
            raise EconomyError("INVALID_RISK_DECISION", "Unsupported risk review decision.")
        if len((reason or "").strip()) < 3:
            raise EconomyError("INVALID_RISK_DECISION", "Risk review requires a reason.")
        now = _iso()
        correlation_id = uuid4().hex
        with self.economy._connect() as con:
            self.economy._begin(con)
            row = con.execute("SELECT * FROM economy_risk_cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                raise EconomyError("RISK_CASE_NOT_FOUND", "Risk case not found.", status_code=404)
            status = "closed" if decision in {"allow", "block", "closed"} else "reviewed"
            con.execute(
                """UPDATE economy_risk_cases
                   SET status=?,reviewer=?,decision=?,decision_reason=?,appeal_status=?,updated_at=?
                   WHERE id=?""",
                (status, reviewer[:160], decision, reason.strip()[:1000], appeal_status, now, case_id),
            )
            self.economy._enqueue_locked(
                con,
                "economy.risk_case_reviewed",
                "risk_case",
                case_id,
                {"decision": decision, "reason": reason, "appeal_status": appeal_status},
                correlation_id=correlation_id,
            )
            result = con.execute("SELECT * FROM economy_risk_cases WHERE id=?", (case_id,)).fetchone()
            con.commit()
        AuditLedger(AccountStore(self.economy.db_path)).append(
            actor=reviewer,
            action="cosmic_economy.risk_case_reviewed",
            subject_user_id=result["subject_user_id"] if result else None,
            details={"case_id": case_id, "decision": decision, "reason": reason, "correlation_id": correlation_id},
        )
        return dict(result)

    def set_creator_receipt_hold(
        self,
        receipt_id: str,
        *,
        held: bool,
        actor: str,
        reason: str,
        reference: str,
    ) -> dict[str, Any]:
        if len((reason or "").strip()) < 3 or len((reference or "").strip()) < 2:
            raise EconomyError("INVALID_RECEIPT_ACTION", "Receipt hold/release requires a reason and reference.")
        now = _iso()
        correlation_id = uuid4().hex
        with self.economy._connect() as con:
            self.economy._begin(con)
            row = con.execute("SELECT * FROM creator_gift_receipts WHERE id=?", (receipt_id,)).fetchone()
            if not row:
                raise EconomyError("CREATOR_RECEIPT_NOT_FOUND", "Creator Gift receipt not found.", status_code=404)
            if row["status"] in {"reversed", "paid"}:
                raise EconomyError("RECEIPT_STATE_CONFLICT", "A reversed or paid receipt cannot be held/released this way.", status_code=409)
            new_status = "held" if held else "pending"
            con.execute(
                """UPDATE creator_gift_receipts
                   SET status=?,hold_reason=?,adjustment_reference=?,updated_at=? WHERE id=?""",
                (new_status, reason if held else None, reference, now, receipt_id),
            )
            self.economy._enqueue_locked(
                con,
                "creator.gift_receipt_held" if held else "creator.gift_receipt_released",
                "creator_gift_receipt",
                receipt_id,
                {"gift_transaction_id": row["gift_transaction_id"], "reason": reason, "reference": reference},
                live_session_id=row["live_session_id"],
                correlation_id=correlation_id,
            )
            result = con.execute("SELECT * FROM creator_gift_receipts WHERE id=?", (receipt_id,)).fetchone()
            con.commit()
        AuditLedger(AccountStore(self.economy.db_path)).append(
            actor=actor,
            action="cosmic_economy.creator_receipt_hold_changed",
            details={"receipt_id": receipt_id, "held": held, "reason": reason, "reference": reference, "correlation_id": correlation_id},
        )
        return dict(result)

    def list_reconciliation_discrepancies(self, *, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.economy._connect() as con:
            rows = con.execute(
                """SELECT * FROM economy_reconciliation_discrepancies
                   WHERE status=? ORDER BY created_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["expected"] = json.loads(item.pop("expected_json"))
            item["actual"] = json.loads(item.pop("actual_json"))
            result.append(item)
        return result

    def finance_snapshot(self) -> dict[str, Any]:
        with self.economy._connect() as con:
            purchase_rows = con.execute(
                """SELECT fiat_currency,status,COUNT(*) AS purchase_count,
                          COALESCE(SUM(fiat_amount_minor),0) AS fiat_amount_minor,
                          COALESCE(SUM(coin_quantity),0) AS coin_quantity
                   FROM coin_purchases GROUP BY fiat_currency,status ORDER BY fiat_currency,status"""
            ).fetchall()
            wallet = con.execute(
                """SELECT COUNT(*) AS account_count,
                          COALESCE(SUM(available_balance),0) AS available_coins,
                          COALESCE(SUM(held_balance),0) AS held_coins,
                          COALESCE(SUM(recovery_debt),0) AS recovery_debt_coins
                   FROM coin_accounts"""
            ).fetchone()
            gift_rows = con.execute(
                """SELECT status,COUNT(*) AS gift_count,COALESCE(SUM(total_coin_cost),0) AS gift_coins
                   FROM gift_transactions GROUP BY status ORDER BY status"""
            ).fetchall()
            receipt_rows = con.execute(
                """SELECT status,COUNT(*) AS receipt_count,COALESCE(SUM(coin_value),0) AS receipt_coins
                   FROM creator_gift_receipts GROUP BY status ORDER BY status"""
            ).fetchall()
            promo = con.execute(
                """SELECT COALESCE(SUM(coin_delta),0) AS issued_coins FROM coin_ledger_entries
                   WHERE entry_type='PROMOTIONAL_CREDIT' AND coin_delta>0"""
            ).fetchone()
            risk = con.execute("SELECT COUNT(*) AS n FROM economy_risk_cases WHERE status!='closed'").fetchone()
            discrepancies = con.execute(
                "SELECT COUNT(*) AS n FROM economy_reconciliation_discrepancies WHERE status='open'"
            ).fetchone()
        return {
            "coin_purchases_by_currency_and_status": [dict(r) for r in purchase_rows],
            "wallet_liability_state": dict(wallet) if wallet else {},
            "gifts_by_status": [dict(r) for r in gift_rows],
            "creator_receipts_by_status": [dict(r) for r in receipt_rows],
            "promotional_coins_issued": int(promo["issued_coins"] if promo else 0),
            "open_risk_cases": int(risk["n"] if risk else 0),
            "open_reconciliation_discrepancies": int(discrepancies["n"] if discrepancies else 0),
            "recognised_esp_revenue_minor": None,
            "creator_payable_fiat_minor": None if self.economy.active_payout_policy() is None else "policy_defined_records_only",
            "processor_fees_minor": None,
            "tax_minor": None,
            "profit_minor": None,
            "note": "Unknown accounting values are intentionally not inferred from Coin gross sales or Gift Coin value.",
        }
