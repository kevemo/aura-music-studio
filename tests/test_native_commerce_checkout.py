from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.native_commerce_api as commerce
from aura_music_studio.native_billing import NativeEntitlementLedger, VerifiedNativePayment
from aura_music_studio.native_paypal import NativePayPalError
from aura_music_studio.native_products import BillingPeriod


class _Store:
    def __init__(self, user: dict | None):
        self.user = user
        self.tokens: list[str | None] = []

    def resolve_session(self, token):
        self.tokens.append(token)
        return self.user


class _Ledger:
    def __init__(self, *, prior_purchase: bool = False):
        self.prior_purchase = prior_purchase
        self.history_calls: list[tuple[str, str]] = []

    def has_product_purchase(self, user_id: str, product_id: str) -> bool:
        self.history_calls.append((user_id, product_id))
        return self.prior_purchase


class _Creator:
    def __init__(self):
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def create_and_send(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {
            "provider": "paypal",
            "invoice_id": "INV2-123",
            "product_id": kwargs["product_id"],
            "billing_period": BillingPeriod(kwargs["billing_period"]).value,
            "founding_offer": bool(kwargs["founding_offer"]),
            "amount_minor": 2499,
            "currency": "GBP",
            "sent": True,
        }


def _client(monkeypatch, *, user: dict | None = None, prior_purchase: bool = False):
    user = user if user is not None else {
        "id": "user-1",
        "email": "member@example.com",
        "status": "active",
    }
    store = _Store(user)
    ledger = _Ledger(prior_purchase=prior_purchase)
    creator = _Creator()
    monkeypatch.setattr(commerce, "_store", store)
    monkeypatch.setattr(commerce, "native_entitlements", ledger)
    monkeypatch.setattr(commerce, "_checkout_creator", lambda: creator)
    app = FastAPI()
    app.include_router(commerce.router)
    return TestClient(app), store, ledger, creator


def test_checkout_uses_authenticated_account_identity_and_server_owned_catalogue(monkeypatch):
    client, store, ledger, creator = _client(monkeypatch)

    response = client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "session-token"},
        json={
            "product_id": "aura_sec",
            "billing_period": "annual",
            "founding_offer": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "created": True,
        "payment_state": "awaiting_provider_payment",
        "provider": "paypal",
        "invoice_id": "INV2-123",
        "product_id": "aura_sec",
        "billing_period": "annual",
        "founding_offer": True,
        "amount_minor": 2499,
        "currency": "GBP",
        "sent": True,
    }
    assert store.tokens == ["session-token"]
    assert ledger.history_calls == [("user-1", "aura_sec")]
    assert creator.calls == [
        {
            "user_id": "user-1",
            "billing_email": "member@example.com",
            "product_id": "aura_sec",
            "billing_period": BillingPeriod.ANNUAL,
            "founding_offer": True,
        }
    ]


def test_checkout_rejects_client_owned_money_identity_and_email_fields(monkeypatch):
    client, _, _, creator = _client(monkeypatch)
    response = client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "session-token"},
        json={
            "product_id": "aura_os",
            "billing_period": "monthly",
            "founding_offer": False,
            "amount_minor": 1,
            "currency": "USD",
            "user_id": "attacker-selected-user",
            "billing_email": "attacker@example.com",
        },
    )
    assert response.status_code == 422
    assert creator.calls == []


def test_checkout_requires_authenticated_active_account(monkeypatch):
    client, _, _, creator = _client(monkeypatch, user=None)
    # Explicitly replace the helper's default user with no authenticated member.
    monkeypatch.setattr(commerce, "_store", _Store(None))
    assert client.post(
        "/billing/native/paypal/checkout",
        json={"product_id": "aura_os", "billing_period": "monthly"},
    ).status_code == 401
    assert creator.calls == []

    pending_client, _, _, pending_creator = _client(
        monkeypatch,
        user={"id": "user-2", "email": "pending@example.com", "status": "pending_approval"},
    )
    assert pending_client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "pending-token"},
        json={"product_id": "aura_os", "billing_period": "monthly"},
    ).status_code == 403
    assert pending_creator.calls == []


def test_checkout_rejects_reused_or_invalid_founding_offer_before_provider_creation(monkeypatch):
    client, _, ledger, creator = _client(monkeypatch, prior_purchase=True)
    response = client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "session-token"},
        json={"product_id": "aura_sec", "billing_period": "annual", "founding_offer": True},
    )
    assert response.status_code == 409
    assert ledger.history_calls == [("user-1", "aura_sec")]
    assert creator.calls == []

    fresh_client, _, fresh_ledger, fresh_creator = _client(monkeypatch)
    invalid = fresh_client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "session-token"},
        json={"product_id": "aura_os", "billing_period": "annual", "founding_offer": True},
    )
    assert invalid.status_code == 400
    assert fresh_ledger.history_calls == []
    assert fresh_creator.calls == []


def test_checkout_configuration_and_provider_failures_fail_closed(monkeypatch):
    client, _, _, _ = _client(monkeypatch)
    monkeypatch.setattr(
        commerce,
        "_checkout_creator",
        lambda: (_ for _ in ()).throw(NativePayPalError("metadata secret missing")),
    )
    response = client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "session-token"},
        json={"product_id": "aura_os", "billing_period": "monthly"},
    )
    assert response.status_code == 503

    provider_client, _, _, creator = _client(monkeypatch)
    creator.error = NativePayPalError("PayPal native invoice creation failed")
    provider_response = provider_client.post(
        "/billing/native/paypal/checkout",
        cookies={"lss_session": "session-token"},
        json={"product_id": "aura_os", "billing_period": "monthly"},
    )
    assert provider_response.status_code == 502


def test_verified_purchase_history_remains_true_after_first_native_term(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native-history.sqlite3")

    class _Verifier:
        def verify(self, raw_event, headers):
            return VerifiedNativePayment(
                provider="paypal",
                event_id="event-1",
                payment_id="invoice-1",
                verification_id="transmission-1",
                user_id="user-1",
                product_id="aura_sec",
                billing_period=BillingPeriod.ANNUAL,
                amount_minor=2499,
                currency="GBP",
                occurred_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
                founding_offer=True,
            )

    assert ledger.has_product_purchase("user-1", "aura_sec") is False
    ledger.process_verified_event(raw_event=b"{}", headers={}, verifier=_Verifier())
    assert ledger.has_product_purchase("user-1", "aura_sec") is True
