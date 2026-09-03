from __future__ import annotations

from fastapi.routing import APIRoute


def test_native_paypal_webhook_is_mounted_once_on_production_app():
    from app import app as production_app

    matches = [
        route
        for route in production_app.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/billing/native/paypal/webhook"
        and "POST" in (route.methods or set())
    ]

    assert len(matches) == 1
    assert matches[0].include_in_schema is False


def test_native_paypal_webhook_does_not_gain_browser_get_surface():
    from app import app as production_app

    methods = {
        method
        for route in production_app.router.routes
        if isinstance(route, APIRoute) and route.path == "/billing/native/paypal/webhook"
        for method in (route.methods or set())
    }

    assert methods == {"POST"}
