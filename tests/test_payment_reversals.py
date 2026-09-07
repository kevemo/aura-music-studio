from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.commerce_receipts import CommerceReceiptStore
from aura_music_studio.credit_wallet import CreditWalletStore
from aura_music_studio.payment_reversals import PaymentReversalStore


def _user(store: AccountStore, email: str = "refund@example.com") -> dict:
    signup = store.signup(email, "Refund Member", "StrongPassword123!", "free")
    store.decide_membership(signup.approval_token, "approve", "Kev")
    user = store.get_user(signup.user_id)
    assert user is not None
    return user


def _receipt(db, user_id: str, *, reference: str = "stripe:checkout:cs_refund", amount: int = 500):
    return CommerceReceiptStore(db).record(
        provider="stripe",
        kind="credit_topup",
        reference=reference,
        user_id=user_id,
        pack_id="coins-test",
        amount_minor=amount,
        currency="GBP",
        units=1000,
        status="paid",
    )


def _refund_event(
    refund_id: str,
    payment_intent: str,
    amount: int,
    *,
    status: str = "succeeded",
    currency: str = "gbp",
    event_type: str = "refund.updated",
) -> dict:
    return {
        "id": f"evt_{refund_id}",
        "type": event_type,
        "data": {
            "object": {
                "id": refund_id,
                "payment_intent": payment_intent,
                "amount": amount,
                "currency": currency,
                "status": status,
                "reason": "requested_by_customer",
            }
        },
    }


def test_payment_correlation_requires_matching_verified_receipt(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    reversals = PaymentReversalStore(db)

    with pytest.raises(ValueError, match="verified local receipt"):
        reversals.bind_credit_payment(
            payment_intent_id="pi_missing",
            receipt_reference="stripe:checkout:cs_missing",
            user_id=user["id"],
            amount_minor=500,
            currency="GBP",
        )

    receipt = _receipt(db, user["id"])
    first = reversals.bind_credit_payment(
        payment_intent_id="pi_verified",
        receipt_reference=receipt["reference"],
        user_id=user["id"],
        amount_minor=500,
        currency="GBP",
    )
    duplicate = reversals.bind_credit_payment(
        payment_intent_id="pi_verified",
        receipt_reference=receipt["reference"],
        user_id=user["id"],
        amount_minor=500,
        currency="GBP",
    )
    assert first["payment_intent_id"] == "pi_verified"
    assert duplicate["receipt_reference"] == receipt["reference"]

    # A caller cannot alter the financial amount on an existing correlation. The verified
    # local receipt mismatch is detected before duplicate-correlation reuse is even considered.
    with pytest.raises(ValueError, match="does not match the verified local receipt"):
        reversals.bind_credit_payment(
            payment_intent_id="pi_verified",
            receipt_reference=receipt["reference"],
            user_id=user["id"],
            amount_minor=499,
            currency="GBP",
        )


def test_pending_refund_does_not_reduce_finance_until_succeeded(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    receipt = _receipt(db, user["id"])
    reversals = PaymentReversalStore(db)
    reversals.bind_credit_payment(
        payment_intent_id="pi_pending",
        receipt_reference=receipt["reference"],
        user_id=user["id"],
        amount_minor=500,
        currency="GBP",
    )

    pending = reversals.record_stripe_refund(
        _refund_event("re_pending", "pi_pending", 200, status="pending", event_type="refund.created")
    )
    assert pending is None
    assert reversals.summary()["verified_refunds_minor"] == {}

    succeeded = reversals.record_stripe_refund(
        _refund_event("re_pending", "pi_pending", 200, status="succeeded")
    )
    assert succeeded is not None
    assert succeeded["linked"] is True
    assert reversals.summary()["verified_refunds_minor"] == {"GBP": 200}


def test_refunds_are_idempotent_and_cannot_exceed_original_purchase(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    receipt = _receipt(db, user["id"], amount=500)
    reversals = PaymentReversalStore(db)
    reversals.bind_credit_payment(
        payment_intent_id="pi_partial",
        receipt_reference=receipt["reference"],
        user_id=user["id"],
        amount_minor=500,
        currency="GBP",
    )

    first = reversals.record_stripe_refund(_refund_event("re_1", "pi_partial", 200))
    duplicate = reversals.record_stripe_refund(_refund_event("re_1", "pi_partial", 200))
    second = reversals.record_stripe_refund(_refund_event("re_2", "pi_partial", 250))
    assert first is not None and duplicate is not None and second is not None
    assert duplicate["id"] == first["id"]
    assert reversals.summary()["verified_refunds_minor"] == {"GBP": 450}

    with pytest.raises(ValueError, match="Cumulative"):
        reversals.record_stripe_refund(_refund_event("re_3", "pi_partial", 100))

    with pytest.raises(ValueError, match="currency"):
        reversals.record_stripe_refund(
            _refund_event("re_currency", "pi_partial", 25, currency="usd")
        )


def test_unmatched_verified_refund_is_preserved_then_can_be_reconciled(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    reversals = PaymentReversalStore(db)
    event = _refund_event("re_late", "pi_late", 125)

    unmatched = reversals.record_stripe_refund(event)
    assert unmatched is not None
    assert unmatched["linked"] is False
    summary = reversals.summary()
    assert summary["verified_refunds_minor"] == {}
    assert summary["unmatched_verified_refunds_minor"] == {"GBP": 125}
    assert summary["unmatched_verified_refund_count"] == 1

    receipt = _receipt(db, user["id"], reference="stripe:checkout:cs_late", amount=500)
    reversals.bind_credit_payment(
        payment_intent_id="pi_late",
        receipt_reference=receipt["reference"],
        user_id=user["id"],
        amount_minor=500,
        currency="GBP",
    )
    linked = reversals.record_stripe_refund(event)
    assert linked is not None and linked["linked"] is True
    summary = reversals.summary()
    assert summary["verified_refunds_minor"] == {"GBP": 125}
    assert summary["unmatched_verified_refund_count"] == 0


def test_refund_finance_evidence_does_not_mutate_wallet_membership_or_esp_role(tmp_path):
    db = tmp_path / "refunds.sqlite3"
    accounts = AccountStore(db)
    user = _user(accounts)
    wallet = CreditWalletStore(db)
    wallet.adjust(
        user["id"],
        1000,
        kind="purchase",
        reason="Verified purchase",
        actor="test",
        reference="wallet:test-purchase",
    )
    receipt = _receipt(db, user["id"], amount=500)
    reversals = PaymentReversalStore(db)
    reversals.bind_credit_payment(
        payment_intent_id="pi_boundary",
        receipt_reference=receipt["reference"],
        user_id=user["id"],
        amount_minor=500,
        currency="GBP",
    )

    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE test_esp_role_boundary (user_id TEXT PRIMARY KEY, role TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO test_esp_role_boundary(user_id,role) VALUES (?,?)",
            (user["id"], "creator"),
        )

    before_user = accounts.get_user(user["id"])
    before_balance = wallet.balance(user["id"])
    reversals.record_stripe_refund(_refund_event("re_boundary", "pi_boundary", 300))
    after_user = accounts.get_user(user["id"])
    after_balance = wallet.balance(user["id"])

    assert before_user is not None and after_user is not None
    assert after_user["plan_id"] == before_user["plan_id"]
    assert after_user["status"] == before_user["status"]
    assert after_balance == before_balance == 1000
    with sqlite3.connect(db) as con:
        role = con.execute(
            "SELECT role FROM test_esp_role_boundary WHERE user_id=?", (user["id"],)
        ).fetchone()
        receipt_status = con.execute(
            "SELECT status FROM commerce_receipts WHERE reference=?", (receipt["reference"],)
        ).fetchone()
    assert role == ("creator",)
    assert receipt_status == ("paid",)
