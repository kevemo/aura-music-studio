from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.commerce_receipts import CommerceReceiptStore
from aura_music_studio.owner_auth import OWNER_COOKIE, OwnerSessionStore
from aura_music_studio.owner_finance import OwnerFinanceService, router
from aura_music_studio.payment_reversals import PaymentReversalStore
from aura_music_studio.subscriptions import SubscriptionLedger


def _approved_user(store: AccountStore, email: str, plan_id: str = "base") -> dict:
    signup = store.signup(email, email.split("@", 1)[0].title(), "StrongPassword123!", plan_id)
    store.decide_membership(signup.approval_token, "approve", "Kev")
    user = store.get_user(signup.user_id)
    assert user is not None
    return user


def test_finance_snapshot_uses_verified_ledgers_and_never_claims_bank_settlement(tmp_path):
    db = tmp_path / "finance.sqlite3"
    accounts = AccountStore(db)
    base = _approved_user(accounts, "base@example.com", "base")
    pro = _approved_user(accounts, "pro@example.com", "pro")
    ledger = SubscriptionLedger(accounts)
    ledger.verify_payment(base["id"], "base", "stripe:checkout:cs_base")
    ledger.verify_payment(pro["id"], "pro", "paypal-invoice-verified-1")

    receipts = CommerceReceiptStore(db)
    receipts.record(
        provider="stripe",
        kind="credit_topup",
        reference="stripe:checkout:cs_credits",
        user_id=base["id"],
        pack_id="credits-500",
        amount_minor=299,
        currency="GBP",
        units=500,
    )

    snapshot = OwnerFinanceService(db).snapshot()

    assert snapshot["subscription_receipts_minor"]["GBP"] == 1498
    assert snapshot["credit_topup_receipts_minor"]["GBP"] == 299
    assert snapshot["verified_gross_receipts_minor"]["GBP"] == 1797
    assert snapshot["verified_refunds_minor"] == {}
    assert snapshot["verified_net_receipts_minor"]["GBP"] == 1797
    assert snapshot["unmatched_verified_refund_count"] == 0
    assert snapshot["active_paid_subscriptions"] == {"base": 1, "pro": 1}
    assert snapshot["estimated_monthly_recurring_access_value_minor_gbp"] == 1498
    assert snapshot["purchased_credits"] == 500
    assert snapshot["verified_receipts_by_provider_minor"]["stripe"] == 798
    assert snapshot["verified_receipts_by_provider_minor"]["verified_external"] == 999
    assert snapshot["settlement"]["bank_details_stored_in_application"] is False
    assert snapshot["settlement"]["bank_balance_known_to_application"] is False
    assert snapshot["settlement"]["payout_status_known_to_application"] is False
    serialized = repr(snapshot).lower()
    assert "sort code" not in serialized
    assert "account number" not in serialized
    assert "creator" in snapshot["role_boundary"].lower()


def test_finance_net_subtracts_only_linked_successful_refunds(tmp_path):
    db = tmp_path / "finance.sqlite3"
    accounts = AccountStore(db)
    user = _approved_user(accounts, "refund@example.com", "base")
    CommerceReceiptStore(db).record(
        provider="stripe",
        kind="credit_topup",
        reference="stripe:checkout:cs_refund",
        user_id=user["id"],
        pack_id="credits-500",
        amount_minor=299,
        currency="GBP",
        units=500,
    )
    reversals = PaymentReversalStore(db)
    reversals.bind_credit_payment(
        payment_intent_id="pi_refund",
        receipt_reference="stripe:checkout:cs_refund",
        user_id=user["id"],
        amount_minor=299,
        currency="GBP",
    )
    reversals.record_stripe_refund(
        {
            "id": "evt_refund",
            "type": "refund.created",
            "data": {
                "object": {
                    "id": "re_refund",
                    "payment_intent": "pi_refund",
                    "amount": 100,
                    "currency": "gbp",
                    "status": "succeeded",
                }
            },
        }
    )

    snapshot = OwnerFinanceService(db).snapshot()
    assert snapshot["verified_gross_receipts_minor"] == {"GBP": 299}
    assert snapshot["verified_refunds_minor"] == {"GBP": 100}
    assert snapshot["verified_net_receipts_minor"] == {"GBP": 199}
    assert len(snapshot["recent_verified_refunds"]) == 1


def test_credit_gross_totals_cover_full_ledger_while_recent_history_stays_bounded(tmp_path):
    db = tmp_path / "finance.sqlite3"
    accounts = AccountStore(db)
    user = _approved_user(accounts, "volume@example.com", "base")
    receipts = CommerceReceiptStore(db)

    for index in range(7):
        receipts.record(
            provider="stripe",
            kind="credit_topup",
            reference=f"stripe:checkout:cs_volume_{index}",
            user_id=user["id"],
            pack_id="credits-volume",
            amount_minor=100,
            currency="GBP",
            units=10,
        )

    snapshot = OwnerFinanceService(db).snapshot(recent_limit=3)
    assert snapshot["credit_topup_receipts_minor"] == {"GBP": 700}
    assert snapshot["verified_gross_receipts_minor"] == {"GBP": 700}
    assert snapshot["verified_net_receipts_minor"] == {"GBP": 700}
    assert snapshot["verified_receipts_by_provider_minor"] == {"stripe": 700}
    assert snapshot["purchased_credits"] == 70
    assert len(snapshot["recent_verified_receipts"]) == 3
    assert all(item["kind"] == "credit_topup" for item in snapshot["recent_verified_receipts"])


def test_commerce_receipt_is_idempotent_and_rejects_reference_reuse(tmp_path):
    db = tmp_path / "finance.sqlite3"
    accounts = AccountStore(db)
    user = _approved_user(accounts, "member@example.com", "base")
    store = CommerceReceiptStore(db)
    first = store.record(
        provider="stripe",
        kind="credit_topup",
        reference="stripe:checkout:cs_same",
        user_id=user["id"],
        pack_id="small",
        amount_minor=199,
        currency="GBP",
        units=250,
    )
    duplicate = store.record(
        provider="stripe",
        kind="credit_topup",
        reference="stripe:checkout:cs_same",
        user_id=user["id"],
        pack_id="small",
        amount_minor=199,
        currency="GBP",
        units=250,
    )
    assert duplicate["id"] == first["id"]

    with pytest.raises(ValueError, match="reused"):
        store.record(
            provider="stripe",
            kind="credit_topup",
            reference="stripe:checkout:cs_same",
            user_id=user["id"],
            pack_id="small",
            amount_minor=999,
            currency="GBP",
            units=250,
        )


def test_owner_finance_json_requires_owner_session(monkeypatch, tmp_path):
    db = tmp_path / "finance.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    AccountStore(db)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    denied = client.get("/owner/finance.json")
    assert denied.status_code == 403

    token = OwnerSessionStore(db).create()
    allowed = client.get("/owner/finance.json", cookies={OWNER_COOKIE: token})
    assert allowed.status_code == 200
    assert allowed.json()["basis"] == "verified_local_payment_evidence"
    assert "verified_net_receipts_minor" in allowed.json()
    assert allowed.headers["cache-control"] == "private, no-store"


def test_finance_snapshot_does_not_modify_esp_role_state(tmp_path):
    db = tmp_path / "finance.sqlite3"
    accounts = AccountStore(db)
    user = _approved_user(accounts, "creator@example.com", "base")
    with sqlite3.connect(db) as con:
        con.execute(
            """CREATE TABLE test_esp_role_boundary (
                   user_id TEXT PRIMARY KEY, role TEXT NOT NULL
               )"""
        )
        con.execute("INSERT INTO test_esp_role_boundary(user_id,role) VALUES (?,?)", (user["id"], "creator"))

    OwnerFinanceService(db).snapshot()

    with sqlite3.connect(db) as con:
        role = con.execute("SELECT role FROM test_esp_role_boundary WHERE user_id=?", (user["id"],)).fetchone()
    assert role == ("creator",)
