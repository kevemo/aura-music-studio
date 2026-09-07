from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio.marketplace_settlement import MarketplaceSettlementStore
from aura_music_studio.stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore
from aura_music_studio import stripe_marketplace_refund_evidence as refunds


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def _config():
    return SimpleNamespace(secret_key="sk_test_private")


def _stores(tmp_path):
    db_path = tmp_path / "marketplace.sqlite3"
    fee_store = StripeMarketplaceFeeEvidenceStore(db_path)
    refund_store = refunds.StripeMarketplaceRefundEvidenceStore(db_path)
    settlements = MarketplaceSettlementStore(db_path)
    fee_store.record(
        event_id="evt_original_payment",
        checkout_session_id="cs_marketplace_123",
        order_id="local-order-a",
        payment_intent_id="pi_marketplace_123",
        charge_id="ch_marketplace_123",
        balance_transaction_id="txn_payment_123",
        gross_minor=2500,
        provider_fee_minor=75,
        net_minor=2425,
        currency="GBP",
    )
    settlements.record_verified_order(
        provider="stripe",
        provider_order_reference="pi_marketplace_123",
        tenant_id="tenant-a",
        publication_id="publication-a",
        creator_user_id="creator-a",
        gross_minor=2500,
        provider_fee_minor=75,
        currency="GBP",
    )
    return fee_store, refund_store, settlements


def _refund(refund_id="re_marketplace_1", amount=1000, balance_tx="txn_refund_1", status="succeeded"):
    return {
        "id": refund_id,
        "object": "refund",
        "status": status,
        "amount": amount,
        "currency": "gbp",
        "charge": "ch_marketplace_123",
        "payment_intent": "pi_marketplace_123",
        "balance_transaction": balance_tx,
    }


def _balance(refund_id="re_marketplace_1", amount=-1000, tx_id="txn_refund_1", fee=0, net=-1000):
    return {
        "id": tx_id,
        "type": "refund",
        "source": refund_id,
        "amount": amount,
        "fee": fee,
        "net": net,
        "currency": "gbp",
        "status": "available",
    }


def _provider(monkeypatch, objects):
    calls = []

    def fake_get(url, *, headers, timeout):
        calls.append((url, dict(headers), timeout))
        path = url.removeprefix("https://api.stripe.com")
        return _Response(objects[path])

    monkeypatch.setattr(refunds, "_stripe_get", lambda config, path: fake_get(
        f"https://api.stripe.com{path}",
        headers={"Authorization": f"Bearer {config.secret_key}"},
        timeout=20.0,
    ).json())
    return calls


def test_successful_partial_refund_reverses_proportional_verified_net(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    objects = {
        "/v1/refunds/re_marketplace_1": _refund(),
        "/v1/balance_transactions/txn_refund_1": _balance(),
    }
    calls = _provider(monkeypatch, objects)

    result = refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_refund_1",
        refund_event_object={"id": "re_marketplace_1", "amount": 1, "metadata": {"fake": "facts"}},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=_config(),
    )

    assert result["customer_refund_minor"] == 1000
    assert result["provider_balance_impact_minor"] == -1000
    assert result["settlement_reversal_minor"] == 970
    assert result["settlement_reversal"]["amount_minor"] == 970
    assert result["subscription_effect"] == "none"
    assert result["creation_coin_effect"] == "none"
    assert result["esp_role_effect"] == "none"
    assert result["payout_initiated"] is False
    assert result["bank_reconciled"] is False
    assert len(calls) == 2

    balance = settlements.balance_for_order("pi_marketplace_123")
    assert balance["verified_net_minor"] == 2425
    assert balance["reversed_minor"] == 970
    assert balance["remaining_net_minor"] == 1455
    stored = refund_store.by_refund("re_marketplace_1")
    assert stored["settlement_recorded_at"]
    assert "sk_test_private" not in str(stored)


def test_multiple_partial_refunds_converge_to_full_original_net_reversal(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    objects = {
        "/v1/refunds/re_marketplace_1": _refund(),
        "/v1/balance_transactions/txn_refund_1": _balance(),
        "/v1/refunds/re_marketplace_2": _refund(
            refund_id="re_marketplace_2",
            amount=1500,
            balance_tx="txn_refund_2",
        ),
        "/v1/balance_transactions/txn_refund_2": _balance(
            refund_id="re_marketplace_2",
            amount=-1500,
            tx_id="txn_refund_2",
            net=-1500,
        ),
    }
    _provider(monkeypatch, objects)

    first = refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_refund_1",
        refund_event_object={"id": "re_marketplace_1"},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=_config(),
    )
    second = refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_refund_2",
        refund_event_object={"id": "re_marketplace_2"},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=_config(),
    )

    assert first["settlement_reversal_minor"] == 970
    assert second["settlement_reversal_minor"] == 1455
    balance = settlements.balance_for_order("pi_marketplace_123")
    assert balance["reversed_minor"] == 2425
    assert balance["remaining_net_minor"] == 0
    assert balance["creator_share_remaining_minor"] == 0
    assert balance["admin_pool_share_remaining_minor"] == 0


def test_verified_refund_retry_uses_persisted_evidence_without_provider_fetch(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    objects = {
        "/v1/refunds/re_marketplace_1": _refund(),
        "/v1/balance_transactions/txn_refund_1": _balance(),
    }
    _provider(monkeypatch, objects)
    first = refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_refund_1",
        refund_event_object={"id": "re_marketplace_1"},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=_config(),
    )

    monkeypatch.setattr(
        refunds,
        "_stripe_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called on refund retry")),
    )
    second = refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_refund_retry",
        refund_event_object={"id": "re_marketplace_1", "amount": 999999},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=_config(),
    )

    assert second["settlement_reversal"]["id"] == first["settlement_reversal"]["id"]
    assert settlements.balance_for_order("pi_marketplace_123")["reversed_minor"] == 970


def test_pending_refund_never_mutates_marketplace_settlement(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    objects = {
        "/v1/refunds/re_marketplace_1": _refund(status="pending"),
    }
    _provider(monkeypatch, objects)

    with pytest.raises(ValueError, match="has not succeeded"):
        refunds.verify_and_record_stripe_marketplace_refund(
            event_id="evt_refund_1",
            refund_event_object={"id": "re_marketplace_1"},
            fee_evidence=fee_store,
            refund_evidence=refund_store,
            settlements=settlements,
            config=_config(),
        )
    assert refund_store.by_refund("re_marketplace_1") is None
    assert settlements.balance_for_order("pi_marketplace_123")["reversed_minor"] == 0


def test_refund_must_reference_verified_marketplace_payment(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    unbound = {
        **_refund(),
        "payment_intent": "pi_unrelated_123",
        "charge": "ch_unrelated_123",
    }
    objects = {"/v1/refunds/re_marketplace_1": unbound}
    _provider(monkeypatch, objects)

    with pytest.raises(ValueError, match="verified marketplace payment"):
        refunds.verify_and_record_stripe_marketplace_refund(
            event_id="evt_refund_1",
            refund_event_object={"id": "re_marketplace_1"},
            fee_evidence=fee_store,
            refund_evidence=refund_store,
            settlements=settlements,
            config=_config(),
        )
    assert refund_store.by_refund("re_marketplace_1") is None
    assert settlements.balance_for_order("pi_marketplace_123")["reversed_minor"] == 0


def test_refund_balance_transaction_must_bind_and_reconcile(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    bad_balance = {**_balance(), "source": "re_other"}
    objects = {
        "/v1/refunds/re_marketplace_1": _refund(),
        "/v1/balance_transactions/txn_refund_1": bad_balance,
    }
    _provider(monkeypatch, objects)

    with pytest.raises(ValueError, match="not bound"):
        refunds.verify_and_record_stripe_marketplace_refund(
            event_id="evt_refund_1",
            refund_event_object={"id": "re_marketplace_1"},
            fee_evidence=fee_store,
            refund_evidence=refund_store,
            settlements=settlements,
            config=_config(),
        )
    assert refund_store.by_refund("re_marketplace_1") is None
    assert settlements.balance_for_order("pi_marketplace_123")["reversed_minor"] == 0


def test_cumulative_verified_customer_refunds_cannot_exceed_original_gross(tmp_path, monkeypatch):
    fee_store, refund_store, settlements = _stores(tmp_path)
    objects = {
        "/v1/refunds/re_marketplace_1": _refund(),
        "/v1/balance_transactions/txn_refund_1": _balance(),
        "/v1/refunds/re_marketplace_2": _refund(
            refund_id="re_marketplace_2",
            amount=1600,
            balance_tx="txn_refund_2",
        ),
        "/v1/balance_transactions/txn_refund_2": _balance(
            refund_id="re_marketplace_2",
            amount=-1600,
            tx_id="txn_refund_2",
            net=-1600,
        ),
    }
    _provider(monkeypatch, objects)
    refunds.verify_and_record_stripe_marketplace_refund(
        event_id="evt_refund_1",
        refund_event_object={"id": "re_marketplace_1"},
        fee_evidence=fee_store,
        refund_evidence=refund_store,
        settlements=settlements,
        config=_config(),
    )

    with pytest.raises(ValueError, match="exceed"):
        refunds.verify_and_record_stripe_marketplace_refund(
            event_id="evt_refund_2",
            refund_event_object={"id": "re_marketplace_2"},
            fee_evidence=fee_store,
            refund_evidence=refund_store,
            settlements=settlements,
            config=_config(),
        )
    assert refund_store.by_refund("re_marketplace_2") is None
    assert settlements.balance_for_order("pi_marketplace_123")["reversed_minor"] == 970
