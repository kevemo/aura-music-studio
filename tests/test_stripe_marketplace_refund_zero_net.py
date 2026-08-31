from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio.marketplace_settlement import MarketplaceSettlementStore
from aura_music_studio.stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore
from aura_music_studio import stripe_marketplace_refund_evidence as refunds


def test_full_refund_of_zero_net_marketplace_payment_records_evidence_without_fake_reversal(tmp_path, monkeypatch):
    db_path = tmp_path / "zero-net.sqlite3"
    fee_store = StripeMarketplaceFeeEvidenceStore(db_path)
    refund_store = refunds.StripeMarketplaceRefundEvidenceStore(db_path)
    settlements = MarketplaceSettlementStore(db_path)

    fee_store.record(
        event_id="evt_original_zero_net",
        checkout_session_id="cs_zero_net_123",
        order_id="local-zero-net-order",
        payment_intent_id="pi_zero_net_123",
        charge_id="ch_zero_net_123",
        balance_transaction_id="txn_zero_net_payment",
        gross_minor=1,
        provider_fee_minor=1,
        net_minor=0,
        currency="GBP",
    )
    settlements.record_verified_order(
        provider="stripe",
        provider_order_reference="pi_zero_net_123",
        tenant_id="tenant-a",
        publication_id="publication-a",
        creator_user_id="creator-a",
        gross_minor=1,
        provider_fee_minor=1,
        currency="GBP",
    )

    provider_objects = {
        "/v1/refunds/re_zero_net_123": {
            "id": "re_zero_net_123",
            "status": "succeeded",
            "amount": 1,
            "currency": "gbp",
            "charge": "ch_zero_net_123",
            "payment_intent": "pi_zero_net_123",
            "balance_transaction": "txn_zero_net_refund",
        },
        "/v1/balance_transactions/txn_zero_net_refund": {
            "id": "txn_zero_net_refund",
            "type": "refund",
            "source": "re_zero_net_123",
            "amount": -1,
            "fee": 0,
            "net": -1,
            "currency": "gbp",
        },
    }
    monkeypatch.setattr(
        refunds,
        "_stripe_get",
        lambda config, path: provider_objects[path],
    )

    result = refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_zero_net_refund",
        refund_event_object={"id": "re_zero_net_123"},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=SimpleNamespace(secret_key="sk_test_private"),
    )

    assert result["customer_refund_minor"] == 1
    assert result["settlement_reversal_minor"] == 0
    assert result["settlement_reversal"] is None
    assert result["settlement_recorded"] is True
    balance = settlements.balance_for_order("pi_zero_net_123")
    assert balance["verified_net_minor"] == 0
    assert balance["reversed_minor"] == 0
    assert balance["remaining_net_minor"] == 0
