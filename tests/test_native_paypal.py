from __future__ import annotations

import json
from datetime import timezone

import pytest

from aura_music_studio.native_paypal import (
    NativeCheckoutMetadataSigner,
    NativePayPalError,
    NativePayPalInvoiceCreator,
    NativePayPalPaymentVerifier,
)
from aura_music_studio.native_products import BillingPeriod


SECRET = b"native-paypal-test-secret-material-32-bytes-minimum"


class FakePayPalWebhookVerifier:
    def __init__(self, result: bool = True):
        self.result = result
        self.calls = []
        self.base_url = "https://api-m.sandbox.paypal.com"

    def verify(self, headers: dict[str, str], event: dict) -> bool:
        self.calls.append((headers, event))
        return self.result

    def _access_token(self) -> str:
        return "access-token"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _event(*, amount: str = "4.99", currency: str = "GBP", status: str = "PAID") -> bytes:
    return json.dumps(
        {
            "id": "WH-evt-1",
            "event_type": "INVOICING.INVOICE.PAID",
            "create_time": "2026-09-03T06:30:00Z",
            "resource": {
                "id": "INV2-native-1",
                "status": status,
                "amount": {"currency_code": currency, "value": amount},
            },
        }
    ).encode("utf-8")


def _invoice(*, token: str, amount: str = "4.99", currency: str = "GBP", status: str = "PAID") -> dict:
    return {
        "id": "INV2-native-1",
        "status": status,
        "detail": {"reference": token},
        "amount": {"currency_code": currency, "value": amount},
    }


def _headers() -> dict[str, str]:
    return {"PayPal-Transmission-Id": "tx-verified-1"}


def _verifier(*, signer, invoice: dict, webhook_result: bool = True):
    return NativePayPalPaymentVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(webhook_result),
        metadata_signer=signer,
        invoice_loader=lambda invoice_id: invoice if invoice_id == "INV2-native-1" else {},
    )


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
    assert len(token) <= 120


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


def test_metadata_signer_fails_closed_when_reference_would_exceed_paypal_limit():
    signer = NativeCheckoutMetadataSigner(SECRET)
    with pytest.raises(NativePayPalError, match="too long for the PayPal invoice reference"):
        signer.sign(user_id="u" * 100, product_id="aura_os", billing_period="monthly")


def test_founder_offer_cannot_be_signed_for_wrong_product_or_period():
    signer = NativeCheckoutMetadataSigner(SECRET)
    with pytest.raises(ValueError):
        signer.sign(user_id="user-123", product_id="aura_os", billing_period="annual", founding_offer=True)
    with pytest.raises(ValueError):
        signer.sign(user_id="user-123", product_id="aura_sec", billing_period="monthly", founding_offer=True)


def test_invoice_creator_uses_canonical_catalogue_and_signed_reference():
    signer = NativeCheckoutMetadataSigner(SECRET)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/v2/invoicing/invoices"):
            return FakeResponse(201, {"id": "INV2-native-1"})
        if url.endswith("/INV2-native-1/send"):
            return FakeResponse(202, {})
        raise AssertionError(url)

    creator = NativePayPalInvoiceCreator(
        paypal=FakePayPalWebhookVerifier(),
        metadata_signer=signer,
        post_request=post,
    )
    receipt = creator.create_and_send(
        user_id="user-123",
        billing_email="buyer@example.com",
        product_id="aura_sec",
        billing_period="annual",
        founding_offer=True,
    )

    create_body = calls[0][1]["json"]
    reference = create_body["detail"]["reference"]
    assert len(reference) <= 120
    assert signer.verify(reference) == {
        "user_id": "user-123",
        "product_id": "aura_sec",
        "billing_period": BillingPeriod.ANNUAL,
        "founding_offer": True,
    }
    assert create_body["detail"]["currency_code"] == "GBP"
    assert create_body["items"][0]["unit_amount"] == {"currency_code": "GBP", "value": "24.99"}
    assert create_body["primary_recipients"][0]["billing_info"]["email_address"] == "buyer@example.com"
    assert create_body["configuration"]["partial_payment"]["allow_partial_payment"] is False
    assert calls[1][1]["json"] == {"send_to_recipient": True, "send_to_invoicer": False}
    assert receipt == {
        "provider": "paypal",
        "invoice_id": "INV2-native-1",
        "product_id": "aura_sec",
        "billing_period": "annual",
        "founding_offer": True,
        "amount_minor": 2499,
        "currency": "GBP",
        "sent": True,
    }


def test_invoice_creator_rejects_invalid_email_and_never_calls_paypal():
    calls = []
    creator = NativePayPalInvoiceCreator(
        paypal=FakePayPalWebhookVerifier(),
        metadata_signer=NativeCheckoutMetadataSigner(SECRET),
        post_request=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(NativePayPalError, match="billing email"):
        creator.create_and_send(
            user_id="user-123",
            billing_email="not-an-email",
            product_id="aura_os",
            billing_period="monthly",
        )
    assert calls == []


def test_invoice_creator_does_not_send_when_draft_creation_fails():
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return FakeResponse(422, {})

    creator = NativePayPalInvoiceCreator(
        paypal=FakePayPalWebhookVerifier(),
        metadata_signer=NativeCheckoutMetadataSigner(SECRET),
        post_request=post,
    )
    with pytest.raises(NativePayPalError, match="creation failed"):
        creator.create_and_send(
            user_id="user-123",
            billing_email="buyer@example.com",
            product_id="aura_os_sec_bundle",
            billing_period="monthly",
        )
    assert len(calls) == 1


def test_verified_invoice_becomes_exact_native_payment_evidence_from_authoritative_reference():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    webhook = FakePayPalWebhookVerifier(True)
    verifier = NativePayPalPaymentVerifier(
        webhook_verifier=webhook,
        metadata_signer=signer,
        invoice_loader=lambda invoice_id: _invoice(token=token),
    )
    evidence = verifier.verify(_event(), _headers())
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


def test_webhook_payload_cannot_supply_or_override_native_identity():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    payload = json.loads(_event().decode("utf-8"))
    payload["resource"]["custom_id"] = signer.sign(
        user_id="attacker", product_id="aura_sec", billing_period="monthly"
    )
    verifier = _verifier(signer=signer, invoice=_invoice(token=token))
    evidence = verifier.verify(json.dumps(payload).encode("utf-8"), _headers())
    assert evidence.user_id == "user-123"
    assert evidence.product_id == "aura_os"


def test_paypal_signature_failure_is_fail_closed_before_invoice_trust():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = _verifier(signer=signer, invoice=_invoice(token=token), webhook_result=False)
    with pytest.raises(NativePayPalError, match="signature verification failed"):
        verifier.verify(_event(), _headers())


def test_authoritative_invoice_id_and_paid_state_must_match_webhook():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    wrong = _invoice(token=token)
    wrong["id"] = "INV2-other"
    verifier = _verifier(signer=signer, invoice=wrong)
    with pytest.raises(NativePayPalError, match="does not match"):
        verifier.verify(_event(), _headers())

    unpaid = _invoice(token=token, status="UNPAID")
    verifier = _verifier(signer=signer, invoice=unpaid)
    with pytest.raises(NativePayPalError, match="not fully paid"):
        verifier.verify(_event(), _headers())


def test_verified_paypal_event_cannot_override_authoritative_product_or_price():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = _verifier(signer=signer, invoice=_invoice(token=token, amount="7.99"))
    with pytest.raises(NativePayPalError, match="amount does not match"):
        verifier.verify(_event(amount="7.99"), _headers())


def test_webhook_and_authoritative_invoice_amounts_must_agree():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = _verifier(signer=signer, invoice=_invoice(token=token))
    with pytest.raises(NativePayPalError, match="webhook and authoritative invoice amounts"):
        verifier.verify(_event(amount="7.99"), _headers())


def test_verified_paypal_event_rejects_wrong_currency_or_unpaid_webhook_state():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = _verifier(signer=signer, invoice=_invoice(token=token, currency="USD"))
    with pytest.raises(NativePayPalError, match="currency"):
        verifier.verify(_event(currency="USD"), _headers())

    verifier = _verifier(signer=signer, invoice=_invoice(token=token))
    with pytest.raises(NativePayPalError, match="not fully paid"):
        verifier.verify(_event(status="PARTIALLY_PAID"), _headers())


def test_verified_paypal_event_requires_authoritative_signed_reference_and_transmission_identity():
    signer = NativeCheckoutMetadataSigner(SECRET)
    invoice = _invoice(token="")
    verifier = _verifier(signer=signer, invoice=invoice)
    with pytest.raises(NativePayPalError, match="metadata token"):
        verifier.verify(_event(), _headers())

    token = signer.sign(user_id="user-123", product_id="aura_os", billing_period="monthly")
    verifier = _verifier(signer=signer, invoice=_invoice(token=token))
    with pytest.raises(NativePayPalError, match="transmission ID"):
        verifier.verify(_event(), {})


def test_founding_aura_sec_invoice_uses_exact_2499_contract():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(
        user_id="user-123",
        product_id="aura_sec",
        billing_period="annual",
        founding_offer=True,
    )
    verifier = _verifier(signer=signer, invoice=_invoice(token=token, amount="24.99"))
    evidence = verifier.verify(_event(amount="24.99"), _headers())
    assert evidence.product_id == "aura_sec"
    assert evidence.billing_period is BillingPeriod.ANNUAL
    assert evidence.founding_offer is True
    assert evidence.amount_minor == 2499
