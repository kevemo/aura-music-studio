from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aura_music_studio import stripe_creation_coins as coins


def _pack():
    return SimpleNamespace(
        id="starter",
        label="Starter Pack",
        stripe_price_id="price_server_only",
        credits=250,
        amount_minor=199,
        currency="GBP",
    )


def test_catalog_allows_active_free_member_and_hides_stripe_price_id(monkeypatch):
    monkeypatch.setattr(coins, "_session_user", lambda request: {"id": "u-free", "status": "active", "plan_id": "free"})
    monkeypatch.setattr(coins, "credit_packs", lambda: {"starter": _pack()})
    monkeypatch.setattr(coins._wallet, "balance", lambda user_id: 75)
    request = SimpleNamespace()

    payload = coins.creation_coin_catalog_payload(request)

    assert payload["balance"] == 75
    assert payload["eligible_membership_ids_for_coin_purchase"] == ["free", "base", "pro"]
    assert payload["client_can_set_amount_or_coin_quantity"] is False
    assert payload["packs"] == [{
        "id": "starter",
        "label": "Starter Pack",
        "creation_coins": 250,
        "amount_minor": 199,
        "currency": "GBP",
    }]
    assert "stripe_price_id" not in payload["packs"][0]
    assert payload["subscription_effect"] == "none"
    assert payload["esp_role_effect"] == "none"


def test_catalog_rejects_non_active_account_before_wallet_lookup(monkeypatch):
    monkeypatch.setattr(coins, "_session_user", lambda request: {"id": "u-pending", "status": "approved_pending_payment"})
    monkeypatch.setattr(coins._wallet, "balance", lambda user_id: (_ for _ in ()).throw(AssertionError("wallet lookup must not occur")))

    with pytest.raises(HTTPException) as exc:
        coins.creation_coin_catalog_payload(SimpleNamespace())

    assert exc.value.status_code == 403


def test_storefront_uses_pack_id_checkout_and_does_not_embed_server_price_id(monkeypatch):
    monkeypatch.setattr(coins, "_session_user", lambda request: {"id": "u-free", "status": "active", "plan_id": "free"})
    monkeypatch.setattr(coins, "credit_packs", lambda: {"starter": _pack()})
    monkeypatch.setattr(coins._wallet, "balance", lambda user_id: 12)

    app = FastAPI()
    app.include_router(coins.router)
    response = TestClient(app).get("/billing/creation-coins")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "Creation Coins" in response.text
    assert "Free, Basic and Pro members" in response.text
    assert "price_server_only" not in response.text
    assert "pack_id:p.id" in response.text
    assert "https://checkout.stripe.com/" in response.text


def test_hardened_production_router_mounts_creation_coin_catalog(monkeypatch):
    monkeypatch.setattr(coins, "_session_user", lambda request: (_ for _ in ()).throw(HTTPException(401, "Sign in required")))
    from aura_music_studio.stripe_billing_hardening import router as hardened_router

    app = FastAPI()
    app.include_router(hardened_router)
    response = TestClient(app).get("/billing/creation-coins/catalog")

    assert response.status_code == 401
    assert response.json()["detail"] == "Sign in required"
