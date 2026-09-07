from __future__ import annotations

import pytest

from aura_music_studio.marketplace_orders import MarketplaceOrderStore, record_verified_marketplace_payment
from aura_music_studio.marketplace_settlement import MarketplaceSettlementStore


def _orders(tmp_path):
    return MarketplaceOrderStore(tmp_path / "orders.sqlite3")


def _settlements(tmp_path):
    return MarketplaceSettlementStore(tmp_path / "settlements.sqlite3")


def _creator_order(store: MarketplaceOrderStore, **overrides):
    values = {
        "provider": "stripe",
        "tenant_id": "tenant-a",
        "buyer_user_id": "buyer-1",
        "publication_id": "pub-1",
        "publication_revision": "sha256:publication-v1",
        "creator_user_id": "creator-7",
        "gross_minor": 1001,
        "currency": "GBP",
    }
    values.update(overrides)
    return store.create_order(**values)


def test_creator_order_snapshots_server_authoritative_commercial_facts(tmp_path):
    store = _orders(tmp_path)
    row = _creator_order(store)

    assert row["tenant_id"] == "tenant-a"
    assert row["buyer_user_id"] == "buyer-1"
    assert row["publication_id"] == "pub-1"
    assert row["publication_revision"] == "sha256:publication-v1"
    assert row["creator_user_id"] == "creator-7"
    assert row["gross_minor"] == 1001
    assert row["currency"] == "GBP"
    assert row["esp_owned"] == 0
    assert row["catalogue_owner"] is None
    assert row["provider_checkout_reference"] is None


def test_creator_order_cannot_spoof_owner_catalogue_provenance(tmp_path):
    store = _orders(tmp_path)
    with pytest.raises(ValueError, match="cannot claim ESP catalogue ownership"):
        _creator_order(store, catalogue_owner="mary")


def test_esp_owned_order_requires_mary_or_kev_provenance_and_no_creator_payee(tmp_path):
    store = _orders(tmp_path)

    with pytest.raises(ValueError, match="Mary/Kev"):
        _creator_order(store, creator_user_id=None, esp_owned=True, catalogue_owner="someone")
    with pytest.raises(ValueError, match="cannot name a creator payee"):
        _creator_order(store, esp_owned=True, catalogue_owner="kev")

    row = _creator_order(store, creator_user_id=None, esp_owned=True, catalogue_owner="MARY")
    assert row["esp_owned"] == 1
    assert row["catalogue_owner"] == "mary"
    assert row["creator_user_id"] is None


def test_order_requires_positive_price_and_immutable_publication_revision(tmp_path):
    store = _orders(tmp_path)
    with pytest.raises(ValueError, match="gross amount must be positive"):
        _creator_order(store, gross_minor=0)
    with pytest.raises(ValueError, match="immutable publication provenance"):
        _creator_order(store, publication_revision="")


def test_provider_checkout_binding_is_idempotent_but_not_rebindable(tmp_path):
    store = _orders(tmp_path)
    order = _creator_order(store)

    first = store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_123")
    second = store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_123")
    assert first["provider_checkout_reference"] == "cs_123"
    assert second["provider_checkout_reference"] == "cs_123"

    with pytest.raises(ValueError, match="different provider checkout"):
        store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_other")


def test_provider_checkout_reference_cannot_bind_two_orders(tmp_path):
    store = _orders(tmp_path)
    first = _creator_order(store, publication_id="pub-1")
    second = _creator_order(store, publication_id="pub-2")
    store.bind_provider_checkout(order_id=first["id"], provider_checkout_reference="cs_unique")

    with pytest.raises(ValueError, match="already bound to another"):
        store.bind_provider_checkout(order_id=second["id"], provider_checkout_reference="cs_unique")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "paypal"),
        ("provider_checkout_reference", "cs_wrong"),
        ("tenant_id", "tenant-b"),
        ("buyer_user_id", "buyer-2"),
        ("gross_minor", 999),
        ("currency", "USD"),
    ],
)
def test_verified_payment_fails_closed_on_any_provider_or_identity_mismatch(tmp_path, field, value):
    store = _orders(tmp_path)
    order = _creator_order(store)
    store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_exact")
    evidence = {
        "order_id": order["id"],
        "provider": "stripe",
        "provider_checkout_reference": "cs_exact",
        "tenant_id": "tenant-a",
        "buyer_user_id": "buyer-1",
        "gross_minor": 1001,
        "currency": "GBP",
    }
    evidence[field] = value

    with pytest.raises(ValueError, match="does not match immutable marketplace order"):
        store.verified_payment_snapshot(**evidence)


def test_verified_payment_cannot_settle_before_checkout_is_bound(tmp_path):
    store = _orders(tmp_path)
    order = _creator_order(store)

    with pytest.raises(ValueError, match="does not match immutable marketplace order"):
        store.verified_payment_snapshot(
            order_id=order["id"],
            provider="stripe",
            provider_checkout_reference="cs_unbound",
            tenant_id="tenant-a",
            buyer_user_id="buyer-1",
            gross_minor=1001,
            currency="GBP",
        )


def test_verified_payment_bridge_uses_stored_creator_publication_and_50_50_policy(tmp_path):
    orders = _orders(tmp_path)
    settlements = _settlements(tmp_path)
    order = _creator_order(orders)
    orders.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_paid")

    settlement = record_verified_marketplace_payment(
        orders=orders,
        settlements=settlements,
        order_id=order["id"],
        provider="stripe",
        provider_checkout_reference="cs_paid",
        provider_order_reference="ch_123",
        tenant_id="tenant-a",
        buyer_user_id="buyer-1",
        gross_minor=1001,
        provider_fee_minor=101,
        currency="GBP",
    )

    assert settlement["publication_id"] == "pub-1"
    assert settlement["creator_user_id"] == "creator-7"
    assert settlement["gross_minor"] == 1001
    assert settlement["provider_fee_minor"] == 101
    assert settlement["net_minor"] == 900
    assert settlement["creator_share_minor"] == 450
    assert settlement["admin_pool_share_minor"] == 450
    assert settlement["allocation_policy"] == "creator_marketplace_50_50"


def test_verified_payment_bridge_preserves_esp_owner_catalogue_allocation(tmp_path):
    orders = _orders(tmp_path)
    settlements = _settlements(tmp_path)
    order = _creator_order(
        orders,
        creator_user_id=None,
        esp_owned=True,
        catalogue_owner="kev",
        publication_id="esp-catalogue-1",
    )
    orders.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_owner")

    settlement = record_verified_marketplace_payment(
        orders=orders,
        settlements=settlements,
        order_id=order["id"],
        provider="stripe",
        provider_checkout_reference="cs_owner",
        provider_order_reference="ch_owner",
        tenant_id="tenant-a",
        buyer_user_id="buyer-1",
        gross_minor=1001,
        provider_fee_minor=1,
        currency="GBP",
    )

    assert settlement["creator_user_id"] is None
    assert settlement["catalogue_owner"] == "kev"
    assert settlement["creator_share_minor"] == 0
    assert settlement["admin_pool_share_minor"] == 1000
    assert settlement["allocation_policy"] == "esp_owner_catalogue_100"


def test_verified_payment_bridge_rejects_webhook_amount_tampering_before_ledger_mutation(tmp_path):
    orders = _orders(tmp_path)
    settlements = _settlements(tmp_path)
    order = _creator_order(orders)
    orders.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_paid")

    with pytest.raises(ValueError, match="does not match immutable marketplace order"):
        record_verified_marketplace_payment(
            orders=orders,
            settlements=settlements,
            order_id=order["id"],
            provider="stripe",
            provider_checkout_reference="cs_paid",
            provider_order_reference="ch_tampered",
            tenant_id="tenant-a",
            buyer_user_id="buyer-1",
            gross_minor=1,
            provider_fee_minor=0,
            currency="GBP",
        )

    with pytest.raises(ValueError, match="Unknown marketplace order"):
        settlements.balance_for_order("ch_tampered")


def test_verified_payment_bridge_requires_provider_order_reference(tmp_path):
    orders = _orders(tmp_path)
    settlements = _settlements(tmp_path)
    order = _creator_order(orders)
    orders.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_paid")

    with pytest.raises(ValueError, match="provider order reference"):
        record_verified_marketplace_payment(
            orders=orders,
            settlements=settlements,
            order_id=order["id"],
            provider="stripe",
            provider_checkout_reference="cs_paid",
            provider_order_reference="",
            tenant_id="tenant-a",
            buyer_user_id="buyer-1",
            gross_minor=1001,
            provider_fee_minor=100,
            currency="GBP",
        )
