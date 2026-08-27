from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI

from aura_music_studio.accounts import AccountStore
from aura_music_studio.credit_wallet import CreditWalletStore
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


def test_receipt_overlay_owns_effective_webhook_route_before_base_handlers():
    # FastAPI 0.137+ keeps nested router composition in a live route tree. Mirror the existing
    # Stripe routability regression by including the overlay into an effective app before
    # checking endpoint precedence.
    from aura_music_studio.creative_version_autopromotion import router as overlay_router

    effective_app = FastAPI()
    effective_app.include_router(overlay_router)
    matching = [
        route
        for route in effective_app.routes
        if getattr(route, "path", None) == "/billing/stripe/webhook"
        and "POST" in (getattr(route, "methods", None) or set())
    ]
    assert matching
    assert matching[0].endpoint.__name__ == "stripe_webhook_with_commerce_receipt"
