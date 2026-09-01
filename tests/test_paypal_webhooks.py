from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.paypal_webhooks import (
    PayPalWebhookError,
    PayPalWebhookEvidenceStore,
    PayPalWebhookVerifier,
    validate_invoice_paid_event,
)


def paid_event(**resource_overrides):
    resource = {
        "id": "INV2-TEST-001",
        "status": "PAID",
        "amount": {"currency_code": "GBP", "value": "4.99"},
        "payer_email": "member@example.com",
    }
    resource.update(resource_overrides)
    return {
        "id": "WH-TEST-001",
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": resource,
    }


def test_exact_fully_paid_invoice_can_become_payment_evidence():
    reference = validate_invoice_paid_event(
        paid_event(),
        expected_amount_minor=499,
        expected_currency="GBP",
        expected_email="Member@Example.com",
    )
    assert reference == "paypal:WH-TEST-001"


@pytest.mark.parametrize(
    "event, message",
    [
        (paid_event(status="PARTIALLY_PAID"), "not fully paid"),
        (paid_event(amount={"currency_code": "GBP", "value": "4.98"}), "amount does not match"),
        (paid_event(amount={"currency_code": "USD", "value": "4.99"}), "currency does not match"),
        (paid_event(payer_email="other@example.com"), "payer identity does not match"),
    ],
)
def test_invoice_activation_gate_rejects_mismatched_evidence(event, message):
    with pytest.raises(PayPalWebhookError, match=message):
        validate_invoice_paid_event(
            event,
            expected_amount_minor=499,
            expected_currency="GBP",
            expected_email="member@example.com",
        )


def test_verified_event_ledger_is_idempotent(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    evidence = PayPalWebhookEvidenceStore(store)
    event = paid_event()

    first = evidence.record(event, "transmission-1")
    second = evidence.record(event, "transmission-1")

    assert first["verified"] is True
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    stored = evidence.get(event["id"])
    assert stored is not None
    assert stored["payload"] == event
    assert evidence.recent(1)[0]["event_id"] == event["id"]


@pytest.mark.parametrize(
    "changed_event, transmission_id",
    [
        (paid_event(status="REFUNDED"), "transmission-1"),
        (paid_event(), "transmission-2"),
    ],
)
def test_verified_event_ledger_rejects_conflicting_duplicate_evidence(
    tmp_path, changed_event, transmission_id
):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    evidence = PayPalWebhookEvidenceStore(store)
    evidence.record(paid_event(), "transmission-1")

    with pytest.raises(PayPalWebhookError, match="conflicts"):
        evidence.record(changed_event, transmission_id)

    stored = evidence.get("WH-TEST-001")
    assert stored is not None
    assert stored["transmission_id"] == "transmission-1"
    assert stored["payload"]["resource"]["status"] == "PAID"


def test_verifier_rejects_non_paypal_certificate_host(monkeypatch):
    monkeypatch.setenv("LSS_PAYPAL_CLIENT_ID", "client")
    monkeypatch.setenv("LSS_PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LSS_PAYPAL_WEBHOOK_ID", "webhook")
    verifier = PayPalWebhookVerifier()
    headers = {
        "paypal-transmission-id": "t1",
        "paypal-transmission-time": "2026-08-27T00:00:00Z",
        "paypal-cert-url": "https://evilpaypal.com/cert",
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-sig": "sig",
    }

    with pytest.raises(PayPalWebhookError, match="Untrusted"):
        verifier.verify(headers, paid_event())


def test_verifier_uses_paypal_postback_and_requires_success(monkeypatch):
    monkeypatch.setenv("LSS_PAYPAL_CLIENT_ID", "client")
    monkeypatch.setenv("LSS_PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LSS_PAYPAL_WEBHOOK_ID", "webhook")
    monkeypatch.setenv("LSS_PAYPAL_ENVIRONMENT", "sandbox")
    calls = []

    class FakeResponse:
        def __init__(self, body):
            self.status_code = 200
            self._body = body

        def json(self):
            return self._body

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/v1/oauth2/token"):
            return FakeResponse({"access_token": "token"})
        return FakeResponse({"verification_status": "SUCCESS"})

    monkeypatch.setattr("aura_music_studio.paypal_webhooks.requests.post", fake_post)
    verifier = PayPalWebhookVerifier()
    headers = {
        "paypal-transmission-id": "t1",
        "paypal-transmission-time": "2026-08-27T00:00:00Z",
        "paypal-cert-url": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-1",
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-sig": "sig",
    }

    assert verifier.verify(headers, paid_event()) is True
    assert calls[0][0] == "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    assert calls[1][0].endswith("/v1/notifications/verify-webhook-signature")
    assert calls[1][1]["json"]["webhook_id"] == "webhook"
    assert calls[1][1]["json"]["webhook_event"]["id"] == "WH-TEST-001"
