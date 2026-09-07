from __future__ import annotations

from fastapi.routing import APIRoute


WEBHOOK_PATH = "/billing/native/paypal/webhook"
CHECKOUT_PATH = "/billing/native/paypal/checkout"


def _routes(path: str):
    from app import app as production_app

    return [
        route
        for route in production_app.router.routes
        if isinstance(route, APIRoute) and route.path == path
    ]


def test_native_commerce_routes_are_mounted_once_on_production_app():
    webhook = _routes(WEBHOOK_PATH)
    checkout = _routes(CHECKOUT_PATH)

    assert len(webhook) == 1
    assert webhook[0].methods == {"POST"}
    assert webhook[0].include_in_schema is False

    assert len(checkout) == 1
    assert checkout[0].methods == {"POST"}


def test_native_paypal_webhook_stays_out_of_openapi_but_checkout_is_documented():
    from app import app as production_app

    paths = production_app.openapi().get("paths", {})
    assert WEBHOOK_PATH not in paths
    assert CHECKOUT_PATH in paths


def test_native_commerce_does_not_gain_browser_get_surface():
    for path in (WEBHOOK_PATH, CHECKOUT_PATH):
        methods = {method for route in _routes(path) for method in (route.methods or set())}
        assert methods == {"POST"}
