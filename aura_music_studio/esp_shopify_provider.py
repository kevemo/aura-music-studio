from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import requests

from . import esp_shop_provider_runtime as runtime

SHOPIFY_API_VERSION = "2026-07"
SHOPIFY_TIMEOUT_SECONDS = 30
SHOPIFY_REFRESH_SKEW_SECONDS = 60
SHOPIFY_FULFILLMENT_WRITE_SCOPES = {
    "write_merchant_managed_fulfillment_orders",
    "write_assigned_fulfillment_orders",
    "write_third_party_fulfillment_orders",
}
DEFAULT_SHOPIFY_SCOPES = (
    "write_orders",
    "write_merchant_managed_fulfillment_orders",
)


class ShopSecretBackend(Protocol):
    """External secret persistence used for provider token bundles.

    Implementations must keep the token payload out of the normal ESP application database and
    return an opaque URI-like reference suitable for the provider runtime's `secret_ref` field.
    """

    persistent: bool

    def put(self, key: str, value: dict[str, Any]) -> str: ...

    def get(self, secret_ref: str) -> dict[str, Any]: ...

    def replace(self, secret_ref: str, value: dict[str, Any]) -> None: ...

    def delete(self, secret_ref: str) -> None: ...


class InMemoryShopSecretBackend:
    """Test/development-only secret backend. Never auto-registered in production."""

    persistent = False

    def __init__(self):
        self._values: dict[str, dict[str, Any]] = {}

    def put(self, key: str, value: dict[str, Any]) -> str:
        ref = f"memory-shop-secret://{uuid4().hex}/{hashlib.sha256(key.encode()).hexdigest()[:16]}"
        self._values[ref] = dict(value)
        return ref

    def get(self, secret_ref: str) -> dict[str, Any]:
        if secret_ref not in self._values:
            raise KeyError(secret_ref)
        return dict(self._values[secret_ref])

    def replace(self, secret_ref: str, value: dict[str, Any]) -> None:
        if secret_ref not in self._values:
            raise KeyError(secret_ref)
        self._values[secret_ref] = dict(value)

    def delete(self, secret_ref: str) -> None:
        self._values.pop(secret_ref, None)


class ShopifyProviderError(RuntimeError):
    pass


class ShopifyReauthorizationRequired(ShopifyProviderError):
    pass


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _shop_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("https://"):
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid Shopify shop domain")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port:
            raise ValueError("Shopify shop reference must contain only the shop domain")
        raw = parsed.hostname
    elif "://" in raw:
        raise ValueError("Shopify shop domain must use HTTPS")
    if not raw.endswith(".myshopify.com"):
        raise ValueError("Shopify shop domain must end in .myshopify.com")
    label = raw[: -len(".myshopify.com")]
    if not label or len(label) > 63 or not label[0].isalnum():
        raise ValueError("Invalid Shopify shop domain")
    if any(not (ch.isalnum() or ch == "-") for ch in label) or label.endswith("-"):
        raise ValueError("Invalid Shopify shop domain")
    return raw


def _callback_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Shopify OAuth callback must be an HTTPS URL")
    return parsed.geturl()


def _scope_set(value: str | list[str] | tuple[str, ...] | set[str]) -> set[str]:
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value
    return {str(item).strip() for item in items if str(item).strip()}


def _scope_satisfied(requested: str, granted: set[str]) -> bool:
    if requested in granted:
        return True
    if requested.startswith("read_") and f"write_{requested[5:]}" in granted:
        return True
    return False


def _safe_provider_errors(errors: Any, limit: int = 8) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(errors, list):
        return result
    for error in errors[:limit]:
        if not isinstance(error, dict):
            continue
        result.append(
            {
                "code": str(error.get("code") or "")[:120],
                "message": str(error.get("message") or "")[:500],
            }
        )
    return result


class ShopifyProviderAdapter:
    """Shopify standalone-app OAuth + GraphQL transport.

    This adapter intentionally does not advertise asynchronous shipping-label execution to the
    generic Shop runtime yet. It exposes explicit start/poll primitives so the next orchestration
    layer can persist `PENDING_PURCHASE` safely instead of falsely marking the action executed.
    """

    provider = "shopify"
    requires_signed_callback = True

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        secret_backend: ShopSecretBackend,
        requested_scopes: tuple[str, ...] | list[str] = DEFAULT_SHOPIFY_SCOPES,
        api_version: str = SHOPIFY_API_VERSION,
        http: Any | None = None,
        timeout_seconds: int = SHOPIFY_TIMEOUT_SECONDS,
        clock: Any | None = None,
    ):
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        if not self.client_id or not self.client_secret:
            raise ValueError("Shopify client ID and client secret are required")
        self.secret_backend = secret_backend
        self.requested_scopes = tuple(sorted(_scope_set(list(requested_scopes))))
        if not self.requested_scopes:
            raise ValueError("At least one Shopify access scope is required")
        version = str(api_version or "").strip()
        if not version or len(version) > 20:
            raise ValueError("Invalid Shopify API version")
        self.api_version = version
        self.http = http or requests.Session()
        self.timeout_seconds = max(1, min(int(timeout_seconds), 60))
        self.clock = clock or _now_epoch

    @staticmethod
    def _connection_shop(connection: dict) -> str:
        return _shop_domain(str(connection.get("external_account_ref") or ""))

    def authorization_url(self, connection: dict, *, state: str, callback_url: str) -> str:
        shop = self._connection_shop(connection)
        callback = _callback_url(callback_url)
        query = urlencode(
            {
                "client_id": self.client_id,
                "scope": ",".join(self.requested_scopes),
                "redirect_uri": callback,
                "state": str(state),
            }
        )
        return f"https://{shop}/admin/oauth/authorize?{query}"

    def verify_oauth_callback(self, connection: dict, callback_params: dict[str, str]) -> None:
        supplied_hmac = str(callback_params.get("hmac") or "").strip().lower()
        shop = _shop_domain(str(callback_params.get("shop") or ""))
        expected_shop = self._connection_shop(connection)
        if shop != expected_shop:
            raise PermissionError("Shopify OAuth callback shop does not match the requested connection")
        if not supplied_hmac or len(supplied_hmac) != 64:
            raise PermissionError("Shopify OAuth callback HMAC is missing or invalid")
        canonical = "&".join(
            f"{key}={callback_params[key]}"
            for key in sorted(callback_params)
            if key != "hmac"
        )
        digest = hmac.new(
            self.client_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(digest, supplied_hmac):
            raise PermissionError("Shopify OAuth callback HMAC verification failed")

    def _token_endpoint(self, shop: str) -> str:
        return f"https://{_shop_domain(shop)}/admin/oauth/access_token"

    def _graphql_endpoint(self, shop: str) -> str:
        return f"https://{_shop_domain(shop)}/admin/api/{self.api_version}/graphql.json"

    @staticmethod
    def _json(response: Any, context: str) -> dict[str, Any]:
        try:
            value = response.json()
        except Exception as exc:
            raise ShopifyProviderError(f"Shopify {context} returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise ShopifyProviderError(f"Shopify {context} returned an invalid response")
        return value

    def _post_token(self, shop: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
        try:
            response = self.http.post(
                self._token_endpoint(shop),
                data=data,
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise ShopifyProviderError("Could not reach Shopify token service") from exc
        payload = self._json(response, "token service")
        return int(getattr(response, "status_code", 0) or 0), payload

    def _bundle_from_token_response(self, shop: str, payload: dict[str, Any]) -> dict[str, Any]:
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not access_token:
            raise ShopifyProviderError("Shopify token response did not include an access token")
        try:
            expires_in = max(0, int(payload.get("expires_in") or 0))
        except (TypeError, ValueError):
            expires_in = 0
        try:
            refresh_expires_in = max(0, int(payload.get("refresh_token_expires_in") or 0))
        except (TypeError, ValueError):
            refresh_expires_in = 0
        now = float(self.clock())
        return {
            "provider": "shopify",
            "shop": _shop_domain(shop),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "granted_scopes": sorted(_scope_set(str(payload.get("scope") or ""))),
            "access_expires_at": now + expires_in if expires_in else None,
            "refresh_expires_at": now + refresh_expires_in if refresh_expires_in else None,
            "stored_at": now,
        }

    def _runtime_capabilities(self, granted: set[str]) -> list[str]:
        # Only advertise capabilities this adapter can truthfully execute through the current
        # runtime. Shipping-label purchase remains withheld until async reconciliation lands.
        capabilities: list[str] = []
        if "read_orders" in granted or "write_orders" in granted:
            capabilities.append("orders")
        return capabilities

    def exchange_oauth_code(self, connection: dict, *, code: str, callback_url: str) -> dict:
        shop = self._connection_shop(connection)
        _callback_url(callback_url)
        status, payload = self._post_token(
            shop,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": str(code or ""),
                "expiring": "1",
            },
        )
        if status != 200:
            raise ShopifyProviderError(f"Shopify OAuth token exchange failed with HTTP {status}")
        bundle = self._bundle_from_token_response(shop, payload)
        granted = set(bundle["granted_scopes"])
        missing = [scope for scope in self.requested_scopes if not _scope_satisfied(scope, granted)]
        if missing:
            raise PermissionError("Shopify did not grant all requested access scopes")
        secret_ref = self.secret_backend.put(f"shopify:{shop}", bundle)
        return {
            "secret_ref": secret_ref,
            "external_account_ref": shop,
            "scopes": self._runtime_capabilities(granted),
        }

    def _refresh_bundle(self, secret_ref: str, bundle: dict[str, Any]) -> dict[str, Any]:
        shop = _shop_domain(str(bundle.get("shop") or ""))
        refresh_token = str(bundle.get("refresh_token") or "").strip()
        if not refresh_token:
            raise ShopifyReauthorizationRequired("Shopify reauthorization is required")
        status, payload = self._post_token(
            shop,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if status == 401:
            raise ShopifyReauthorizationRequired("Shopify refresh token is no longer valid")
        if status == 429 or status >= 500:
            raise ShopifyProviderError("Shopify token refresh is temporarily unavailable")
        if status != 200:
            raise ShopifyProviderError(f"Shopify token refresh failed with HTTP {status}")
        refreshed = self._bundle_from_token_response(shop, payload)
        self.secret_backend.replace(secret_ref, refreshed)
        return refreshed

    def access_token(self, secret_ref: str) -> tuple[str, str]:
        try:
            bundle = self.secret_backend.get(secret_ref)
        except Exception as exc:
            raise ShopifyReauthorizationRequired("Shopify credential reference could not be resolved") from exc
        shop = _shop_domain(str(bundle.get("shop") or ""))
        expires_at = _number_or_none(bundle.get("access_expires_at"))
        now = float(self.clock())
        if expires_at is not None and now >= expires_at - SHOPIFY_REFRESH_SKEW_SECONDS:
            bundle = self._refresh_bundle(secret_ref, bundle)
        access_token = str(bundle.get("access_token") or "").strip()
        if not access_token:
            raise ShopifyReauthorizationRequired("Shopify access token is unavailable")
        return shop, access_token

    def graphql(self, secret_ref: str, *, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        shop, access_token = self.access_token(secret_ref)

        def send(token: str):
            try:
                return self.http.post(
                    self._graphql_endpoint(shop),
                    json={"query": str(query), "variables": variables or {}},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "X-Shopify-Access-Token": token,
                    },
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:
                raise ShopifyProviderError("Could not reach Shopify GraphQL Admin API") from exc

        response = send(access_token)
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            bundle = self.secret_backend.get(secret_ref)
            refreshed = self._refresh_bundle(secret_ref, bundle)
            response = send(str(refreshed.get("access_token") or ""))
            status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            raise ShopifyReauthorizationRequired("Shopify rejected the refreshed credential")
        if status == 429 or status >= 500:
            raise ShopifyProviderError("Shopify GraphQL API is temporarily unavailable")
        if status != 200:
            raise ShopifyProviderError(f"Shopify GraphQL request failed with HTTP {status}")
        payload = self._json(response, "GraphQL API")
        if payload.get("errors"):
            raise ShopifyProviderError("Shopify GraphQL returned operation errors")
        return payload

    def start_shipping_label_purchase(self, secret_ref: str, purchase_input: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(purchase_input, dict):
            raise ValueError("Shipping label purchase input must be an object")
        required = {
            "fulfillmentOrderId": str(purchase_input.get("fulfillmentOrderId") or "").strip(),
            "shippingDatetime": str(purchase_input.get("shippingDatetime") or "").strip(),
        }
        if not all(required.values()):
            raise ValueError("Shipping label purchase requires fulfillmentOrderId and shippingDatetime")
        query = """
mutation ESPShippingLabelPurchase($shippingLabelPurchase: ShippingLabelPurchaseInput!) {
  shippingLabelPurchase(shippingLabelPurchase: $shippingLabelPurchase) {
    shippingLabelPurchaseResult { id status done errors { code message } }
    userErrors { code field message }
  }
}
""".strip()
        payload = self.graphql(
            secret_ref,
            query=query,
            variables={"shippingLabelPurchase": purchase_input},
        )
        result = ((payload.get("data") or {}).get("shippingLabelPurchase") or {})
        user_errors = _safe_provider_errors(result.get("userErrors"))
        if user_errors:
            raise ShopifyProviderError("Shopify rejected the shipping label purchase request")
        job = result.get("shippingLabelPurchaseResult") or {}
        execution_ref = str(job.get("id") or "").strip()
        status = str(job.get("status") or "").strip().upper()
        if not execution_ref or status not in {"PENDING_PURCHASE", "PURCHASED", "PURCHASE_FAILED"}:
            raise ShopifyProviderError("Shopify returned an invalid shipping label purchase result")
        return {
            "execution_ref": execution_ref,
            "status": status,
            "done": bool(job.get("done")),
            "errors": _safe_provider_errors(job.get("errors")),
        }

    def poll_shipping_label_purchase(self, secret_ref: str, execution_ref: str) -> dict[str, Any]:
        ref = str(execution_ref or "").strip()
        if not ref.startswith("gid://shopify/ShippingLabelPurchaseResult/"):
            raise ValueError("Invalid Shopify shipping label purchase result reference")
        query = """
query ESPShippingLabelPurchaseStatus($id: ID!) {
  node(id: $id) {
    ... on ShippingLabelPurchaseResult {
      id status done errors { code message }
      shippingLabels { id }
    }
  }
}
""".strip()
        payload = self.graphql(secret_ref, query=query, variables={"id": ref})
        job = ((payload.get("data") or {}).get("node") or {})
        status = str(job.get("status") or "").strip().upper()
        if str(job.get("id") or "") != ref or status not in {"PENDING_PURCHASE", "PURCHASED", "PURCHASE_FAILED"}:
            raise ShopifyProviderError("Shopify returned an invalid shipping label status result")
        labels = job.get("shippingLabels") or []
        return {
            "execution_ref": ref,
            "status": status,
            "done": bool(job.get("done")),
            "errors": _safe_provider_errors(job.get("errors")),
            "shipping_label_ids": [str(item.get("id")) for item in labels[:20] if isinstance(item, dict) and item.get("id")],
        }

    def execute(self, action: dict, connection: dict, *, secret_ref: str) -> dict:
        action_type = str(action.get("action_type") or "")
        if action_type in {"purchase_shipping_label", "buy_label"}:
            raise ShopifyProviderError(
                "Shopify shipping-label execution is blocked until asynchronous purchase reconciliation is enabled"
            )
        raise ShopifyProviderError(f"Shopify action '{action_type}' is not implemented by this adapter")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "configured": True,
            "api_version": self.api_version,
            "requested_scope_count": len(self.requested_scopes),
            "signed_oauth_callback_required": True,
            "expiring_offline_tokens": True,
            "automatic_refresh_supported": True,
            "secret_backend_persistent": bool(getattr(self.secret_backend, "persistent", False)),
            "shipping_label_graphql_primitives": True,
            "shipping_label_runtime_execution_enabled": False,
            "raw_token_exposed": False,
        }


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def register_shopify_provider(adapter: ShopifyProviderAdapter) -> None:
    """Explicit registration hook. Deployment code must supply a real secret backend."""
    runtime.register_provider_adapter("shopify", adapter)


__all__ = [
    "ShopifyProviderAdapter",
    "ShopSecretBackend",
    "InMemoryShopSecretBackend",
    "ShopifyProviderError",
    "ShopifyReauthorizationRequired",
    "register_shopify_provider",
    "SHOPIFY_API_VERSION",
    "DEFAULT_SHOPIFY_SCOPES",
]
