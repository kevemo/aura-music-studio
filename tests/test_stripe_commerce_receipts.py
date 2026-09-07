from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.credit_wallet import CreditWalletStore
from aura_music_studio.payment_reversals import PaymentReversalStore
from aura_music_studio import stripe_commerce_receipts as commerce_overlay


def _active_user(store: AccountStore) -> dict:
    signup = store.signup("credits@example.com", "Credits Member", "StrongPassword123!", "free")
    store.decide_membership(signup.approval_token, "approve", "Kev")
    user = store.get_user(signup.user_id)
    assert user is not None
    return user


def _event(user_id: str) -> dict:
    return {
        "id": "evt_credit_paid_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_credit_paid_1",
                "payment_intent": "pi_credit_paid_1",
                "client_reference_id": user_id,
                "payment_status": "paid",
                "amount_total": 299,
                "currency": "gbp",
                "metadata": {
                    "user_id": user_id,
                    "purchase_kind": "credit_topup",
                    "credit_pack_id": "credits-500",
                },
            }
        },
    }


def test_verified_credit_receipt_requires_existing_local_purchase(monkeypatch, tmp_path):
    db = tmp_path / "stripe-receipts.sqlite3"
    accounts = AccountStore(db)
    user = _active_user(accounts)
    commerce_overlay.accounts.db_path = db
    monkeypatch.setenv(
        "LSS_STRIPE_CREDIT_PACKS_JSON",
        '[{"id":"credits-500","label":"500 credits","stripe_price_id":"price_test_credits","credits":500,"amount_minor":299,"currency":"GBP"}]',
    )

    with pytest.raises(ValueError, match="no matching verified local credit purchase"):
        commerce_overlay._record_verified_credit_receipt(_event(user["id"]))


def test_verified_credit_receipt_is_amount_bearing_and_idempotent(monkeypatch, tmp_path):
    db = tmp_path / "stripe-receipts.sqlite3"
    accounts = AccountStore(db)
    user = _active_user(accounts)
    commerce_overlay.accounts.db_path = db
    monkeypatch.setenv(
        "LSS_STRIPE_CREDIT_PACKS_JSON",
        '[{"id":"credits-500","label":"500 credits","stripe_price_id":"price_test_credits","credits":500,"amount_minor":299,"currency":"GBP"}]',
    )
    wallet = CreditWalletStore(db)
    wallet.adjust(
        user["id"],
        500,
        kind="purchase",
        reason="Stripe credit top-up — 500 credits",
        actor="stripe_webhook",
        reference="stripe:checkout:cs_credit_paid_1",
    )

    first = commerce_overlay._record_verified_credit_receipt(_event(user["id"]))
    second = commerce_overlay._record_verified_credit_receipt(_event(user["id"]))
    assert first is not None and second is not None
    assert first["id"] == second["id"]
    assert first["amount_minor"] == 299
    assert first["currency"] == "GBP"
    assert first["units"] == 500

    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM credit_transactions").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM commerce_receipts").fetchone()[0] == 1


def test_verified_credit_receipt_binds_payment_intent_for_refund_reconciliation(monkeypatch, tmp_path):
    db = tmp_path / "stripe-receipts.sqlite3"
    accounts = AccountStore(db)
    user = _active_user(accounts)
    commerce_overlay.accounts.db_path = db
    monkeypatch.setattr(commerce_overlay, "reversals", PaymentReversalStore(db))
    monkeypatch.setenv(
        "LSS_STRIPE_CREDIT_PACKS_JSON",
        '[{"id":"credits-500","label":"500 credits","stripe_price_id":"price_test_credits","credits":500,"amount_minor":299,"currency":"GBP"}]',
    )
    CreditWalletStore(db).adjust(
        user["id"],
        500,
        kind="purchase",
        reason="Stripe credit top-up — 500 credits",
        actor="stripe_webhook",
        reference="stripe:checkout:cs_credit_paid_1",
    )

    event = _event(user["id"])
    receipt = commerce_overlay._record_verified_credit_receipt(event)
    assert receipt is not None
    correlation = commerce_overlay._bind_credit_refund_correlation(event, receipt)
    assert correlation is not None
    assert correlation["payment_intent_id"] == "pi_credit_paid_1"
    assert correlation["receipt_reference"] == receipt["reference"]
    assert correlation["amount_minor"] == 299


def test_receipt_overlay_records_refund_evidence_after_hardened_delegate(monkeypatch):
    calls: list[str] = []

    async def fake_hardened(request):
        calls.append("hardened_delegate")
        return {"source": "verified_stripe"}

    class FakeReversals:
        def record_stripe_refund(self, event):
            assert event["type"] == "refund.updated"
            calls.append("refund_evidence")
            return {"linked": False}

    monkeypatch.setattr(commerce_overlay, "hardened_stripe_webhook", fake_hardened)
    monkeypatch.setattr(commerce_overlay, "reversals", FakeReversals())

    app = FastAPI()
    app.include_router(commerce_overlay.router)
    client = TestClient(app)
    response = client.post(
        "/billing/stripe/webhook",
        json={
            "id": "evt_refund_probe",
            "type": "refund.updated",
            "data": {
                "object": {
                    "id": "re_probe",
                    "payment_intent": "pi_old",
                    "amount": 100,
                    "currency": "gbp",
                    "status": "succeeded",
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["finance_refund_recorded"] is True
    assert response.json()["finance_refund_linked"] is False
    assert response.json()["finance_reconciliation_required"] is True
    assert response.json()["wallet_effect"] == "none"
    assert calls == ["hardened_delegate", "refund_evidence"]


def test_receipt_overlay_owns_effective_webhook_route_before_base_handlers(monkeypatch):
    # Prove actual ASGI dispatch rather than relying on OpenAPI or internal route flattening.
    # The commerce overlay must receive the request first and then delegate to the hardened Stripe
    # processor. A non-credit event avoids finance mutation while still exercising that routing.
    from aura_music_studio.creative_version_autopromotion import router as overlay_router

    calls: list[str] = []

    async def fake_hardened(request):
        calls.append("hardened_delegate")
        return {"source": "commerce_overlay"}

    monkeypatch.setattr(commerce_overlay, "hardened_stripe_webhook", fake_hardened)

    effective_app = FastAPI()
    effective_app.include_router(overlay_router)
    client = TestClient(effective_app)
    response = client.post(
        "/billing/stripe/webhook",
        json={"id": "evt_route_probe", "type": "customer.updated", "data": {"object": {}}},
    )

    assert response.status_code == 200
    assert response.json() == {"source": "commerce_overlay"}
    assert calls == ["hardened_delegate"]
