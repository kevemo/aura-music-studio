from __future__ import annotations

from datetime import datetime, timezone

from aura_music_studio.cosmic_economy import (
    BASELINE_PACK_ID,
    EligibilityDecision,
    LiveGiftContext,
    VerifiedPaymentEvent,
)
from aura_music_studio.cosmic_economy_integrations import IntegratedCosmicEconomy


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class Live:
    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, True, True, True)


def make_economy(tmp_path):
    return IntegratedCosmicEconomy(
        tmp_path / "economy.sqlite3",
        live_sessions=Live(),
        eligibility=AllowEligibility(),
    )


def pending_purchase(economy):
    purchase = economy.create_purchase(
        user_id="viewer-1",
        pack_id=BASELINE_PACK_ID,
        pack_version=1,
        provider="fake",
        idempotency_key="purchase-1",
    )
    return economy.bind_provider_payment(
        purchase["id"], provider_payment_id="provider-payment-1"
    )


def event(purchase, event_type, event_id):
    return VerifiedPaymentEvent(
        provider="fake",
        provider_event_id=event_id,
        provider_payment_id="provider-payment-1",
        purchase_id=purchase["id"],
        event_type=event_type,
        verified=True,
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )


def test_verified_failed_purchase_event_closes_pending_without_credit(tmp_path):
    econ = make_economy(tmp_path)
    purchase = pending_purchase(econ)
    result = econ.apply_verified_payment_event(event(purchase, "failed", "evt-failed"))
    assert result["purchase"]["status"] == "failed"
    assert econ.get_balance("viewer-1")["available_coins"] == 0


def test_purchase_reconciliation_detects_missing_credit_reference(tmp_path):
    econ = make_economy(tmp_path)
    purchase = pending_purchase(econ)
    econ.apply_verified_payment_event(event(purchase, "confirmed", "evt-confirm"))
    with econ._connect() as con:
        con.execute(
            "UPDATE coin_purchases SET ledger_credit_id=NULL WHERE id=?",
            (purchase["id"],),
        )
    result = econ.reconcile()
    assert result["ok"] is False
    assert any(
        row["discrepancy_type"] == "PURCHASE_LEDGER_CREDIT_MISMATCH"
        for row in result["discrepancies"]
    )
