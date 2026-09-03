from __future__ import annotations

import json
from datetime import timezone

import pytest

from aura_music_studio.native_paypal import (
    NativeCheckoutMetadataSigner,
    NativePayPalError,
    NativePayPalPaymentVerifier,
)
from aura_music_studio.native_products import BillingPeriod


SECRET = b"native-paypal-test-secret-material-32-bytes-minimum"


class FakePayPalWebhookVerifier:
    def __init__(self, result: bool = True):
        self.result = result
        self.calls = []

    def verify(self, headers: dict[str, str], event: dict) -> bool:
        self.calls.append((headers, event))
        return self.result


def _event(*, token: str, amount: str = "4.99", currency: str = "GBP", status: str = "PAID") -> bytes:
    return json.dumps(
        {
            "id": "WH-evt-1",
            "event_type": "INVOICING.INVOICE.PAID",
            "create_time": "2026-09-03T06:30:00Z",
            "resource": {
                "id": "INV2-native-1",
                "status": status,
                "custom_id": token,
                "amount": {"currency_code": currency, "value": amount},
            },
        }
    ).encode("utf-8")


def _headers() -> dict[str, str]:
    return {"PayPal-Transmission-Id": "tx-verified-1"}


def test_metadata_signer_round_trip_binds_native_identity():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(
        user_id="user-123",
        product_id="aura_sec",
        billing_period=BillingPeriod.ANNUAL,
        founding_offer=True,
    )

    payload = signer.verify(token)

    assert payload["user_id"] == "user-123"
    assert payload["product_id"] == "aura_sec"
    assert payload["billing_period"] is BillingPeriod.ANNUAL
    assert payload["founding_offer"] is True


def test_metadata_signer_rejects_tampering():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    body, signature = token.split(".", 1)
    tampered = ("A" if body[0] != "A" else "B") + body[1:] + "." + signature

    with pytest.raises(NativePayPalError, match="signature|payload|encoding"):
        signer.verify(tampered)


def test_metadata_signer_has_no_weak_default(monkeypatch):
    monkeypatch.delenv("LSS_NATIVE_PAYPAL_METADATA_SECRET", raising=False)
    with pytest.raises(NativePayPalError, match="at least 32 bytes"):
        NativeCheckoutMetadataSigner()


def test_founder_offer_cannot_be_signed_for_wrong_product_or_period():
    signer = NativeCheckoutMetadataSigner(SECRET)
    with pytest.raises(ValueError):
        signer.sign(
            user_id="user-123",
            product_id="aura_os",
            billing_period="annual",
            founding_offer=True,
        )
    with pytest.raises(ValueError):
        signer.sign(
            user_id="user-123",
            product_id="aura_sec",
            billing_period="monthly",
            founding_offer=True,
        )


def test_verified_invoice_becomes_exact_native_payment_evidence():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    webhook = FakePayPalWebhookVerifier(True)
    verifier = NativePayPalPaymentVerifier(webhook_verifier=webhook, metadata_signer=signer)

    evidence = verifier.verify(_event(token=token), _headers())

    assert evidence.provider == "paypal"
    assert evidence.event_id == "WH-evt-1"
    assert evidence.payment_id == "INV2-native-1"
    assert evidence.verification_id == "tx-verified-1"
    assert evidence.user_id == "user-123"
    assert evidence.product_id == "aura_os"
    assert evidence.billing_period is BillingPeriod.MONTHLY
    assert evidence.amount_minor == 499
    assert evidence.currency == "GBP"
    assert evidence.occurred_at.tzinfo == timezone.utc
    assert webhook.calls


def test_paypal_signature_failure_is_fail_closed():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = NativePayPalPaymentVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(False),
        metadata_signer=signer,
    )

    with pytest.raises(NativePayPalError, match="signature verification failed"):
        verifier.verify(_event(token=token), _headers())


def test_verified_paypal_event_cannot_override_signed_product_or_price():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = NativePayPalPaymentVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(True),
        metadata_signer=signer,
    )

    with pytest.raises(NativePayPalError, match="amount does not match"):
        verifier.verify(_event(token=token, amount="7.99"), _headers())


def test_verified_paypal_event_rejects_wrong_currency_or_unpaid_state():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = NativePayPalPaymentVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(True),
        metadata_signer=signer,
    )

    with pytest.raises(NativePayPalError, match="currency"):
        verifier.verify(_event(token=token, currency="USD"), _headers())
    with pytest.raises(NativePayPalError, match="not fully paid"):
        verifier.verify(_event(token=token, status="PARTIALLY_PAID"), _headers())


def test_verified_paypal_event_requires_signed_metadata_and_transmission_identity():
    signer = NativeCheckoutMetadataSigner(SECRET)
    verifier = NativePayPalPaymentVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(True),
        metadata_signer=signer,
    )
    missing_metadata = json.loads(_event(token="").decode("utf-8"))
    missing_metadata["resource"].pop("custom_id")

    with pytest.raises(NativePayPalError, match="metadata token"):
        verifier.verify(json.dumps(missing_metadata).encode("utf-8"), _headers())

    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    with pytest.raises(NativePayPalError, match="transmission ID"):
        verifier.verify(_event(token=token), {})


def test_founding_aura_sec_invoice_uses_exact_2499_contract():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(
        user_id="user-123",
        product_id="aura_sec",
        billing_period="annual",
        founding_offer=True,
    )
    verifier = NativePayPalPaymentVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(True),
        metadata_signer=signer,
    )

    evidence = verifier.verify(_event(token=token, amount="24.99"), _headers())

    assert evidence.product_id == "aura_sec"
    assert evidence.billing_period is BillingPeriod.ANNUAL
    assert evidence.founding_offer is True
    assert evidence.amount_minor == 2499
