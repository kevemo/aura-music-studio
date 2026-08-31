from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.stripe_billing import StripeConfig
from aura_music_studio.stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore
from aura_music_studio.stripe_marketplace_payout_evidence import (
    StripeMarketplacePayoutEvidenceStore,
    fetch_verified_stripe_payout_inclusion,
    verify_and_record_stripe_marketplace_payout,
)


def _config() -> StripeConfig:
    return StripeConfig(
        secret_key="sk_test_provider_evidence",
        webhook_secret="whsec_test",
        public_base_url="https://example.test",
        base_price_id="price_base",
        pro_price_id="price_pro",
        settlement_label="test",
    )


def _fee_evidence(tmp_path) -> StripeMarketplaceFeeEvidenceStore:
    store = StripeMarketplaceFeeEvidenceStore(tmp_path / "marketplace.sqlite3")
    store.record(
        event_id="evt_payment_001",
        checkout_session_id="cs_market_001",
        order_id="order_001",
        payment_intent_id="pi_market_001",
        charge_id="ch_market_001",
        balance_transaction_id="txn_market_001",
        gross_minor=1000,
        provider_fee_minor=30,
        net_minor=970,
        currency="GBP",
    )
    return store


def test_payout_store_is_idempotent_and_never_claims_bank_finality(tmp_path):
    store = StripeMarketplacePayoutEvidenceStore(tmp_path / "marketplace.sqlite3")
    kwargs = dict(
        event_id="evt_payout_001",
        payout_id="po_auto_001",
        amount_minor=1500,
        currency="GBP",
        payout_status="paid",
        automatic=True,
        reconciliation_status="completed",
        items=[
            {
                "balance_transaction_id": "txn_market_001",
                "checkout_session_id": "cs_market_001",
                "order_id": "order_001",
                "net_minor": 970,
                "currency": "GBP",
            }
        ],
    )
    first = store.record(**kwargs)
    second = store.record(**kwargs)
    assert second["payout_id"] == first["payout_id"]
    assert second["marketplace_transaction_count"] == 1
    assert second["marketplace_net_minor"] == 970
    linked = store.payout_for_balance_transaction("txn_market_001")
    assert linked is not None
    assert linked["payout_id"] == "po_auto_001"
    assert linked["marketplace_item_net_minor"] == 970


def test_payout_store_rejects_manual_or_incomplete_payouts(tmp_path):
    store = StripeMarketplacePayoutEvidenceStore(tmp_path / "marketplace.sqlite3")
    base = dict(
        event_id="evt_payout_001",
        payout_id="po_auto_001",
        amount_minor=1500,
        currency="GBP",
        payout_status="paid",
        reconciliation_status="completed",
        items=[],
    )
    with pytest.raises(ValueError, match="automatic"):
        store.record(automatic=False, **base)
    with pytest.raises(ValueError, match="not complete"):
        store.record(automatic=True, **{**base, "reconciliation_status": "in_progress"})


def test_balance_transaction_cannot_be_reassigned_to_another_payout(tmp_path):
    store = StripeMarketplacePayoutEvidenceStore(tmp_path / "marketplace.sqlite3")
    item = {
        "balance_transaction_id": "txn_market_001",
        "checkout_session_id": "cs_market_001",
        "order_id": "order_001",
        "net_minor": 970,
        "currency": "GBP",
    }
    store.record(
        event_id="evt_payout_001",
        payout_id="po_auto_001",
        amount_minor=1500,
        currency="GBP",
        payout_status="paid",
        automatic=True,
        reconciliation_status="completed",
        items=[item],
    )
    with pytest.raises(ValueError, match="another payout"):
        store.record(
            event_id="evt_payout_002",
            payout_id="po_auto_002",
            amount_minor=1500,
            currency="GBP",
            payout_status="paid",
            automatic=True,
            reconciliation_status="completed",
            items=[item],
        )


def test_fetch_payout_inclusion_uses_canonical_provider_pages_and_known_fee_evidence(tmp_path, monkeypatch):
    fee_store = _fee_evidence(tmp_path)
    calls: list[str] = []

    def fake_get(config, path):
        calls.append(path)
        if path == "/v1/payouts/po_auto_001":
            return {
                "id": "po_auto_001",
                "amount": 1500,
                "currency": "gbp",
                "automatic": True,
                "reconciliation_status": "completed",
                "status": "paid",
            }
        if path == "/v1/balance_transactions?payout=po_auto_001&limit=100":
            return {
                "data": [
                    {"id": "txn_market_001", "net": 970, "currency": "gbp"},
                    {"id": "txn_unrelated_001", "net": 530, "currency": "gbp"},
                ],
                "has_more": False,
            }
        raise AssertionError(path)

    monkeypatch.setattr("aura_music_studio.stripe_marketplace_payout_evidence._stripe_get", fake_get)
    result = fetch_verified_stripe_payout_inclusion(
        payout_id="po_auto_001",
        fee_evidence=fee_store,
        config=_config(),
    )
    assert result["payout_id"] == "po_auto_001"
    assert result["amount_minor"] == 1500
    assert [item["balance_transaction_id"] for item in result["items"]] == ["txn_market_001"]
    assert calls == [
        "/v1/payouts/po_auto_001",
        "/v1/balance_transactions?payout=po_auto_001&limit=100",
    ]


def test_fetch_payout_inclusion_fails_closed_if_provider_transaction_drifted(tmp_path, monkeypatch):
    fee_store = _fee_evidence(tmp_path)

    def fake_get(config, path):
        if path.startswith("/v1/payouts/"):
            return {
                "id": "po_auto_001",
                "amount": 1500,
                "currency": "gbp",
                "automatic": True,
                "reconciliation_status": "completed",
                "status": "paid",
            }
        return {
            "data": [{"id": "txn_market_001", "net": 969, "currency": "gbp"}],
            "has_more": False,
        }

    monkeypatch.setattr("aura_music_studio.stripe_marketplace_payout_evidence._stripe_get", fake_get)
    with pytest.raises(ValueError, match="net changed"):
        fetch_verified_stripe_payout_inclusion(
            payout_id="po_auto_001",
            fee_evidence=fee_store,
            config=_config(),
        )


def test_verify_and_record_reports_evidence_only_authority_effects(tmp_path, monkeypatch):
    fee_store = _fee_evidence(tmp_path)
    payout_store = StripeMarketplacePayoutEvidenceStore(tmp_path / "marketplace.sqlite3")

    monkeypatch.setattr(
        "aura_music_studio.stripe_marketplace_payout_evidence.fetch_verified_stripe_payout_inclusion",
        lambda **kwargs: {
            "payout_id": "po_auto_001",
            "amount_minor": 1500,
            "currency": "GBP",
            "payout_status": "paid",
            "automatic": True,
            "reconciliation_status": "completed",
            "items": [
                {
                    "balance_transaction_id": "txn_market_001",
                    "checkout_session_id": "cs_market_001",
                    "order_id": "order_001",
                    "net_minor": 970,
                    "currency": "GBP",
                }
            ],
        },
    )
    result = verify_and_record_stripe_marketplace_payout(
        event_id="evt_payout_001",
        payout_event_object={"id": "po_auto_001", "amount": 1, "currency": "usd"},
        fee_evidence=fee_store,
        payout_evidence=payout_store,
        config=_config(),
    )
    assert result["provider_payout_inclusion_verified"] is True
    assert result["bank_statement_reconciled"] is False
    assert result["payout_initiated"] is False
    assert result["subscription_effect"] == "none"
    assert result["creation_coin_effect"] == "none"
    assert result["esp_role_effect"] == "none"


def test_store_persists_no_bank_or_entitlement_columns(tmp_path):
    store = StripeMarketplacePayoutEvidenceStore(tmp_path / "marketplace.sqlite3")
    with sqlite3.connect(store.db_path) as con:
        payout_columns = {row[1] for row in con.execute("PRAGMA table_info(stripe_marketplace_payout_evidence)")}
        item_columns = {row[1] for row in con.execute("PRAGMA table_info(stripe_marketplace_payout_items)")}
    forbidden = {
        "bank_account",
        "destination",
        "customer_email",
        "stripe_secret",
        "subscription",
        "creation_coins",
        "role",
        "creator_role",
        "agent_role",
        "owner_role",
    }
    assert payout_columns.isdisjoint(forbidden)
    assert item_columns.isdisjoint(forbidden)
