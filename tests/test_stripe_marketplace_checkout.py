from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.marketplace_orders import MarketplaceOrderStore
from aura_music_studio import stripe_marketplace_checkout as marketplace


class _StripeResponse:
    status_code = 200

    def __init__(self, session_id: str = "cs_test_marketplace"):
        self.session_id = session_id

    def json(self):
        return {
            "id": self.session_id,
            "url": f"https://checkout.stripe.com/c/pay/{self.session_id}",
        }


def _config():
    return SimpleNamespace(
        secret_key="sk_test_private",
        public_base_url="https://command.example",
    )


def _order(store: MarketplaceOrderStore, **overrides):
    data = {
        "provider": "stripe",
        "tenant_id": "tenant-a",
        "buyer_user_id": "buyer-a",
        "publication_id": "publication-a",
        "publication_revision": "rev-immutable-1",
        "gross_minor": 2500,
        "currency": "GBP",
        "creator_user_id": "creator-a",
    }
    data.update(overrides)
    return store.create_order(**data)


def _bound_session(order: dict, session_id: str = "cs_bound") -> dict:
    return {
        "id": session_id,
        "mode": "payment",
        "client_reference_id": order["buyer_user_id"],
        "metadata": {
            "purchase_kind": "marketplace",
            "marketplace_order_id": order["id"],
        },
        "amount_total": order["gross_minor"],
        "currency": str(order["currency"]).lower(),
        "url": f"https://checkout.stripe.com/c/pay/{session_id}",
    }


def test_checkout_submits_only_server_authoritative_price_and_opaque_order_metadata(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    captured = {}

    def fake_post(url, *, data, headers, timeout):
        captured.update(url=url, data=dict(data), headers=dict(headers), timeout=timeout)
        return _StripeResponse()

    monkeypatch.setattr(marketplace.httpx, "post", fake_post)
    session = marketplace.create_stripe_marketplace_session(
        order=order,
        user={"id": "buyer-a", "email": "buyer@example.test"},
        config=_config(),
    )

    assert session["id"] == "cs_test_marketplace"
    assert captured["data"]["line_items[0][price_data][unit_amount]"] == "2500"
    assert captured["data"]["line_items[0][price_data][currency]"] == "gbp"
    assert captured["data"]["metadata[purchase_kind]"] == "marketplace"
    assert captured["data"]["metadata[marketplace_order_id]"] == order["id"]
    assert not any("creator" in key for key in captured["data"])
    assert not any("publication" in key for key in captured["data"])
    assert not any("catalogue" in key for key in captured["data"])
    assert not any("tenant" in key for key in captured["data"])
    assert captured["headers"]["Idempotency-Key"] == marketplace._checkout_idempotency_key(order["id"])
    assert captured["headers"]["Idempotency-Key"].startswith("esp-marketplace-checkout-")
    assert "sk_test_private" not in str(captured["data"])


def test_cross_buyer_checkout_is_rejected_before_stripe(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    monkeypatch.setattr(
        marketplace.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called")),
    )
    monkeypatch.setattr(
        marketplace,
        "_stripe_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called")),
    )

    with pytest.raises(PermissionError):
        marketplace.create_stripe_marketplace_session(
            order=order,
            user={"id": "buyer-b", "email": "other@example.test"},
            config=_config(),
        )


def test_non_stripe_order_fails_before_provider_submission(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    other = _order(store, provider="paypal", publication_id="publication-b")
    monkeypatch.setattr(
        marketplace.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called")),
    )
    monkeypatch.setattr(
        marketplace,
        "_stripe_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Stripe must not be called")),
    )

    with pytest.raises(ValueError):
        marketplace.create_stripe_marketplace_session(
            order=other,
            user={"id": "buyer-a"},
            config=_config(),
        )


def test_bound_order_recovers_same_verified_checkout_without_new_post(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_bound")
    bound = marketplace._load_order(store, order["id"])
    monkeypatch.setattr(
        marketplace.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not create another Checkout Session")),
    )
    requested = []
    monkeypatch.setattr(
        marketplace,
        "_stripe_get",
        lambda config, path: requested.append(path) or _bound_session(bound),
    )

    session = marketplace.create_stripe_marketplace_session(
        order=bound,
        user={"id": "buyer-a", "email": "buyer@example.test"},
        config=_config(),
    )

    assert session["id"] == "cs_bound"
    assert requested == ["/v1/checkout/sessions/cs_bound"]


def test_bound_order_recovery_fails_closed_when_provider_identity_changes(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_bound")
    bound = marketplace._load_order(store, order["id"])
    changed = _bound_session(bound)
    changed["amount_total"] = 2600
    monkeypatch.setattr(marketplace, "_stripe_get", lambda config, path: changed)

    with pytest.raises(RuntimeError, match="could not be revalidated"):
        marketplace.create_stripe_marketplace_session(
            order=bound,
            user={"id": "buyer-a", "email": "buyer@example.test"},
            config=_config(),
        )


def test_checkout_route_requires_active_bound_buyer_and_binds_session(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    monkeypatch.setattr(marketplace, "orders", store)
    monkeypatch.setattr(
        marketplace,
        "_session_user",
        lambda request: {"id": "buyer-a", "email": "buyer@example.test", "status": "active"},
    )
    monkeypatch.setattr(marketplace.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(marketplace.httpx, "post", lambda *args, **kwargs: _StripeResponse("cs_route"))

    app = FastAPI()
    app.include_router(marketplace.router)
    response = TestClient(app).post("/billing/stripe/checkout/marketplace", json={"order_id": order["id"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["checkout_session_id"] == "cs_route"
    assert payload["subscription_effect"] == "none"
    assert payload["creation_coin_effect"] == "none"
    assert payload["esp_role_effect"] == "none"
    rebound = marketplace._load_order(store, order["id"])
    assert rebound["provider_checkout_reference"] == "cs_route"


def test_checkout_route_can_resume_already_bound_session(tmp_path, monkeypatch):
    store = MarketplaceOrderStore(tmp_path / "marketplace.sqlite3")
    order = _order(store)
    store.bind_provider_checkout(order_id=order["id"], provider_checkout_reference="cs_resume")
    bound = marketplace._load_order(store, order["id"])
    monkeypatch.setattr(marketplace, "orders", store)
    monkeypatch.setattr(
        marketplace,
        "_session_user",
        lambda request: {"id": "buyer-a", "email": "buyer@example.test", "status": "active"},
    )
    monkeypatch.setattr(marketplace.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(marketplace, "_stripe_get", lambda config, path: _bound_session(bound, "cs_resume"))
    monkeypatch.setattr(
        marketplace.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not create another Checkout Session")),
    )

    app = FastAPI()
    app.include_router(marketplace.router)
    response = TestClient(app).post("/billing/stripe/checkout/marketplace", json={"order_id": order["id"]})

    assert response.status_code == 200
    assert response.json()["checkout_session_id"] == "cs_resume"
    assert response.json()["checkout_url"].endswith("/cs_resume")


def test_marketplace_router_is_mounted_on_production_entrypoint():
    # FastAPI 0.137+ preserves included routers instead of flattening every APIRoute into
    # app.routes. Verify the release-relevant production OpenAPI surface in a clean interpreter
    # rather than relying on the framework's private router-tree representation.
    script = """
import app as production_entrypoint
schema = production_entrypoint.app.openapi()
target = '/billing/stripe/checkout/marketplace'
operation = (schema.get('paths', {}).get(target) or {}).get('post')
if not operation:
    raise SystemExit(
        f'missing production marketplace POST route: {target}; '
        f'known_billing_paths={sorted(path for path in schema.get("paths", {}) if path.startswith("/billing/"))!r}'
    )
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "clean-interpreter production marketplace composition failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
