from __future__ import annotations

import importlib

from aura_music_studio.api import app as package_app


EXPECTED_ROUTES = {
    ("/economy/coin-packs", "GET"),
    ("/economy/me/balance", "GET"),
    ("/economy/me/history", "GET"),
    ("/economy/me/spending", "GET"),
    ("/economy/me/personal-spending-limits", "PUT"),
    ("/economy/me/coin-purchases", "POST"),
    ("/economy/me/gifts/send", "POST"),
    ("/owner/economy/finance-snapshot", "GET"),
    ("/owner/economy/risk-cases", "GET"),
    ("/owner/economy/operational-events", "GET"),
}


def _signatures(app):
    signatures: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            signatures.append((path, method))
    return signatures


def _assert_routes_once(app):
    signatures = _signatures(app)
    for expected in EXPECTED_ROUTES:
        assert signatures.count(expected) == 1, expected


def test_chat5_routes_are_mounted_once_on_package_application():
    _assert_routes_once(package_app)


def test_chat5_routes_survive_final_production_app_composition():
    production_module = importlib.import_module("app")
    _assert_routes_once(production_module.app)
