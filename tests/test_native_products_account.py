from __future__ import annotations

from types import SimpleNamespace

from fastapi.routing import APIRoute

import aura_music_studio.native_commerce_api as commerce
from aura_music_studio.native_products import (
    AURA_OS_ENTITLEMENT,
    AURA_SEC_ENTITLEMENT,
    public_native_products,
)


class _AccessResolver:
    def __init__(self, entitlements: set[str], sources: dict[str, list[str]] | None = None):
        self.entitlements = sorted(entitlements)
        self.sources = sources or {}

    def resolve(self, user_id: str):
        snapshot = {
            "user_id": user_id,
            "membership_plan_id": "pro" if any("membership:pro" in v for v in self.sources.values()) else "free",
            "entitlements": self.entitlements,
            "membership_entitlements": sorted(
                key for key, values in self.sources.items() if "membership:pro" in values
            ),
            "purchased_entitlements": sorted(
                key for key, values in self.sources.items() if "native_purchase" in values
            ),
            "sources": self.sources,
            "device_authority_granted": False,
            "device_limit": None,
        }
        return SimpleNamespace(public_dict=lambda: snapshot)


class _History:
    def __init__(self, purchased_products: set[str] | None = None):
        self.purchased_products = purchased_products or set()

    def has_product_purchase(self, _user_id: str, product_id: str) -> bool:
        return product_id in self.purchased_products


def _member(plan_id: str = "free") -> dict:
    return {"id": "member-1", "email": "member@example.test", "plan_id": plan_id}


def test_public_native_pricing_surface_is_canonical_and_does_not_claim_auto_renewal():
    payload = commerce.native_products_pricing()

    assert payload["products"] == public_native_products()
    assert payload["currency"] == "GBP"
    assert payload["checkout_type"] == "paypal_invoice"
    assert payload["automatic_renewal_enabled"] is False


def test_account_snapshot_marks_unlimited_pro_native_products_active(monkeypatch):
    sources = {
        AURA_OS_ENTITLEMENT: ["membership:pro"],
        AURA_SEC_ENTITLEMENT: ["membership:pro"],
    }
    monkeypatch.setattr(commerce, "native_access", _AccessResolver({AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT}, sources))
    monkeypatch.setattr(commerce, "native_entitlements", _History())

    snapshot = commerce._native_product_snapshot(_member("pro"))
    products = {item["id"]: item for item in snapshot["products"]}

    assert products["aura_os"]["fully_active"] is True
    assert products["aura_sec"]["fully_active"] is True
    assert products["aura_os_sec_bundle"]["fully_active"] is True
    assert all(item["account_checkout_available"] is False for item in products.values())
    assert snapshot["access"]["device_authority_granted"] is False
    assert snapshot["payment_model"]["automatic_renewal_enabled"] is False


def test_account_snapshot_withholds_partially_overlapping_bundle_without_inventing_credit(monkeypatch):
    sources = {AURA_SEC_ENTITLEMENT: ["native_purchase"]}
    monkeypatch.setattr(commerce, "native_access", _AccessResolver({AURA_SEC_ENTITLEMENT}, sources))
    monkeypatch.setattr(commerce, "native_entitlements", _History({"aura_sec"}))

    snapshot = commerce._native_product_snapshot(_member())
    products = {item["id"]: item for item in snapshot["products"]}

    assert products["aura_sec"]["fully_active"] is True
    assert products["aura_os"]["account_checkout_available"] is True
    assert products["aura_os_sec_bundle"]["fully_active"] is False
    assert products["aura_os_sec_bundle"]["active_entitlements"] == [AURA_SEC_ENTITLEMENT]
    assert products["aura_os_sec_bundle"]["missing_entitlements"] == [AURA_OS_ENTITLEMENT]
    assert products["aura_os_sec_bundle"]["account_checkout_available"] is False
    assert products["aura_sec"]["founding_offer_available"] is False


def test_founding_offer_is_presented_only_before_verified_product_history(monkeypatch):
    monkeypatch.setattr(commerce, "native_access", _AccessResolver(set()))
    monkeypatch.setattr(commerce, "native_entitlements", _History())
    fresh = {item["id"]: item for item in commerce._native_product_snapshot(_member())["products"]}
    assert fresh["aura_sec"]["founding_offer_available"] is True

    monkeypatch.setattr(commerce, "native_entitlements", _History({"aura_sec"}))
    prior = {item["id"]: item for item in commerce._native_product_snapshot(_member())["products"]}
    assert prior["aura_sec"]["founding_offer_available"] is False


def test_native_account_and_pricing_routes_are_reachable_once_in_production():
    from app import app as production_app

    expected = {
        "/pricing/native-products": {"GET"},
        "/account/native-products.json": {"GET"},
        "/account/native-products": {"GET"},
        "/billing/native/paypal/checkout": {"POST"},
        "/billing/native/paypal/webhook": {"POST"},
    }
    for path, methods in expected.items():
        routes = [
            route
            for route in production_app.router.routes
            if isinstance(route, APIRoute) and route.path == path
        ]
        assert len(routes) == 1
        assert routes[0].methods == methods


def test_native_account_html_and_provider_webhook_stay_out_of_openapi():
    from app import app as production_app

    paths = production_app.openapi().get("paths", {})
    assert "/pricing/native-products" in paths
    assert "/account/native-products.json" in paths
    assert "/account/native-products" not in paths
    assert "/billing/native/paypal/checkout" in paths
    assert "/billing/native/paypal/webhook" not in paths
