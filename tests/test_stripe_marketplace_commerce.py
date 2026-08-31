from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import stripe_billing_hardening as hardening
from aura_music_studio import stripe_marketplace_commerce as commerce


def _config():
    return SimpleNamespace(
        secret_key="sk_test_private",
        webhook_secret="whsec_test_private",
        public_base_url="https://command.example",
        webhook_configured=True,
    )


class _Evidence:
    def __init__(self, *, duplicate=False, status="received"):
        self.duplicate = duplicate
        self.status = status
        self.finished = []

    def begin_event(self, event, raw):
        return {
            "event_id": event["id"],
            "processing_status": self.status,
            "duplicate": self.duplicate,
        }

    def finish_event(self, event_id, status, error=None):
        self.finished.append((event_id, status, error))


def _webhook_app():
    app = FastAPI()
    app.include_router(hardening.router)
    return TestClient(app)


def test_hardened_webhook_processes_marketplace_event_without_legacy_delegate(monkeypatch):
    evidence = _Evidence()
    processed = []
    monkeypatch.setattr(hardening, "evidence_store", evidence)
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda *args, **kwargs: None)
    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", lambda event_type, obj, config=None: True)
    monkeypatch.setattr(
        hardening,
        "process_verified_marketplace_stripe_event",
        lambda **kwargs: processed.append(kwargs) or {
            "processed": True,
            "kind": "marketplace_payment",
            "marketplace_order_id": "order-a",
        },
    )

    async def forbidden_legacy(request):
        raise AssertionError("marketplace event must not reach legacy billing processor")

    monkeypatch.setattr(hardening, "base_stripe_webhook", forbidden_legacy)
    event = {
        "id": "evt_marketplace_123",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_marketplace_123"}},
    }
    response = _webhook_app().post(
        "/billing/stripe/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test"},
    )
    assert response.status_code == 200
    assert response.json()["marketplace"] is True
    assert response.json()["processed"] is True
    assert processed[0]["event_id"] == "evt_marketplace_123"
    assert evidence.finished == [("evt_marketplace_123", "processed", None)]


def test_terminal_duplicate_marketplace_webhook_does_not_replay_settlement(monkeypatch):
    evidence = _Evidence(duplicate=True, status="processed")
    monkeypatch.setattr(hardening, "evidence_store", evidence)
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda *args, **kwargs: None)
    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", lambda event_type, obj, config=None: True)
    monkeypatch.setattr(
        hardening,
        "process_verified_marketplace_stripe_event",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("terminal duplicate must not replay")),
    )
    event = {
        "id": "evt_marketplace_duplicate",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_marketplace_123"}},
    }
    response = _webhook_app().post(
        "/billing/stripe/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test"},
    )
    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert response.json()["status"] == "processed"
    assert evidence.finished == []


def test_failed_duplicate_marketplace_webhook_is_retryable(monkeypatch):
    evidence = _Evidence(duplicate=True, status="failed")
    monkeypatch.setattr(hardening, "evidence_store", evidence)
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda *args, **kwargs: None)
    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", lambda event_type, obj, config=None: True)
    monkeypatch.setattr(
        hardening,
        "process_verified_marketplace_stripe_event",
        lambda **kwargs: {"processed": True, "kind": "marketplace_refund"},
    )
    event = {
        "id": "evt_marketplace_retry",
        "type": "refund.updated",
        "data": {"object": {"id": "re_marketplace_123"}},
    }
    response = _webhook_app().post(
        "/billing/stripe/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test"},
    )
    assert response.status_code == 200
    assert response.json()["processed"] is True
    assert evidence.finished == [("evt_marketplace_retry", "processed", None)]


def test_marketplace_event_classifier_supports_delayed_checkout_and_refund_events(monkeypatch):
    checkout = {"metadata": {"purchase_kind": "marketplace"}}
    assert commerce.is_marketplace_stripe_event("checkout.session.completed", checkout) is True
    assert commerce.is_marketplace_stripe_event("checkout.session.async_payment_succeeded", checkout) is True

    monkeypatch.setattr(commerce, "_refund_targets_verified_marketplace", lambda obj, config=None: True)
    assert commerce.is_marketplace_stripe_event("refund.created", {"id": "re_1"}) is True
    assert commerce.is_marketplace_stripe_event("refund.updated", {"id": "re_1"}) is True
    assert commerce.is_marketplace_stripe_event("charge.refund.updated", {"id": "re_1"}) is True
    assert commerce.is_marketplace_stripe_event("refund.failed", {"id": "re_1"}) is True


def test_out_of_order_refund_resolves_payment_intent_back_to_locally_bound_checkout(monkeypatch):
    captured = []
    monkeypatch.setattr(
        commerce,
        "_checkout_reference_is_marketplace",
        lambda session_id: session_id == "cs_bound_marketplace",
    )

    def fake_get(config, path):
        captured.append(path)
        return {"object": "list", "data": [{"id": "cs_bound_marketplace"}]}

    monkeypatch.setattr(commerce, "_stripe_get", fake_get)
    refund = {
        "id": "re_out_of_order_123",
        "payment_intent": "pi_out_of_order_123",
        "charge": "ch_out_of_order_123",
    }
    assert commerce.is_marketplace_stripe_event("refund.created", refund, config=_config()) is True
    assert captured == ["/v1/checkout/sessions?payment_intent=pi_out_of_order_123&limit=2"]


def test_out_of_order_refund_does_not_claim_unbound_checkout(monkeypatch):
    monkeypatch.setattr(commerce, "_checkout_reference_is_marketplace", lambda session_id: False)
    monkeypatch.setattr(
        commerce,
        "_stripe_get",
        lambda config, path: {"object": "list", "data": [{"id": "cs_unrelated"}]},
    )
    refund = {
        "id": "re_unrelated_123",
        "payment_intent": "pi_unrelated_123",
        "charge": "ch_unrelated_123",
    }
    assert commerce.is_marketplace_stripe_event("refund.updated", refund, config=_config()) is False


def test_refund_classification_provider_failure_is_audited_and_retryable(monkeypatch):
    evidence = _Evidence(duplicate=False, status="received")
    monkeypatch.setattr(hardening, "evidence_store", evidence)
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda *args, **kwargs: None)

    def fail_classification(event_type, obj, config=None):
        raise RuntimeError("temporary Stripe evidence lookup failure")

    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", fail_classification)
    event = {
        "id": "evt_refund_lookup_retry",
        "type": "refund.created",
        "data": {
            "object": {
                "id": "re_refund_lookup_retry",
                "payment_intent": "pi_retry_123",
                "charge": "ch_retry_123",
            }
        },
    }
    response = _webhook_app().post(
        "/billing/stripe/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test"},
    )
    assert response.status_code == 502
    assert evidence.finished == [
        ("evt_refund_lookup_retry", "failed", "temporary Stripe evidence lookup failure")
    ]


def test_marketplace_checkout_and_webhook_are_mounted_on_hardened_surface():
    paths = {getattr(route, "path", "") for route in hardening.router.routes}
    assert "/billing/stripe/checkout/marketplace" in paths
    assert "/billing/stripe/webhook" in paths
