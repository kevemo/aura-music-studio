from __future__ import annotations

import json
import subprocess
import sys


def _fresh_production_route_snapshot() -> list[dict]:
    """Inspect the canonical production app in a pristine interpreter.

    The full regression suite intentionally monkeypatches and mutates shared router objects in
    several focused tests. Production composition must be verified before any such process-local
    test mutation, which is exactly how uvicorn imports ``app:app`` in a real deployment.
    """

    marker = "NATIVE_ROUTE_SNAPSHOT="
    code = r'''
import json
from fastapi.routing import APIRoute
from app import app

rows = [
    {
        "path": route.path,
        "methods": sorted(route.methods or set()),
        "include_in_schema": bool(route.include_in_schema),
    }
    for route in app.router.routes
    if isinstance(route, APIRoute) and route.path == "/billing/native/paypal/webhook"
]
print("NATIVE_ROUTE_SNAPSHOT=" + json.dumps(rows, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    line = next(
        (item for item in reversed(completed.stdout.splitlines()) if item.startswith(marker)),
        None,
    )
    assert line is not None, completed.stdout + completed.stderr
    return json.loads(line[len(marker) :])


def test_native_paypal_webhook_is_mounted_once_on_pristine_production_app():
    matches = _fresh_production_route_snapshot()

    assert len(matches) == 1
    assert matches[0]["include_in_schema"] is False


def test_native_paypal_webhook_does_not_gain_browser_get_surface():
    matches = _fresh_production_route_snapshot()

    assert matches == [
        {
            "path": "/billing/native/paypal/webhook",
            "methods": ["POST"],
            "include_in_schema": False,
        }
    ]
