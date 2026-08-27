from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs, urlparse

import pytest

from aura_music_studio.esp_shop_automation_overlay import router as shop_router
from aura_music_studio.esp_shop_provider_callback_security import CALLBACK_PATH
from aura_music_studio.esp_shop_provider_runtime import clear_provider_adapters
from aura_music_studio.esp_shopify_provider import (
    DEFAULT_SHOPIFY_SCOPES,
    InMemoryShopSecretBackend,
    SHOPIFY_API_VERSION,
    ShopifyProviderAdapter,
    ShopifyProviderError,
    ShopifyReauthorizationRequired,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No fake HTTP response configured")
        return self.responses.pop(0)


class FakeClock:
    def __init__(self, value=1_800_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value


def _connection(shop="example-store.myshopify.com"):
    return {
        "id": "connection-1",
        "provider": "shopify",
        "external_account_ref": shop,
        "scopes": [],
    }


def _adapter(*, responses=None, clock=None, scopes=DEFAULT_SHOPIFY_SCOPES):
    backend = InMemoryShopSecretBackend()
    http = FakeHttp(responses)
    adapter = ShopifyProviderAdapter(
        client_id="client-id",
        client_secret="client-secret",
        secret_backend=backend,
        requested_scopes=list(scopes),
        http=http,
        clock=clock or FakeClock(),
    )
    return adapter, backend, http


def _signed_callback(secret: str, **params) -> dict[str, str]:
    clean = {str(key): str(value) for key, value in params.items()}
    message = "&".join(f"{key}={clean[key]}" for key in sorted(clean))
    clean["hmac"] = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return clean


def test_authorization_url_is_bound_to_exact_myshopify_domain_and_https_callback():
    adapter, _backend, _http = _adapter()
    url = adapter.authorization_url(
        _connection(),
        state="nonce-value",
        callback_url="https://pulsar.example/command-center/shop-automation/oauth/shopify/callback",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "example-store.myshopify.com"
    assert parsed.path == "/admin/oauth/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["client-id"]
    assert query["state"] == ["nonce-value"]
    assert query["scope"] == [",".join(sorted(DEFAULT_SHOPIFY_SCOPES))]

    with pytest.raises(ValueError, match="myshopify"):
        adapter.authorization_url(
            _connection("example-store.myshopify.com.attacker.example"),
            state="nonce",
            callback_url="https://pulsar.example/callback",
        )
    with pytest.raises(ValueError, match="HTTPS"):
        adapter.authorization_url(
            _connection(), state="nonce", callback_url="http://pulsar.example/callback"
        )


def test_callback_hmac_and_shop_binding_are_required():
    adapter, _backend, _http = _adapter()
    params = _signed_callback(
        "client-secret",
        code="verified-code",
        shop="example-store.myshopify.com",
        state="nonce",
        timestamp="1800000000",
    )
    adapter.verify_oauth_callback(_connection(), params)

    tampered = dict(params)
    tampered["shop"] = "other-store.myshopify.com"
    with pytest.raises(PermissionError):
        adapter.verify_oauth_callback(_connection(), tampered)

    bad_hmac = dict(params)
    bad_hmac["hmac"] = "0" * 64
    with pytest.raises(PermissionError, match="HMAC"):
        adapter.verify_oauth_callback(_connection(), bad_hmac)


def test_oauth_exchange_requests_expiring_token_and_stores_bundle_only_in_secret_backend():
    token_payload = {
        "access_token": "shpat_access_secret",
        "refresh_token": "refresh_secret",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
        "scope": "write_orders,write_merchant_managed_fulfillment_orders",
    }
    clock = FakeClock()
    adapter, backend, http = _adapter(
        responses=[FakeResponse(200, token_payload)],
        clock=clock,
    )
    verified = adapter.exchange_oauth_code(
        _connection(),
        code="verified-code",
        callback_url="https://pulsar.example/command-center/shop-automation/oauth/shopify/callback",
    )
    assert verified["secret_ref"].startswith("memory-shop-secret://")
    assert verified["external_account_ref"] == "example-store.myshopify.com"
    # Orders can be advertised now. Shipping labels deliberately cannot be advertised until
    # the generic runtime has asynchronous reconciliation semantics.
    assert verified["scopes"] == ["orders"]
    assert "shipping_labels" not in verified["scopes"]
    call = http.calls[0]
    assert call["url"] == "https://example-store.myshopify.com/admin/oauth/access_token"
    assert call["data"]["client_id"] == "client-id"
    assert call["data"]["client_secret"] == "client-secret"
    assert call["data"]["code"] == "verified-code"
    assert call["data"]["expiring"] == "1"

    stored = backend.get(verified["secret_ref"])
    assert stored["access_token"] == "shpat_access_secret"
    assert stored["refresh_token"] == "refresh_secret"
    assert stored["access_expires_at"] == clock.value + 3600


def test_missing_requested_scope_fails_closed_before_connection_can_be_verified():
    adapter, _backend, _http = _adapter(
        responses=[
            FakeResponse(
                200,
                {
                    "access_token": "shpat_access_secret",
                    "refresh_token": "refresh_secret",
                    "expires_in": 3600,
                    "scope": "write_orders",
                },
            )
        ]
    )
    with pytest.raises(PermissionError, match="requested access scopes"):
        adapter.exchange_oauth_code(
            _connection(),
            code="verified-code",
            callback_url="https://pulsar.example/callback",
        )


def test_expiring_offline_token_is_refreshed_and_rotated_in_secret_backend():
    clock = FakeClock()
    adapter, backend, http = _adapter(
        responses=[
            FakeResponse(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "refresh_token_expires_in": 7776000,
                    "scope": "write_orders,write_merchant_managed_fulfillment_orders",
                },
            )
        ],
        clock=clock,
    )
    secret_ref = backend.put(
        "shopify:example-store.myshopify.com",
        {
            "provider": "shopify",
            "shop": "example-store.myshopify.com",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "granted_scopes": ["write_orders", "write_merchant_managed_fulfillment_orders"],
            "access_expires_at": clock.value + 30,
            "refresh_expires_at": clock.value + 7776000,
        },
    )
    shop, token = adapter.access_token(secret_ref)
    assert shop == "example-store.myshopify.com"
    assert token == "new-access"
    assert http.calls[0]["data"]["grant_type"] == "refresh_token"
    assert http.calls[0]["data"]["refresh_token"] == "old-refresh"
    stored = backend.get(secret_ref)
    assert stored["refresh_token"] == "new-refresh"


def test_terminal_refresh_401_requires_reauthorization():
    clock = FakeClock()
    adapter, backend, _http = _adapter(responses=[FakeResponse(401, {"error": "invalid_request"})], clock=clock)
    secret_ref = backend.put(
        "shopify:example-store.myshopify.com",
        {
            "shop": "example-store.myshopify.com",
            "access_token": "old-access",
            "refresh_token": "expired-refresh",
            "access_expires_at": clock.value,
        },
    )
    with pytest.raises(ShopifyReauthorizationRequired):
        adapter.access_token(secret_ref)


def test_graphql_uses_stable_version_and_access_token_header_without_logging_token():
    clock = FakeClock()
    adapter, backend, http = _adapter(
        responses=[FakeResponse(200, {"data": {"shop": {"id": "gid://shopify/Shop/1"}}})],
        clock=clock,
    )
    secret_ref = backend.put(
        "shopify:example-store.myshopify.com",
        {
            "shop": "example-store.myshopify.com",
            "access_token": "live-access-token",
            "refresh_token": "refresh",
            "access_expires_at": clock.value + 3000,
        },
    )
    result = adapter.graphql(secret_ref, query="query { shop { id } }")
    assert result["data"]["shop"]["id"].endswith("/1")
    call = http.calls[0]
    assert f"/admin/api/{SHOPIFY_API_VERSION}/graphql.json" in call["url"]
    assert call["headers"]["X-Shopify-Access-Token"] == "live-access-token"
    diagnostics = adapter.diagnostics()
    assert "live-access-token" not in str(diagnostics)
    assert diagnostics["raw_token_exposed"] is False


def test_shipping_label_start_preserves_pending_state_and_poll_can_confirm_purchase():
    clock = FakeClock()
    adapter, backend, _http = _adapter(
        responses=[
            FakeResponse(
                200,
                {
                    "data": {
                        "shippingLabelPurchase": {
                            "shippingLabelPurchaseResult": {
                                "id": "gid://shopify/ShippingLabelPurchaseResult/123",
                                "status": "PENDING_PURCHASE",
                                "done": False,
                                "errors": [],
                            },
                            "userErrors": [],
                        }
                    }
                },
            ),
            FakeResponse(
                200,
                {
                    "data": {
                        "node": {
                            "id": "gid://shopify/ShippingLabelPurchaseResult/123",
                            "status": "PURCHASED",
                            "done": True,
                            "errors": [],
                            "shippingLabels": [{"id": "gid://shopify/ShippingLabel/456"}],
                        }
                    }
                },
            ),
        ],
        clock=clock,
    )
    secret_ref = backend.put(
        "shopify:example-store.myshopify.com",
        {
            "shop": "example-store.myshopify.com",
            "access_token": "access",
            "refresh_token": "refresh",
            "access_expires_at": clock.value + 3000,
        },
    )
    started = adapter.start_shipping_label_purchase(
        secret_ref,
        {
            "fulfillmentOrderId": "gid://shopify/FulfillmentOrder/42",
            "shippingDatetime": "2026-08-28T10:00:00Z",
            "notifyCustomer": False,
        },
    )
    assert started == {
        "execution_ref": "gid://shopify/ShippingLabelPurchaseResult/123",
        "status": "PENDING_PURCHASE",
        "done": False,
        "errors": [],
    }
    polled = adapter.poll_shipping_label_purchase(secret_ref, started["execution_ref"])
    assert polled["status"] == "PURCHASED"
    assert polled["done"] is True
    assert polled["shipping_label_ids"] == ["gid://shopify/ShippingLabel/456"]


def test_shipping_label_runtime_write_remains_blocked_until_async_reconciliation_exists():
    adapter, _backend, _http = _adapter()
    with pytest.raises(ShopifyProviderError, match="asynchronous purchase reconciliation"):
        adapter.execute(
            {"action_type": "purchase_shipping_label"},
            _connection(),
            secret_ref="memory-shop-secret://unused/ref",
        )
    diagnostics = adapter.diagnostics()
    assert diagnostics["shipping_label_graphql_primitives"] is True
    assert diagnostics["shipping_label_runtime_execution_enabled"] is False


def test_shop_overlay_exposes_only_secured_callback_route():
    callback_routes = [
        route
        for route in shop_router.routes
        if getattr(route, "path", None) == CALLBACK_PATH
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert len(callback_routes) == 1
    assert getattr(callback_routes[0], "endpoint").__name__ == "secured_oauth_callback"


def test_shopify_module_does_not_auto_register_or_embed_production_credentials():
    clear_provider_adapters()
    # Importing/constructing the adapter does not silently activate Shopify. Deployment must
    # provide credentials and an external secret backend, then explicitly register it.
    adapter, _backend, _http = _adapter()
    assert adapter.diagnostics()["configured"] is True
    from aura_music_studio import esp_shop_provider_runtime as runtime

    assert "shopify" not in runtime.PROVIDER_ADAPTERS
