from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio.marketplace_orders import MarketplaceOrderStore
from aura_music_studio.marketplace_settlement import MarketplaceSettlementStore
from aura_music_studio import stripe_marketplace_fee_evidence as fee_evidence


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
    return (
        MarketplaceOrderStore(db_path),
        MarketplaceSettlementStore(db_path),
        fee_evidence.StripeMarketplaceFeeEvidenceStore(db_path),
    )


def _creator_order(orders: MarketplaceOrderStore):
    order = orders.create_order(
        provider="stripe",
        tenant_id="tenant-a",
        buyer_user_id="buyer-a",
        publication_id="publication-a",
        publication_revision="revision-a",
        gross_minor=2500,
        currency="GBP",
        creator_user_id="creator-a",
    )
    return orders.bind_provider_checkout(
        order_id=order["id"],
        provider_checkout_reference="cs_marketplace_123",
    )


def _session(order):
    return {
        "id": "cs_marketplace_123",
        "mode": "payment",
        "payment_status": "paid",
        "client_reference_id": "buyer-a",
        "amount_total": 2500,
        "currency": "gbp",
        "payment_intent": "pi_marketplace_123",
        "metadata": {
            "purchase_kind": "marketplace",
            "marketplace_order_id": order["id"],
        },
    }


def _provider_objects():
    return {
        "/v1/payment_intents/pi_marketplace_123": {
            "id": "pi_marketplace_123",
            "status": "succeeded",
            "amount_received": 2500,
            "currency": "gbp",
            "latest_charge": "ch_marketplace_123",
        },
        "/v1/charges/ch_marketplace_123": {
            "id": "ch_marketplace_123",
            "payment_intent": "pi_marketplace_123",
            "paid": True,
            "captured": True,
            "amount": 2500,
            "amount_captured": 2500,
            "currency": "gbp",
            "balance_transaction": "txn_marketplace_123",
        },
        "/v1/balance_transactions/txn_marketplace_123": {
            "id": "txn_marketplace_123",
            "source": "ch_marketplace_123",
            "type": "charge",
            "amount": 2500,
            "fee": 75,
            "net": 2425,
            "currency": "gbp",
        },
    }


def test_verified_stripe_chain_records_fee_evidence_and_creator_settlement(tmp_path, monkeypatch):
    orders, settlements, evidence = _stores(tmp_path)
    order = _creator_order(orders)
    provider_objects = _provider_objects()
    requests = []

    def fake_get(url, *, headers, timeout):
        requests.append((url, dict(headers), timeout))
        path = url.removeprefix("https://api.stripe.com")
        return _Response(provider_objects[path])

    monkeypatch.setattr(fee_evidence.httpx, "get", fake_get)
    result = fee_evidence.verify_and_record_stripe_marketplace_settlement(
        event_id="evt_marketplace_123",
        checkout_session=_session(order),
        orders=orders,
        settlements=settlements,
        fee_evidence=evidence,
        config=_config(),
    )

    assert result["fee_evidence"]["gross_minor"] == 2500
    assert result["fee_evidence"]["provider_fee_minor"] == 75
    assert result["fee_evidence"]["net_minor"] == 2425
    assert result["settlement"]["provider_order_reference"] == "pi_marketplace_123"
    assert result["settlement"]["creator_share_minor"] == 1212
    assert result["settlement"]["admin_pool_share_minor"] == 1213
    assert result["subscription_effect"] == "none"
    assert result["creation_coin_effect"] == "none"
    assert result["esp_role_effect"] == "none"
    assert result["payout_initiated"] is False
    assert len(requests) == 3
    assert all(item[1]["Authorization"] == "Bearer sk_test_private" for item in requests)

    stored = evidence.by_checkout("cs_marketplace_123")
    assert stored["charge_id"] == "ch_marketplace_123"
    assert stored["balance_transaction_id"] == "txn_marketplace_123"
    assert "sk_test_private" not in str(stored)


def test_existing_verified_fee_evidence_retries_without_calling_stripe_again(tmp_path, monkeypatch):
    orders, settlements, evidence = _stores(tmp_path)
    order = _creator_order(orders)
    provider_objects = _provider_objects()

    monkeypatch.setattr(
        fee_evidence.httpx,
        "get",
        lambda url, **kwargs: _Response(provider_objects[url.removeprefix("https://api.stripe.com")]),
    )
    first = fee_evidence.verify_and_record_stripe_marketplace_settlement(
        event_id="evt_marketplace_123",
        checkout_session=_session(order),
        orders=orders,
        settlements=settlements,
        fee_evidence=evidence,
        config=_config(),
    )

    monkeypatch.setattr(
        fee_evidence.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called on evidence retry")),
    )
    second = fee_evidence.verify_and_record_stripe_marketplace_settlement(
        event_id="evt_marketplace_retry_456",
        checkout_session=_session(order),
        orders=orders,
        settlements=settlements,
        fee_evidence=evidence,
        config=_config(),
    )

    assert second["settlement"]["id"] == first["settlement"]["id"]
    assert second["fee_evidence"] == first["fee_evidence"]


def test_provider_metadata_cannot_rebind_checkout_to_another_local_order(tmp_path, monkeypatch):
    orders, settlements, evidence = _stores(tmp_path)
    order = _creator_order(orders)
    checkout = _session(order)
    checkout["metadata"]["marketplace_order_id"] = "attacker-selected-order"
    monkeypatch.setattr(
        fee_evidence.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe enrichment must not run")),
    )

    with pytest.raises(ValueError, match="metadata"):
        fee_evidence.verify_and_record_stripe_marketplace_settlement(
            event_id="evt_marketplace_123",
            checkout_session=checkout,
            orders=orders,
            settlements=settlements,
            fee_evidence=evidence,
            config=_config(),
        )
    assert evidence.by_checkout("cs_marketplace_123") is None


def test_balance_transaction_must_bind_to_charge_and_reconcile_fee_net(tmp_path, monkeypatch):
    orders, settlements, evidence = _stores(tmp_path)
    order = _creator_order(orders)
    provider_objects = _provider_objects()
    provider_objects["/v1/balance_transactions/txn_marketplace_123"] = {
        **provider_objects["/v1/balance_transactions/txn_marketplace_123"],
        "source": "ch_different",
    }
    monkeypatch.setattr(
        fee_evidence.httpx,
        "get",
        lambda url, **kwargs: _Response(provider_objects[url.removeprefix("https://api.stripe.com")]),
    )

    with pytest.raises(ValueError, match="not bound"):
        fee_evidence.verify_and_record_stripe_marketplace_settlement(
            event_id="evt_marketplace_123",
            checkout_session=_session(order),
            orders=orders,
            settlements=settlements,
            fee_evidence=evidence,
            config=_config(),
        )
    assert evidence.by_checkout("cs_marketplace_123") is None
    with pytest.raises(ValueError, match="Unknown marketplace order"):
        settlements.balance_for_order("pi_marketplace_123")


def test_fee_evidence_store_rejects_reference_reuse_with_different_financial_facts(tmp_path):
    _, _, evidence = _stores(tmp_path)
    first = evidence.record(
        event_id="evt_marketplace_123",
        checkout_session_id="cs_marketplace_123",
        order_id="local-order-a",
        payment_intent_id="pi_marketplace_123",
        charge_id="ch_marketplace_123",
        balance_transaction_id="txn_marketplace_123",
        gross_minor=2500,
        provider_fee_minor=75,
        net_minor=2425,
        currency="GBP",
    )
    same = evidence.record(
        event_id="evt_marketplace_retry_456",
        checkout_session_id="cs_marketplace_123",
        order_id="local-order-a",
        payment_intent_id="pi_marketplace_123",
        charge_id="ch_marketplace_123",
        balance_transaction_id="txn_marketplace_123",
        gross_minor=2500,
        provider_fee_minor=75,
        net_minor=2425,
        currency="GBP",
    )
    assert same["checkout_session_id"] == first["checkout_session_id"]

    with pytest.raises(ValueError, match="changed after verification"):
        evidence.record(
            event_id="evt_marketplace_retry_789",
            checkout_session_id="cs_marketplace_123",
            order_id="local-order-a",
            payment_intent_id="pi_marketplace_123",
            charge_id="ch_marketplace_123",
            balance_transaction_id="txn_marketplace_123",
            gross_minor=2500,
            provider_fee_minor=100,
            net_minor=2400,
            currency="GBP",
        )
