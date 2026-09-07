from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.stripe_marketplace_bank_reconciliation import (
    StripeMarketplaceBankReconciliationStore,
    reconcile_stripe_marketplace_payout_to_bank,
)
from aura_music_studio.stripe_marketplace_payout_evidence import StripePayoutEvidenceStore


def _seed_paid_reconciled_payout(tmp_path, *, payout_id="po_test123", amount=12345, currency="GBP"):
    db = tmp_path / "finance.sqlite3"
    store = StripePayoutEvidenceStore(db)
    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO stripe_payout_events
               (event_id,payout_id,event_type,status,amount_minor,currency,automatic,arrival_date,
                reconciliation_status,balance_transaction_id,failure_balance_transaction_id,
                failure_code,recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "evt_paid123",
                payout_id,
                "payout.paid",
                "paid",
                amount,
                currency,
                1,
                1788200000,
                "completed",
                "txn_payout123",
                None,
                None,
                "2026-08-31T18:00:00+00:00",
            ),
        )
        con.execute(
            """INSERT INTO stripe_payout_reconciliations
               (payout_id,verified_event_id,transaction_set_sha256,transaction_count,
                marketplace_transaction_count,marketplace_contribution_minor,currency,
                provider_reconciled_at) VALUES (?,?,?,?,?,?,?,?)""",
            (
                payout_id,
                "evt_recon123",
                "a" * 64,
                2,
                2,
                amount,
                currency,
                "2026-08-31T18:05:00+00:00",
            ),
        )
    return db, store


def _bank_record(
    *,
    payout_id="po_test123",
    transaction_id="bank_tx_123",
    amount=12345,
    currency="GBP",
    provider="open-banking-test",
):
    return {
        "provider": provider,
        "transaction_id": transaction_id,
        "status": "booked",
        "direction": "credit",
        "stripe_payout_id": payout_id,
        "amount_minor": amount,
        "currency": currency,
        "booked_at": "2026-08-31T18:10:00Z",
    }


def test_reconciles_only_paid_provider_reconciled_payout(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)

    result = reconcile_stripe_marketplace_payout_to_bank(
        payout_id="po_test123",
        bank_transaction_id="bank_tx_123",
        fetch_bank_transaction=lambda transaction_id: _bank_record(transaction_id=transaction_id),
        payout_store=payouts,
        bank_store=banks,
    )

    assert result["bank_reconciled"] is True
    assert result["provider_reconciled"] is True
    assert result["payout_initiated"] is False
    assert result["marketplace_allocation_mutated"] is False
    assert result["subscription_effect"] == "none"
    assert result["creation_coin_effect"] == "none"
    assert result["esp_role_effect"] == "none"
    assert len(result["evidence_sha256"]) == 64


def test_bank_evidence_does_not_store_sensitive_bank_fields(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)
    raw = _bank_record()
    raw.update(
        {
            "account_number": "12345678",
            "sort_code": "00-00-00",
            "iban": "GB00TEST00000000000000",
            "description": "sensitive free form statement description",
            "access_token": "must-not-persist",
        }
    )

    reconcile_stripe_marketplace_payout_to_bank(
        payout_id="po_test123",
        bank_transaction_id="bank_tx_123",
        fetch_bank_transaction=lambda _transaction_id: raw,
        payout_store=payouts,
        bank_store=banks,
    )
    stored = banks.get("po_test123")
    assert stored is not None
    serialized = repr(stored)
    for secret in ("12345678", "00-00-00", "GB00TEST", "sensitive free form", "must-not-persist"):
        assert secret not in serialized


def test_rejects_unpaid_payout(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE stripe_payout_events SET status='in_transit' WHERE payout_id='po_test123'")
    banks = StripeMarketplaceBankReconciliationStore(db)

    with pytest.raises(ValueError, match="not provider-confirmed paid"):
        reconcile_stripe_marketplace_payout_to_bank(
            payout_id="po_test123",
            bank_transaction_id="bank_tx_123",
            fetch_bank_transaction=lambda transaction_id: _bank_record(transaction_id=transaction_id),
            payout_store=payouts,
            bank_store=banks,
        )


def test_rejects_payout_without_provider_reconciliation(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM stripe_payout_reconciliations WHERE payout_id='po_test123'")
    banks = StripeMarketplaceBankReconciliationStore(db)

    with pytest.raises(ValueError, match="has not completed provider reconciliation"):
        reconcile_stripe_marketplace_payout_to_bank(
            payout_id="po_test123",
            bank_transaction_id="bank_tx_123",
            fetch_bank_transaction=lambda transaction_id: _bank_record(transaction_id=transaction_id),
            payout_store=payouts,
            bank_store=banks,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "pending", "not booked"),
        ("direction", "debit", "not an inbound credit"),
        ("amount_minor", 999, "amount does not match"),
        ("currency", "USD", "currency does not match"),
        ("stripe_payout_id", "po_other123", "different Stripe payout"),
    ],
)
def test_rejects_bank_evidence_that_does_not_match_verified_payout(
    tmp_path, field, value, message
):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)
    raw = _bank_record()
    raw[field] = value

    with pytest.raises(ValueError, match=message):
        reconcile_stripe_marketplace_payout_to_bank(
            payout_id="po_test123",
            bank_transaction_id="bank_tx_123",
            fetch_bank_transaction=lambda _transaction_id: raw,
            payout_store=payouts,
            bank_store=banks,
        )


def test_rejects_adapter_returning_different_transaction(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)

    with pytest.raises(ValueError, match="different transaction"):
        reconcile_stripe_marketplace_payout_to_bank(
            payout_id="po_test123",
            bank_transaction_id="bank_tx_123",
            fetch_bank_transaction=lambda _transaction_id: _bank_record(transaction_id="bank_tx_999"),
            payout_store=payouts,
            bank_store=banks,
        )


def test_reconciliation_is_idempotent_for_identical_canonical_evidence(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)
    fetch = lambda transaction_id: _bank_record(transaction_id=transaction_id)

    first = reconcile_stripe_marketplace_payout_to_bank(
        payout_id="po_test123",
        bank_transaction_id="bank_tx_123",
        fetch_bank_transaction=fetch,
        payout_store=payouts,
        bank_store=banks,
    )
    second = reconcile_stripe_marketplace_payout_to_bank(
        payout_id="po_test123",
        bank_transaction_id="bank_tx_123",
        fetch_bank_transaction=fetch,
        payout_store=payouts,
        bank_store=banks,
    )
    assert first["evidence_sha256"] == second["evidence_sha256"]


def test_rejects_changed_bank_evidence_after_reconciliation(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)
    reconcile_stripe_marketplace_payout_to_bank(
        payout_id="po_test123",
        bank_transaction_id="bank_tx_123",
        fetch_bank_transaction=lambda transaction_id: _bank_record(transaction_id=transaction_id),
        payout_store=payouts,
        bank_store=banks,
    )
    changed = _bank_record()
    changed["booked_at"] = "2026-08-31T18:11:00Z"

    with pytest.raises(ValueError, match="changed after verification"):
        reconcile_stripe_marketplace_payout_to_bank(
            payout_id="po_test123",
            bank_transaction_id="bank_tx_123",
            fetch_bank_transaction=lambda _transaction_id: changed,
            payout_store=payouts,
            bank_store=banks,
        )


def test_one_bank_transaction_cannot_reconcile_two_payouts(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)
    reconcile_stripe_marketplace_payout_to_bank(
        payout_id="po_test123",
        bank_transaction_id="bank_tx_123",
        fetch_bank_transaction=lambda transaction_id: _bank_record(transaction_id=transaction_id),
        payout_store=payouts,
        bank_store=banks,
    )

    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO stripe_payout_events
               (event_id,payout_id,event_type,status,amount_minor,currency,automatic,arrival_date,
                reconciliation_status,balance_transaction_id,failure_balance_transaction_id,
                failure_code,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "evt_paid999", "po_other999", "payout.paid", "paid", 12345, "GBP", 1,
                1788200000, "completed", "txn_payout999", None, None,
                "2026-08-31T18:00:00+00:00",
            ),
        )
        con.execute(
            """INSERT INTO stripe_payout_reconciliations
               (payout_id,verified_event_id,transaction_set_sha256,transaction_count,
                marketplace_transaction_count,marketplace_contribution_minor,currency,
                provider_reconciled_at) VALUES (?,?,?,?,?,?,?,?)""",
            ("po_other999", "evt_recon999", "b" * 64, 1, 1, 12345, "GBP", "2026-08-31T18:05:00+00:00"),
        )

    with pytest.raises(ValueError, match="already assigned to another Stripe payout"):
        banks.record(
            "po_other999",
            {
                "provider": "open-banking-test",
                "transaction_id": "bank_tx_123",
                "stripe_payout_id": "po_other999",
                "amount_minor": 12345,
                "currency": "GBP",
                "booked_at": "2026-08-31T18:10:00+00:00",
                "evidence_sha256": "c" * 64,
            },
        )


def test_no_browser_boolean_can_substitute_for_bank_adapter(tmp_path):
    db, payouts = _seed_paid_reconciled_payout(tmp_path)
    banks = StripeMarketplaceBankReconciliationStore(db)

    with pytest.raises(ValueError, match="non-object transaction"):
        reconcile_stripe_marketplace_payout_to_bank(
            payout_id="po_test123",
            bank_transaction_id="bank_tx_123",
            fetch_bank_transaction=lambda _transaction_id: True,
            payout_store=payouts,
            bank_store=banks,
        )
