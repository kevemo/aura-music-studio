from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aura_music_studio.creation_coin_catalog import (
    CANONICAL_CREATION_COIN_PACKS,
    validate_creation_coin_packs,
)
from aura_music_studio import stripe_billing_hardening as hardening


def _pack(pack_id: str, credits: int, amount_minor: int, *, currency: str = "GBP"):
    return SimpleNamespace(
        id=pack_id,
        label=pack_id,
        stripe_price_id=f"price_{pack_id}",
        credits=credits,
        amount_minor=amount_minor,
        currency=currency,
    )


def _canonical_packs():
    return {
        "small": _pack("small", 1_000, 500),
        "medium": _pack("medium", 2_500, 1_000),
        "large": _pack("large", 6_000, 2_000),
    }


def test_master_creation_coin_catalog_is_exact_and_immutable():
    assert CANONICAL_CREATION_COIN_PACKS == {
        (1_000, 500, "GBP"),
        (2_500, 1_000, "GBP"),
        (6_000, 2_000, "GBP"),
    }
    assert validate_creation_coin_packs(_canonical_packs())


@pytest.mark.parametrize(
    "packs",
    [
        {"small": _pack("small", 999, 500), "medium": _pack("medium", 2_500, 1_000), "large": _pack("large", 6_000, 2_000)},
        {"small": _pack("small", 1_000, 499), "medium": _pack("medium", 2_500, 1_000), "large": _pack("large", 6_000, 2_000)},
        {"small": _pack("small", 1_000, 500, currency="USD"), "medium": _pack("medium", 2_500, 1_000), "large": _pack("large", 6_000, 2_000)},
        {"small": _pack("small", 1_000, 500), "medium": _pack("medium", 2_500, 1_000)},
        {"a": _pack("a", 1_000, 500), "b": _pack("b", 1_000, 500), "large": _pack("large", 6_000, 2_000)},
    ],
)
def test_catalog_rejects_value_drift_missing_and_duplicate_packs(packs):
    with pytest.raises(ValueError):
        validate_creation_coin_packs(packs)


def test_hardened_public_catalog_fails_closed_before_legacy_handler(monkeypatch):
    monkeypatch.setattr(hardening, "credit_packs", lambda: {"bad": _pack("bad", 9_999, 1)})
    monkeypatch.setattr(
        hardening,
        "base_creation_coin_catalog",
        lambda request: (_ for _ in ()).throw(AssertionError("legacy catalog must not run")),
    )

    with pytest.raises(HTTPException) as exc:
        hardening.hardened_creation_coin_catalog(SimpleNamespace())

    assert exc.value.status_code == 503


def test_hardened_credit_checkout_fails_before_stripe_submission(monkeypatch):
    monkeypatch.setattr(hardening, "credit_packs", lambda: {"bad": _pack("bad", 1_000, 1)})
    monkeypatch.setattr(
        hardening,
        "base_create_credit_checkout",
        lambda body, request: (_ for _ in ()).throw(AssertionError("Stripe checkout must not run")),
    )

    body = hardening.CreditCheckoutRequest(pack_id="bad")
    with pytest.raises(HTTPException) as exc:
        hardening.hardened_credit_checkout(body, SimpleNamespace())

    assert exc.value.status_code == 503


def test_hardened_routes_precede_legacy_creation_coin_routes():
    app = FastAPI()
    app.include_router(hardening.router)

    expected = {
        ("/billing/creation-coins/catalog", "GET"): "hardened_creation_coin_catalog",
        ("/billing/creation-coins", "GET"): "hardened_creation_coin_storefront",
        ("/billing/stripe/checkout/credits", "POST"): "hardened_credit_checkout",
    }
    first_matches = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()):
            first_matches.setdefault((path, method), getattr(getattr(route, "endpoint", None), "__name__", ""))

    for key, endpoint_name in expected.items():
        assert first_matches[key] == endpoint_name


def test_credit_topup_webhook_rejects_noncanonical_catalog_before_wallet_processor(monkeypatch):
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: SimpleNamespace(webhook_configured=True, webhook_secret="whsec_test")))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda raw, header, secret: None)
    monkeypatch.setattr(hardening, "credit_packs", lambda: {"bad": _pack("bad", 1_000, 1)})

    async def _must_not_run(request):
        raise AssertionError("base Stripe webhook must not mutate wallet state")

    monkeypatch.setattr(hardening, "base_stripe_webhook", _must_not_run)
    app = FastAPI()
    app.include_router(hardening.router)
    event = {
        "id": "evt_coin_bad_catalog",
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"purchase_kind": "credit_topup"}}},
    }

    response = TestClient(app).post("/billing/stripe/webhook", json=event, headers={"stripe-signature": "test"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Creation Coin catalogue is not safely configured"


def test_non_coin_webhook_does_not_require_creation_coin_catalog(monkeypatch):
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: SimpleNamespace(webhook_configured=True, webhook_secret="whsec_test")))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda raw, header, secret: None)
    monkeypatch.setattr(hardening, "credit_packs", lambda: (_ for _ in ()).throw(AssertionError("coin catalog must not be loaded")))

    async def _delegate(request):
        return {"received": True, "processed": False}

    monkeypatch.setattr(hardening, "base_stripe_webhook", _delegate)
    app = FastAPI()
    app.include_router(hardening.router)
    event = {"id": "evt_unrelated", "type": "customer.subscription.deleted", "data": {"object": {}}}

    response = TestClient(app).post("/billing/stripe/webhook", json=event, headers={"stripe-signature": "test"})

    assert response.status_code == 200
    assert response.json()["received"] is True
