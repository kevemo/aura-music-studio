from __future__ import annotations

from fastapi.routing import APIRoute


def test_native_paypal_webhook_exists_on_initial_production_import():
    from app import app as production_app

    matches = [
        route
        for route in production_app.router.routes
        if isinstance(route, APIRoute) and route.path == "/billing/native/paypal/webhook"
    ]
    assert len(matches) == 1
    assert matches[0].methods == {"POST"}
    assert matches[0].include_in_schema is False
