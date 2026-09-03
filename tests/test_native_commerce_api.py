from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.native_commerce_api as commerce


@dataclass
class _Receipt:
    event_id: str = "evt-1"
    product_id: str = "aura_sec"
    event_type: str = "refund"


class _Ledger:
    def __init__(self):
        self.payment_calls = 0
        self.lifecycle_calls = 0
        self.error: Exception | None = None

    def process_verified_event(self, **kwargs):
        self.payment_calls += 1
        if self.error:
            raise self.error
        assert kwargs["raw_event"]
        assert kwargs["headers"]
        return _Receipt()

    def process_verified_lifecycle_event(self, **kwargs):
        self.lifecycle_calls += 1
        if self.error:
            raise self.error
        assert kwargs["raw_event"]
        assert kwargs["headers"]
        return _Receipt()


def _client(monkeypatch):
    ledger = _Ledger()
    monkeypatch.setattr(commerce, "native_entitlements", ledger)
    monkeypatch.setattr(commerce, "_payment_verifier", lambda: object())
    monkeypatch.setattr(commerce, "_lifecycle_verifier", lambda: object())
    app = FastAPI()
    app.include_router(commerce.router)
    return TestClient(app), ledger


def test_paid_event_reaches_only_verified_payment_boundary(monkeypatch):
    client, ledger = _client(monkeypatch)
    response = client.post(
        "/billing/native/paypal/webhook",
        json={"event_type": "INVOICING.INVOICE.PAID"},
        headers={"paypal-transmission-id": "tx-1"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "kind": "payment",
        "event_id": "evt-1",
        "product_id": "aura_sec",
    }
    assert ledger.payment_calls == 1
    assert ledger.lifecycle_calls == 0


def test_refund_event_reaches_only_verified_lifecycle_boundary(monkeypatch):
    client, ledger = _client(monkeypatch)
    response = client.post(
        "/billing/native/paypal/webhook",
        json={"event_type": "INVOICING.INVOICE.REFUNDED"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "refund"
    assert ledger.payment_calls == 0
    assert ledger.lifecycle_calls == 1


def test_cancel_event_uses_lifecycle_boundary(monkeypatch):
    client, ledger = _client(monkeypatch)
    response = client.post(
        "/billing/native/paypal/webhook",
        json={"event_type": "INVOICING.INVOICE.CANCELLED"},
    )
    assert response.status_code == 200
    assert ledger.lifecycle_calls == 1


def test_unsupported_event_never_reaches_ledger(monkeypatch):
    client, ledger = _client(monkeypatch)
    response = client.post(
        "/billing/native/paypal/webhook",
        json={"event_type": "PAYMENT.CAPTURE.COMPLETED"},
    )
    assert response.status_code == 400
    assert ledger.payment_calls == 0
    assert ledger.lifecycle_calls == 0


def test_duplicate_provider_retry_is_acknowledged(monkeypatch):
    client, ledger = _client(monkeypatch)
    ledger.error = ValueError("Provider event has already been processed")
    response = client.post(
        "/billing/native/paypal/webhook",
        json={"event_type": "INVOICING.INVOICE.PAID"},
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "duplicate": True}


def test_non_duplicate_ledger_validation_fails_closed(monkeypatch):
    client, ledger = _client(monkeypatch)
    ledger.error = ValueError("Verified payment amount does not match the canonical product price")
    response = client.post(
        "/billing/native/paypal/webhook",
        json={"event_type": "INVOICING.INVOICE.PAID"},
    )
    assert response.status_code == 400


def test_invalid_or_empty_body_is_rejected(monkeypatch):
    client, ledger = _client(monkeypatch)
    assert client.post("/billing/native/paypal/webhook", content=b"").status_code == 400
    assert client.post("/billing/native/paypal/webhook", content=b"not-json").status_code == 400
    assert ledger.payment_calls == 0
    assert ledger.lifecycle_calls == 0


def test_oversized_body_is_rejected_before_parsing(monkeypatch):
    client, ledger = _client(monkeypatch)
    response = client.post(
        "/billing/native/paypal/webhook",
        content=b"{" + b"x" * commerce._MAX_WEBHOOK_BYTES,
    )
    assert response.status_code == 413
    assert ledger.payment_calls == 0
    assert ledger.lifecycle_calls == 0
