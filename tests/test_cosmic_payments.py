from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_music_studio.cosmic_economy import EconomyError, VerifiedPaymentEvent
from aura_music_studio.cosmic_payments import CoinCheckout, CoinPaymentProviderRegistry


class FakeSignedProvider:
    name = "fake-signed"

    def __init__(self):
        self.checkout_calls: list[dict] = []

    def create_checkout(
        self,
        *,
        purchase_id,
        user_id,
        fiat_amount_minor,
        fiat_currency,
        coin_quantity,
        idempotency_key,
    ):
        assert isinstance(fiat_amount_minor, int)
        assert isinstance(coin_quantity, int)
        assert idempotency_key == purchase_id
        self.checkout_calls.append(
            {
                "purchase_id": purchase_id,
                "user_id": user_id,
                "fiat_amount_minor": fiat_amount_minor,
                "fiat_currency": fiat_currency,
                "coin_quantity": coin_quantity,
                "idempotency_key": idempotency_key,
            }
        )
        return CoinCheckout(
            self.name,
            f"pay-{purchase_id}",
            f"https://example.invalid/checkout/{purchase_id}",
        )

    def verify_webhook(self, *, headers, body):
        if headers.get("x-test-signature") != "valid":
            raise EconomyError("INVALID_PAYMENT_WEBHOOK", "Invalid test signature", status_code=401)
        return VerifiedPaymentEvent(
            provider=self.name,
            provider_event_id="evt-1",
            provider_payment_id="pay-1",
            purchase_id="purchase-1",
            event_type="confirmed",
            verified=True,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )


def test_registry_is_fail_closed_when_provider_not_configured():
    registry = CoinPaymentProviderRegistry()
    with pytest.raises(EconomyError) as exc:
        registry.get("missing")
    assert exc.value.code == "PAYMENT_PROVIDER_UNAVAILABLE"


def test_fake_provider_checkout_uses_server_minor_units_and_purchase_idempotency():
    registry = CoinPaymentProviderRegistry()
    provider = FakeSignedProvider()
    registry.register(provider)
    checkout = registry.get("fake-signed").create_checkout(
        purchase_id="purchase-1",
        user_id="viewer-1",
        fiat_amount_minor=500,
        fiat_currency="GBP",
        coin_quantity=1000,
        idempotency_key="purchase-1",
    )
    assert checkout.provider_payment_id == "pay-purchase-1"
    assert checkout.status == "pending"
    assert provider.checkout_calls == [
        {
            "purchase_id": "purchase-1",
            "user_id": "viewer-1",
            "fiat_amount_minor": 500,
            "fiat_currency": "GBP",
            "coin_quantity": 1000,
            "idempotency_key": "purchase-1",
        }
    ]


def test_fake_provider_rejects_forged_webhook_and_accepts_verified_one():
    provider = FakeSignedProvider()
    with pytest.raises(EconomyError) as exc:
        provider.verify_webhook(headers={"x-test-signature": "forged"}, body=b"{}")
    assert exc.value.code == "INVALID_PAYMENT_WEBHOOK"
    event = provider.verify_webhook(headers={"x-test-signature": "valid"}, body=b"{}")
    assert event.verified is True
    assert event.event_type == "confirmed"
