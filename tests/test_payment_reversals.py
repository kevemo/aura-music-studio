from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.payment_reversals import PaymentReversalStore


def _user(store: AccountStore) -> dict:
    signup = store.signup("refunds@example.com", "Refund Member", "StrongPassword123!", "free")
    store.decide_membership(signup.approval_token, "approve", "Kev")
    user = store.get_user(signup.user_id)
    assert user is not None
    return user


def _refund(*, refund_id: str = "re_1", payment_intent: str = "pi_1", amount: int = 125, status: str = "succeeded") -> dict:
    return {
        "id": f"evt_{refund_id}",
        "type": "refund.created",
        "data": {
            "object": {
                "id": refund_id,
                "payment_intent": payment_intent,
                "amount": amount,
                "currency": "gbp",
                "status": status,
                "reason": "requested_by_customer",
            }
        },
    }


def test_verified_refund_is_linked_idempotent_and_append_only(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    store = PaymentReversalStore(db)
    store.bind_credit_payment(
        payment_intent_id="pi_1",
        receipt_reference="stripe:checkout:cs_1",
        user_id=user["id"],
        amount_minor=299,
        currency="GBP",
    )

    first = store.record_stripe_refund(_refund())
    second = store.record_stripe_refund(_refund())
    assert first is not None and second is not None
    assert first["linked"] is True
    assert second["linked"] is True
    assert first["id"] == second["id"]
    assert first["amount_minor"] == 125

    summary = store.summary()
    assert summary["verified_refunds_minor"] == {"GBP": 125}
    assert summary["unmatched_verified_refund_count"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM verified_payment_adjustments").fetchone()[0] == 1


def test_partial_refunds_cannot_exceed_original_purchase(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    store = PaymentReversalStore(db)
    store.bind_credit_payment(
        payment_intent_id="pi_1",
        receipt_reference="stripe:checkout:cs_1",
        user_id=user["id"],
        amount_minor=299,
        currency="GBP",
    )
    store.record_stripe_refund(_refund(refund_id="re_1", amount=200))

    with pytest.raises(ValueError, match="Cumulative Stripe refunds exceed"):
        store.record_stripe_refund(_refund(refund_id="re_2", amount=100))


def test_pending_refund_does_not_reduce_finance_until_succeeded(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    store = PaymentReversalStore(db)
    store.bind_credit_payment(
        payment_intent_id="pi_1",
        receipt_reference="stripe:checkout:cs_1",
        user_id=user["id"],
        amount_minor=299,
        currency="GBP",
    )

    assert store.record_stripe_refund(_refund(status="pending")) is None
    assert store.summary()["verified_refunds_minor"] == {}

    event = _refund(status="succeeded")
    event["type"] = "refund.updated"
    recorded = store.record_stripe_refund(event)
    assert recorded is not None and recorded["linked"] is True
    assert store.summary()["verified_refunds_minor"] == {"GBP": 125}


def test_unmatched_verified_refund_is_preserved_but_not_linked(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    AccountStore(db)
    store = PaymentReversalStore(db)

    result = store.record_stripe_refund(_refund(payment_intent="pi_unknown", amount=99))
    assert result is not None
    assert result["linked"] is False
    summary = store.summary()
    assert summary["verified_refunds_minor"] == {}
    assert summary["unmatched_verified_refunds_minor"] == {"GBP": 99}
    assert summary["unmatched_verified_refund_count"] == 1


def test_refund_finance_evidence_never_mutates_roles_or_credit_wallet(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    store = PaymentReversalStore(db)
    store.bind_credit_payment(
        payment_intent_id="pi_1",
        receipt_reference="stripe:checkout:cs_1",
        user_id=user["id"],
        amount_minor=299,
        currency="GBP",
    )
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE test_role_boundary(user_id TEXT PRIMARY KEY, role TEXT NOT NULL)")
        con.execute("INSERT INTO test_role_boundary(user_id,role) VALUES (?,?)", (user["id"], "creator"))

    store.record_stripe_refund(_refund(amount=299))

    with sqlite3.connect(db) as con:
        role = con.execute("SELECT role FROM test_role_boundary WHERE user_id=?", (user["id"],)).fetchone()
        wallet_rows = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='credit_transactions'"
        ).fetchone()[0]
    assert role == ("creator",)
    assert wallet_rows == 0
