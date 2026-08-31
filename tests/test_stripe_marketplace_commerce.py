from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.marketplace_orders import MarketplaceOrderStore
from aura_music_studio import stripe_marketplace_commerce as commerce
from aura_music_studio import stripe_billing_hardening as hardening


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def _config():
    return SimpleNamespace(
        secret_key="sk_test_private",
        webhook_secret="whsec_test_private",
        public_base_url="https://command.example",
    )


def _order(store: MarketplaceOrderStore):
    return store.create_order(
        provider="stripe",
        tenant_id="tenant-a",
        buyer_user_id="buyer-a",
        publication_id="publication-a",
        publication_revision="revision-a",
        gross_minor=2500,
        currency="GBP",
        creator_user_id="creator-a",
    )


def test_checkout_uses_only_immutable_order_commercial_facts(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    captured = {}

    def fake_post(url, *, data, headers, timeout):
        captured.update(url=url, data=dict(data), headers=dict(headers), timeout=timeout)
        return _Response(
            {
                "id": "cs_marketplace_123",
                "url": "https://checkout.stripe.com/c/pay/cs_marketplace_123",
            }
        )

    monkeypatch.setattr(commerce.httpx, "post", fake_post)
    result = commerce.create_marketplace_checkout_session(
        store=store,
        order_id=order["id"],
        user={"id": "buyer-a", "email": "buyer@example.test", "status": "active"},
        config=_config(),
    )

    assert result["checkout_session_id"] == "cs_marketplace_123"
    assert result["subscription_effect"] == "none"
    assert result["creation_coin_effect"] == "none"
    assert result["esp_role_effect"] == "none"
    assert captured["data"]["line_items[0][price_data][unit_amount]"] == "2500"
    assert captured["data"]["line_items[0][price_data][currency]"] == "gbp"
    assert captured["data"]["metadata[purchase_kind]"] == "marketplace"
    assert captured["data"]["metadata[marketplace_order_id]"] == order["id"]
    assert not any("creator" in key for key in captured["data"])
    assert not any("publication" in key for key in captured["data"])
    assert not any("tenant" in key for key in captured["data"])
    assert captured["headers"]["Idempotency-Key"] == commerce._checkout_idempotency_key(order["id"])
    rebound = commerce._load_order(store, order["id"])
    assert rebound["provider_checkout_reference"] == "cs_marketplace_123"


def test_bound_checkout_retry_retrieves_same_session_without_new_post(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    store.bind_provider_checkout(
        order_id=order["id"],
        provider_checkout_reference="cs_marketplace_bound",
    )
    monkeypatch.setattr(
        commerce.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not create another Checkout Session")),
    )
    monkeypatch.setattr(
        commerce,
        "_stripe_get",
        lambda config, path: {
            "id": "cs_marketplace_bound",
            "mode": "payment",
            "client_reference_id": "buyer-a",
            "metadata": {
                "purchase_kind": "marketplace",
                "marketplace_order_id": order["id"],
            },
            "amount_total": 2500,
            "currency": "gbp",
            "url": "https://checkout.stripe.com/c/pay/cs_marketplace_bound",
        },
    )

    result = commerce.create_marketplace_checkout_session(
        store=store,
        order_id=order["id"],
        user={"id": "buyer-a", "email": "buyer@example.test", "status": "active"},
        config=_config(),
    )
    assert result["checkout_session_id"] == "cs_marketplace_bound"


def test_cross_buyer_checkout_fails_before_stripe_submission(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    monkeypatch.setattr(
        commerce.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called")),
    )

    try:
        commerce.create_marketplace_checkout_session(
            store=store,
            order_id=order["id"],
            user={"id": "buyer-b", "email": "other@example.test", "status": "active"},
            config=_config(),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("cross-buyer marketplace checkout must fail")


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
    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", lambda event_type, obj: True)
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
    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", lambda event_type, obj: True)
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
    monkeypatch.setattr(hardening, "is_marketplace_stripe_event", lambda event_type, obj: True)
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

    monkeypatch.setattr(commerce, "_refund_targets_verified_marketplace", lambda obj: True)
    assert commerce.is_marketplace_stripe_event("refund.created", {"id": "re_1"}) is True
    assert commerce.is_marketplace_stripe_event("refund.updated", {"id": "re_1"}) is True
    assert commerce.is_marketplace_stripe_event("charge.refund.updated", {"id": "re_1"}) is True
    assert commerce.is_marketplace_stripe_event("refund.failed", {"id": "re_1"}) is True


def test_marketplace_checkout_route_is_mounted_on_hardened_surface():
    paths = {getattr(route, "path", "") for route in hardening.router.routes}
    assert "/billing/stripe/checkout/marketplace" in paths
    assert "/billing/stripe/webhook" in paths
