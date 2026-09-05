from __future__ import annotations

import importlib

from aura_music_studio.api import app as package_app
from aura_music_studio.cosmic_economy_integrations import runtime_integrations
from aura_music_studio.cosmic_economy_shared_sky import chat5_shared_sky_status


EXPECTED_ROUTES = {
    ("/economy/coin-packs", "GET"),
    ("/economy/coins", "GET"),
    ("/economy/payment-providers", "GET"),
    ("/economy/integration-status", "GET"),
    ("/economy/me/balance", "GET"),
    ("/economy/me/history", "GET"),
    ("/economy/me/spending", "GET"),
    ("/economy/me/personal-spending-limits", "PUT"),
    ("/economy/me/coin-purchases", "POST"),
    ("/economy/me/gifts/send", "POST"),
    ("/billing/creation-coins/catalog", "GET"),
    ("/billing/creation-coins", "GET"),
    ("/billing/stripe/checkout/credits", "POST"),
    ("/owner/economy/finance-snapshot", "GET"),
    ("/owner/economy/risk-cases", "GET"),
    ("/owner/economy/operational-events", "GET"),
}

LEGACY_BRIDGE_SIGNATURES = {
    ("/billing/creation-coins/catalog", "GET"),
    ("/billing/creation-coins", "GET"),
    ("/billing/stripe/checkout/credits", "POST"),
}


def _signatures(app):
    signatures: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            signatures.append((path, method))
    return signatures


def _route_modules(app):
    modules: dict[tuple[str, str], list[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        module = str(getattr(getattr(route, "endpoint", None), "__module__", ""))
        for method in getattr(route, "methods", set()) or set():
            modules.setdefault((path, method), []).append(module)
    return modules


def _assert_routes_once(app):
    signatures = _signatures(app)
    for expected in EXPECTED_ROUTES:
        assert signatures.count(expected) == 1, expected


def _assert_legacy_creation_coin_paths_use_chat5_bridge(app):
    modules = _route_modules(app)
    for signature in LEGACY_BRIDGE_SIGNATURES:
        assert modules.get(signature) == [
            "aura_music_studio.cosmic_economy_legacy_bridge"
        ], (signature, modules.get(signature))


def test_chat5_routes_are_mounted_once_on_package_application():
    _assert_routes_once(package_app)
    _assert_legacy_creation_coin_paths_use_chat5_bridge(package_app)


def test_chat5_routes_survive_final_production_app_composition():
    production_module = importlib.import_module("app")
    _assert_routes_once(production_module.app)
    _assert_legacy_creation_coin_paths_use_chat5_bridge(production_module.app)


def test_merged_shared_sky_live_authority_is_bound_to_chat5():
    status = chat5_shared_sky_status()
    assert status["state"] == "registered", status
    assert status["adapter"] == "SharedSkyGiftLiveSessionDirectory", status
    assert type(runtime_integrations.live_sessions).__name__ == "SharedSkyGiftLiveSessionDirectory"
