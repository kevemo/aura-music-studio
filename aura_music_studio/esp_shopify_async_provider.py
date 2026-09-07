from __future__ import annotations

from typing import Any

from . import esp_shop_provider_runtime as runtime
from .esp_shopify_provider import (
    SHOPIFY_FULFILLMENT_WRITE_SCOPES,
    ShopifyProviderAdapter,
    ShopifyProviderError,
)

LABEL_ACTIONS = {"purchase_shipping_label", "buy_label"}


class ShopifyAsyncProviderAdapter(ShopifyProviderAdapter):
    """Shopify adapter with safe asynchronous shipping-label execution enabled."""

    def _runtime_capabilities(self, granted: set[str]) -> list[str]:
        capabilities = list(super()._runtime_capabilities(granted))
        if "write_orders" in granted and granted.intersection(SHOPIFY_FULFILLMENT_WRITE_SCOPES):
            capabilities.append("shipping_labels")
        return sorted(set(capabilities))

    @staticmethod
    def requires_explicit_approval(action: dict) -> bool:
        return str(action.get("action_type") or "") in LABEL_ACTIONS

    @staticmethod
    def _purchase_input(action: dict) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = action.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("Shopify action payload must be an object")
        purchase_input = payload.get("shippingLabelPurchase")
        if not isinstance(purchase_input, dict):
            raise ValueError("Shopify label action requires a shippingLabelPurchase input object")
        return payload, purchase_input

    def validate_before_execute(self, action: dict, policy: dict) -> None:
        action_type = str(action.get("action_type") or "")
        if action_type not in LABEL_ACTIONS:
            raise ValueError(f"Shopify action '{action_type}' is not enabled for async execution")
        if action.get("status") != "approved":
            raise PermissionError("Shopify label purchases require explicit human approval")
        if not str(action.get("approval_note") or "").strip():
            raise PermissionError("Shopify label purchase approval requires a human approval note")

        payload, purchase_input = self._purchase_input(action)
        try:
            quantity = int(payload.get("quantity", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("Shopify label quantity must be one") from exc
        if quantity != 1:
            raise ValueError("Shopify label actions purchase exactly one label; use separate approved actions")

        if bool(purchase_input.get("notifyCustomer")) and not bool(
            policy.get("allow_customer_notifications")
        ):
            raise PermissionError("Customer notifications are disabled by the Shop safety policy")

        preferred = purchase_input.get("preferredRateSelection")
        accepts_provider_selection = payload.get("acceptShopifySelectedRate") is True
        if preferred is not None:
            if not isinstance(preferred, dict):
                raise ValueError("preferredRateSelection must be an object")
            carrier = str(preferred.get("carrierCode") or "").strip()
            service = str(preferred.get("serviceCode") or "").strip()
            if not carrier or not service:
                raise ValueError("Preferred Shopify rate requires carrierCode and serviceCode")
        elif not accepts_provider_selection:
            raise PermissionError(
                "Approve Shopify's provider-selected rate explicitly or supply preferredRateSelection"
            )

        # Validate required purchase fields before any provider call.
        if not str(purchase_input.get("fulfillmentOrderId") or "").strip():
            raise ValueError("Shopify label purchase requires fulfillmentOrderId")
        if not str(purchase_input.get("shippingDatetime") or "").strip():
            raise ValueError("Shopify label purchase requires shippingDatetime")

    @staticmethod
    def _receipt_from_status(result: dict[str, Any]) -> dict[str, Any]:
        status = str(result.get("status") or "").upper()
        execution_ref = str(result.get("execution_ref") or "").strip()
        metadata = {
            "shopify_status": status,
            "done": bool(result.get("done")),
            "errors": result.get("errors") or [],
            "shipping_label_ids": result.get("shipping_label_ids") or [],
        }
        if status == "PENDING_PURCHASE":
            return {
                "pending": True,
                "execution_ref": execution_ref,
                "metadata": metadata,
            }
        if status == "PURCHASED":
            return {
                "success": True,
                "execution_ref": execution_ref,
                "metadata": metadata,
            }
        if status == "PURCHASE_FAILED":
            return {
                "failed": True,
                "execution_ref": execution_ref,
                "metadata": metadata,
            }
        raise ShopifyProviderError("Shopify returned an unsupported shipping label status")

    def execute(self, action: dict, connection: dict, *, secret_ref: str) -> dict:
        action_type = str(action.get("action_type") or "")
        if action_type not in LABEL_ACTIONS:
            raise ShopifyProviderError(f"Shopify action '{action_type}' is not enabled")
        _payload, purchase_input = self._purchase_input(action)
        result = self.start_shipping_label_purchase(secret_ref, purchase_input)
        return self._receipt_from_status(result)

    def reconcile(
        self,
        action: dict,
        connection: dict,
        *,
        secret_ref: str,
        execution_ref: str,
    ) -> dict:
        if str(action.get("action_type") or "") not in LABEL_ACTIONS:
            raise ShopifyProviderError("This Shopify action does not support reconciliation")
        result = self.poll_shipping_label_purchase(secret_ref, execution_ref)
        return self._receipt_from_status(result)

    def diagnostics(self) -> dict[str, Any]:
        data = dict(super().diagnostics())
        data.update(
            {
                "shipping_label_runtime_execution_enabled": True,
                "shipping_label_async_reconciliation": True,
                "shipping_label_explicit_approval_required": True,
                "shipping_label_single_action_quantity": 1,
            }
        )
        return data


def register_shopify_async_provider(adapter: ShopifyAsyncProviderAdapter) -> None:
    """Explicit deployment registration; credentials remain external to source control."""
    runtime.register_provider_adapter("shopify", adapter)


__all__ = [
    "ShopifyAsyncProviderAdapter",
    "register_shopify_async_provider",
    "LABEL_ACTIONS",
]
