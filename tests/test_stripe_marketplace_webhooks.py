from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import stripe_billing_hardening as hardening
from aura_music_studio import stripe_marketplace_webhooks as webhooks
from aura_music_studio.marketplace_orders import MarketplaceOrderStore
from aura_music_studio.stripe_billing import StripeConfig
from aura_music_studio.stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore


def _config() -> StripeConfig:
    return StripeConfig(
        secret_key="sk_test_private",
        webhook_secret="whsec_test_private",
        public_base_url="https://command.example",
        base_price_id="",
        pro_price_id="",
        settlement_label="",
    )


class _Evidence:
    def __init__(self, *, duplicate: bool = False, status: str = "received"):
        self.duplicate = duplicate
        self.status = status
        self.finished: list[tuple[str, str, str | None]] = []

    def begin_event(self, event, raw):
        return {
            "event_id": event["id"],
            "processing_status": self.status,
            "duplicate": self.duplicate,
        }

    def finish_event(self, event_id, status, error=None):
        self.finished.append((event_id, status, error))


def _webhook_client() -> TestClient:
    app = FastAPI()
    app.include_router(hardening.router)
    return TestClient(app)


def _post_event(client: TestClient, event: dict):
    return client.post(
        "/billing/stripe/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test"},
    )


def _patch_verified_webhook(monkeypatch, *, evidence: _Evidence):
    monkeypatch.setattr(hardening, "evidence_store", evidence)
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda *args, **kwargs: None)


def test_hardened_webhook_processes_marketplace_event_without_legacy_delegate(monkeypatch):
    evidence = _Evidence()
    processed = []
    _patch_verified_webhook(monkeypatch, evidence=evidence)
    monkeypatch.setattr(
        hardening,
        "is_marketplace_stripe_event",
        lambda event_type, obj, *, config=None: True,
    )
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
    response = _post_event(_webhook_client(), event)

    assert response.status_code == 200
    assert response.json()["marketplace"] is True
    assert response.json()["processed"] is True
    assert processed[0]["event_id"] == "evt_marketplace_123"
    assert isinstance(processed[0]["config"], StripeConfig)
    assert evidence.finished == [("evt_marketplace_123", "processed", None)]


def test_terminal_duplicate_marketplace_webhook_does_not_replay_settlement(monkeypatch):
    evidence = _Evidence(duplicate=True, status="processed")
    _patch_verified_webhook(monkeypatch, evidence=evidence)
    monkeypatch.setattr(
        hardening,
        "is_marketplace_stripe_event",
        lambda event_type, obj, *, config=None: True,
    )
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
    response = _post_event(_webhook_client(), event)

    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert response.json()["status"] == "processed"
    assert evidence.finished == []


def test_failed_duplicate_marketplace_webhook_is_retryable(monkeypatch):
    evidence = _Evidence(duplicate=True, status="failed")
    _patch_verified_webhook(monkeypatch, evidence=evidence)
    monkeypatch.setattr(
        hardening,
        "is_marketplace_stripe_event",
        lambda event_type, obj, *, config=None: True,
    )
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
    response = _post_event(_webhook_client(), event)

    assert response.status_code == 200
    assert response.json()["processed"] is True
    assert evidence.finished == [("evt_marketplace_retry", "processed", None)]


def test_transient_refund_classification_failure_is_persisted_for_retry(monkeypatch):
    evidence = _Evidence()
    _patch_verified_webhook(monkeypatch, evidence=evidence)
    monkeypatch.setattr(
        hardening,
        "is_marketplace_stripe_event",
        lambda event_type, obj, *, config=None: (_ for _ in ()).throw(RuntimeError("Stripe lookup unavailable")),
    )
    event = {
        "id": "evt_refund_lookup_retry",
        "type": "refund.updated",
        "data": {"object": {"id": "re_marketplace_123", "payment_intent": "pi_123"}},
    }
    response = _post_event(_webhook_client(), event)

    assert response.status_code == 502
    assert evidence.finished == [("evt_refund_lookup_retry", "failed", "Stripe lookup unavailable")]


def test_classifier_supports_marketplace_payment_and_refund_events(monkeypatch):
    checkout = {"metadata": {"purchase_kind": "marketplace"}}
    assert webhooks.is_marketplace_stripe_event("checkout.session.completed", checkout, config=_config()) is True
    assert webhooks.is_marketplace_stripe_event("checkout.session.async_payment_succeeded", checkout, config=_config()) is True
    assert webhooks.is_marketplace_stripe_event(
        "checkout.session.completed",
        {"metadata": {"purchase_kind": "subscription"}},
        config=_config(),
    ) is False

    monkeypatch.setattr(
        webhooks,
        "_refund_targets_verified_marketplace",
        lambda obj, *, config=None: True,
    )
    for event_type in ("refund.created", "refund.updated", "refund.failed", "charge.refund.updated"):
        assert webhooks.is_marketplace_stripe_event(event_type, {"id": "re_1"}, config=_config()) is True


def test_refund_classifier_uses_verified_fee_evidence_without_provider_lookup(tmp_path, monkeypatch):
    store = StripeMarketplaceFeeEvidenceStore(tmp_path / "marketplace.sqlite3")
    store.record(
        event_id="evt_marketplace_1",
        checkout_session_id="cs_marketplace_1",
        order_id="order-marketplace-1",
        payment_intent_id="pi_marketplace_1",
        charge_id="ch_marketplace_1",
        balance_transaction_id="txn_marketplace_1",
        gross_minor=2500,
        provider_fee_minor=100,
        net_minor=2400,
        currency="GBP",
    )
    monkeypatch.setattr(webhooks, "fee_evidence", store)
    monkeypatch.setattr(
        webhooks,
        "_stripe_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider lookup is unnecessary")),
    )

    assert webhooks._refund_targets_verified_marketplace(
        {"payment_intent": "pi_marketplace_1", "charge": "ch_marketplace_1"},
        config=_config(),
    ) is True


def test_out_of_order_refund_resolves_payment_intent_to_locally_bound_checkout(tmp_path, monkeypatch):
    db = tmp_path / "marketplace.sqlite3"
    order_store = MarketplaceOrderStore(db)
    order = order_store.create_order(
        provider="stripe",
        tenant_id="tenant-a",
        buyer_user_id="buyer-a",
        publication_id="publication-a",
        publication_revision="revision-a",
        gross_minor=2500,
        currency="GBP",
        creator_user_id="creator-a",
    )
    order_store.bind_provider_checkout(
        order_id=order["id"],
        provider_checkout_reference="cs_marketplace_bound",
    )
    monkeypatch.setattr(webhooks, "orders", order_store)
    monkeypatch.setattr(webhooks, "fee_evidence", StripeMarketplaceFeeEvidenceStore(db))
    monkeypatch.setattr(
        webhooks,
        "_stripe_get",
        lambda config, path: {
            "data": [
                {
                    "id": "cs_marketplace_bound",
                    "payment_intent": "pi_marketplace_out_of_order",
                }
            ]
        },
    )

    assert webhooks._refund_targets_verified_marketplace(
        {"payment_intent": "pi_marketplace_out_of_order", "charge": "ch_unknown"},
        config=_config(),
    ) is True


def test_production_openapi_mounts_marketplace_checkout_and_webhook():
    import app as production_entrypoint

    paths = production_entrypoint.app.openapi().get("paths", {})
    assert "post" in paths["/billing/stripe/checkout/marketplace"]
    assert "post" in paths["/billing/stripe/webhook"]
