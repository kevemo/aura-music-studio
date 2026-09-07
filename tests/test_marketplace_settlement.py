from __future__ import annotations

import pytest

from aura_music_studio.marketplace_settlement import MarketplaceSettlementStore


def _store(tmp_path):
    return MarketplaceSettlementStore(tmp_path / "marketplace.sqlite3")


def test_creator_marketplace_order_records_verified_financial_snapshot_and_50_50_split(tmp_path):
    store = _store(tmp_path)

    row = store.record_verified_order(
        provider="stripe",
        provider_order_reference="order_creator_1",
        tenant_id="tenant-a",
        publication_id="pub-1",
        creator_user_id="creator-1",
        gross_minor=1_200,
        provider_fee_minor=201,
        currency="gbp",
    )

    assert row["gross_minor"] == 1_200
    assert row["provider_fee_minor"] == 201
    assert row["net_minor"] == 999
    assert row["currency"] == "GBP"
    assert row["allocation_policy"] == "creator_marketplace_50_50"
    assert row["creator_share_minor"] == 499
    assert row["admin_pool_share_minor"] == 500
    assert row["esp_owned"] == 0
    assert row["catalogue_owner"] is None


def test_owner_catalogue_requires_explicit_mary_or_kev_provenance_and_allocates_100_percent(tmp_path):
    store = _store(tmp_path)

    row = store.record_verified_order(
        provider="stripe",
        provider_order_reference="order_owner_1",
        tenant_id="tenant-a",
        publication_id="esp-track-1",
        creator_user_id=None,
        gross_minor=2_000,
        provider_fee_minor=200,
        currency="GBP",
        esp_owned=True,
        catalogue_owner="Mary",
    )

    assert row["allocation_policy"] == "esp_owner_catalogue_100"
    assert row["creator_share_minor"] == 0
    assert row["admin_pool_share_minor"] == 1_800
    assert row["catalogue_owner"] == "mary"

    with pytest.raises(ValueError, match="Mary/Kev provenance"):
        store.record_verified_order(
            provider="stripe",
            provider_order_reference="order_owner_bad",
            tenant_id="tenant-a",
            publication_id="esp-track-2",
            creator_user_id=None,
            gross_minor=2_000,
            provider_fee_minor=200,
            currency="GBP",
            esp_owned=True,
            catalogue_owner="other-admin",
        )


def test_creator_publication_cannot_smuggle_owner_catalogue_provenance(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="cannot be set for a creator publication"):
        store.record_verified_order(
            provider="stripe",
            provider_order_reference="order_creator_spoof",
            tenant_id="tenant-a",
            publication_id="pub-2",
            creator_user_id="creator-1",
            gross_minor=1_000,
            provider_fee_minor=100,
            currency="GBP",
            catalogue_owner="kev",
        )


def test_order_reference_is_idempotent_but_financial_reuse_fails_closed(tmp_path):
    store = _store(tmp_path)
    payload = dict(
        provider="stripe",
        provider_order_reference="order_idempotent",
        tenant_id="tenant-a",
        publication_id="pub-3",
        creator_user_id="creator-2",
        gross_minor=1_000,
        provider_fee_minor=100,
        currency="GBP",
    )

    first = store.record_verified_order(**payload)
    second = store.record_verified_order(**payload)
    assert second["id"] == first["id"]

    with pytest.raises(ValueError, match="reused with different financial data"):
        store.record_verified_order(**{**payload, "gross_minor": 1_001})


def test_provider_fee_cannot_exceed_gross(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="gross/fee amounts are invalid"):
        store.record_verified_order(
            provider="stripe",
            provider_order_reference="order_bad_fee",
            tenant_id="tenant-a",
            publication_id="pub-4",
            creator_user_id="creator-3",
            gross_minor=100,
            provider_fee_minor=101,
            currency="GBP",
        )


def test_verified_partial_and_full_reversals_reduce_each_allocation_deterministically(tmp_path):
    store = _store(tmp_path)
    store.record_verified_order(
        provider="stripe",
        provider_order_reference="order_refund",
        tenant_id="tenant-a",
        publication_id="pub-5",
        creator_user_id="creator-4",
        gross_minor=1_000,
        provider_fee_minor=1,
        currency="GBP",
    )

    first = store.record_verified_reversal(
        provider="stripe",
        provider_reversal_reference="refund_1",
        provider_order_reference="order_refund",
        amount_minor=333,
        currency="GBP",
    )
    assert first["creator_share_minor"] == 166
    assert first["admin_pool_share_minor"] == 167

    second = store.record_verified_reversal(
        provider="stripe",
        provider_reversal_reference="refund_2",
        provider_order_reference="order_refund",
        amount_minor=666,
        currency="GBP",
    )
    assert second["creator_share_minor"] == 333
    assert second["admin_pool_share_minor"] == 333

    balance = store.balance_for_order("order_refund")
    assert balance["remaining_net_minor"] == 0
    assert balance["creator_share_remaining_minor"] == 0
    assert balance["admin_pool_share_remaining_minor"] == 0
    assert balance["payout_initiated"] is False
    assert balance["provider_reconciled"] is False


def test_reversals_fail_closed_on_unknown_order_mismatch_and_over_refund(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="unknown order"):
        store.record_verified_reversal(
            provider="stripe",
            provider_reversal_reference="refund_unknown",
            provider_order_reference="missing",
            amount_minor=1,
            currency="GBP",
        )

    store.record_verified_order(
        provider="stripe",
        provider_order_reference="order_guarded",
        tenant_id="tenant-a",
        publication_id="pub-6",
        creator_user_id="creator-5",
        gross_minor=500,
        provider_fee_minor=0,
        currency="GBP",
    )

    with pytest.raises(ValueError, match="provider/currency does not match"):
        store.record_verified_reversal(
            provider="paypal",
            provider_reversal_reference="refund_wrong_provider",
            provider_order_reference="order_guarded",
            amount_minor=10,
            currency="GBP",
        )

    with pytest.raises(ValueError, match="cannot exceed verified net"):
        store.record_verified_reversal(
            provider="stripe",
            provider_reversal_reference="refund_too_large",
            provider_order_reference="order_guarded",
            amount_minor=501,
            currency="GBP",
        )


def test_reversal_reference_is_idempotent_but_reuse_with_changed_amount_fails(tmp_path):
    store = _store(tmp_path)
    store.record_verified_order(
        provider="stripe",
        provider_order_reference="order_reversal_idempotent",
        tenant_id="tenant-a",
        publication_id="pub-7",
        creator_user_id="creator-6",
        gross_minor=500,
        provider_fee_minor=0,
        currency="GBP",
    )
    payload = dict(
        provider="stripe",
        provider_reversal_reference="refund_idempotent",
        provider_order_reference="order_reversal_idempotent",
        amount_minor=100,
        currency="GBP",
    )

    first = store.record_verified_reversal(**payload)
    second = store.record_verified_reversal(**payload)
    assert second["id"] == first["id"]

    with pytest.raises(ValueError, match="reused with different data"):
        store.record_verified_reversal(**{**payload, "amount_minor": 101})
