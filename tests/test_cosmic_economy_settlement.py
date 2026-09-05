from __future__ import annotations

from dataclasses import replace

import pytest

from aura_music_studio.cosmic_economy import (
    BASELINE_PACK_ID,
    EconomyError,
    EligibilityDecision,
    VerifiedPaymentEvent,
)
from aura_music_studio.cosmic_economy_personal_limits import PersonalLimitCosmicEconomy
from aura_music_studio.cosmic_economy_settlement import (
    CoinSettlementReconciler,
    VerifiedSettlementState,
)


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class FakeSettlementProvider:
    name = "fake"

    def __init__(self, state: VerifiedSettlementState | None):
        self.state = state
        self.calls = []

    def fetch_settlement_state(self, *, provider_payment_id, purchase_id):
        self.calls.append((provider_payment_id, purchase_id))
        return self.state


class NoSettlementCapability:
    name = "fake"


def economy(tmp_path):
    return PersonalLimitCosmicEconomy(
        tmp_path / "economy.sqlite3",
        eligibility=AllowEligibility(),
    )


def pending_purchase(e, *, key="purchase-1", payment_id="provider-payment-1"):
    purchase = e.create_purchase(
        user_id="viewer-1",
        pack_id=BASELINE_PACK_ID,
        pack_version=1,
        provider="fake",
        idempotency_key=key,
    )
    return e.bind_provider_payment(purchase["id"], provider_payment_id=payment_id)


def confirmed_purchase(e):
    purchase = pending_purchase(e)
    event = VerifiedPaymentEvent(
        provider="fake",
        provider_event_id="evt-confirmed",
        provider_payment_id="provider-payment-1",
        purchase_id=purchase["id"],
        event_type="confirmed",
        verified=True,
        occurred_at="2026-09-05T20:00:00+00:00",
    )
    return e.apply_verified_payment_event(event)["purchase"]


def state_for(purchase, **overrides):
    state = VerifiedSettlementState(
        provider="fake",
        provider_payment_id=purchase["provider_payment_id"],
        purchase_id=purchase["id"],
        status=purchase["status"],
        fiat_amount_minor=int(purchase["fiat_amount_minor"]),
        fiat_currency=purchase["fiat_currency"],
        verified=True,
        observed_at="2026-09-05T20:05:00+00:00",
    )
    return replace(state, **overrides)


def test_matching_verified_provider_settlement_is_non_mutating(tmp_path):
    e = economy(tmp_path)
    purchase = confirmed_purchase(e)
    before = e.get_balance("viewer-1")
    provider = FakeSettlementProvider(state_for(purchase))

    result = CoinSettlementReconciler(e).reconcile_purchase(
        purchase["id"], provider=provider
    )

    assert result["ok"] is True
    assert result["discrepancies"] == []
    assert provider.calls == [("provider-payment-1", purchase["id"])]
    assert e.get_balance("viewer-1") == before


def test_provider_amount_mismatch_is_persisted_for_review_without_repair(tmp_path):
    e = economy(tmp_path)
    purchase = confirmed_purchase(e)
    provider = FakeSettlementProvider(
        state_for(purchase, fiat_amount_minor=int(purchase["fiat_amount_minor"]) + 1)
    )

    result = CoinSettlementReconciler(e).reconcile_purchase(
        purchase["id"], provider=provider
    )

    assert result["ok"] is False
    assert any(
        row["discrepancy_type"] == "PROVIDER_SETTLEMENT_AMOUNT_MISMATCH"
        for row in result["discrepancies"]
    )
    with e._connect() as con:
        stored = con.execute(
            """SELECT * FROM economy_reconciliation_discrepancies
               WHERE discrepancy_type='PROVIDER_SETTLEMENT_AMOUNT_MISMATCH'
                 AND subject_id=? AND status='open'""",
            (purchase["id"],),
        ).fetchone()
        internal = con.execute(
            "SELECT fiat_amount_minor,status FROM coin_purchases WHERE id=?",
            (purchase["id"],),
        ).fetchone()
    assert stored is not None
    assert int(internal["fiat_amount_minor"]) == 500
    assert internal["status"] == "confirmed"


def test_provider_status_mismatch_is_persisted(tmp_path):
    e = economy(tmp_path)
    purchase = pending_purchase(e)
    provider = FakeSettlementProvider(state_for(purchase, status="confirmed"))

    result = CoinSettlementReconciler(e).reconcile_purchase(
        purchase["id"], provider=provider
    )

    assert result["ok"] is False
    assert any(
        row["discrepancy_type"] == "PROVIDER_SETTLEMENT_STATUS_MISMATCH"
        for row in result["discrepancies"]
    )
    assert e.get_balance("viewer-1")["available_coins"] == 0


def test_unverified_settlement_state_fails_closed(tmp_path):
    e = economy(tmp_path)
    purchase = pending_purchase(e)
    provider = FakeSettlementProvider(state_for(purchase, verified=False))

    with pytest.raises(EconomyError) as exc:
        CoinSettlementReconciler(e).reconcile_purchase(
            purchase["id"], provider=provider
        )

    assert exc.value.code == "UNVERIFIED_SETTLEMENT_STATE"
    assert exc.value.status_code == 503


def test_missing_provider_settlement_capability_fails_closed(tmp_path):
    e = economy(tmp_path)
    purchase = pending_purchase(e)

    with pytest.raises(EconomyError) as exc:
        CoinSettlementReconciler(e).reconcile_purchase(
            purchase["id"], provider=NoSettlementCapability()
        )

    assert exc.value.code == "SETTLEMENT_RECONCILIATION_UNAVAILABLE"
    assert exc.value.status_code == 503


def test_missing_provider_record_creates_review_discrepancy(tmp_path):
    e = economy(tmp_path)
    purchase = pending_purchase(e)
    result = CoinSettlementReconciler(e).reconcile_purchase(
        purchase["id"], provider=FakeSettlementProvider(None)
    )
    assert result["ok"] is False
    assert result["provider_state"] is None
    assert result["discrepancies"][0]["discrepancy_type"] == "PROVIDER_SETTLEMENT_MISSING"


def test_provider_batch_reconciliation_counts_matches_and_mismatches(tmp_path):
    e = economy(tmp_path)
    purchase = pending_purchase(e)

    class Provider(FakeSettlementProvider):
        def fetch_settlement_state(self, *, provider_payment_id, purchase_id):
            return state_for(purchase)

    result = CoinSettlementReconciler(e).reconcile_provider(provider=Provider(None))
    assert result["checked"] == 1
    assert result["matched"] == 1
    assert result["mismatched"] == 0
    assert result["ok"] is True
