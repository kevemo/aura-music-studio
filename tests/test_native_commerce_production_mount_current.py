from __future__ import annotations

from fastapi.routing import APIRoute


def _native_routes():
    from app import app as production_app

    return [
        route
        for route in production_app.router.routes
        if isinstance(route, APIRoute) and route.path == "/billing/native/paypal/webhook"
    ]


def test_native_paypal_webhook_is_mounted_once_on_production_app():
    matches = _native_routes()

    assert len(matches) == 1
    assert matches[0].methods == {"POST"}
    assert matches[0].include_in_schema is False


def test_native_paypal_webhook_is_absent_from_openapi_schema():
    from app import app as production_app

    schema = production_app.openapi()
    assert "/billing/native/paypal/webhook" not in schema.get("paths", {})


def test_native_paypal_webhook_does_not_gain_browser_get_surface():
    methods = {method for route in _native_routes() for method in (route.methods or set())}
    assert methods == {"POST"}
