from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _fresh_production_route_snapshot() -> dict:
    """Inspect this checkout's canonical production app in a pristine interpreter.

    The full regression suite intentionally monkeypatches and mutates shared router objects in
    several focused tests. It can also change process-local paths. Production composition must be
    verified from the repository root before any such state can affect module resolution, which is
    equivalent to how the deployment starts ``uvicorn app:app`` from the application checkout.
    """

    marker = "NATIVE_ROUTE_SNAPSHOT="
    code = r'''
import json
import app as production_entrypoint
from aura_music_studio.native_commerce_api import router as source_router

TARGET = "/billing/native/paypal/webhook"

def describe(route):
    endpoint = getattr(route, "endpoint", None)
    return {
        "type": f"{type(route).__module__}.{type(route).__name__}",
        "path": getattr(route, "path", None),
        "methods": sorted(getattr(route, "methods", None) or set()),
        "include_in_schema": bool(getattr(route, "include_in_schema", False)),
        "endpoint_module": getattr(endpoint, "__module__", None),
        "endpoint_name": getattr(endpoint, "__name__", None),
    }

payload = {
    "app_file": production_entrypoint.__file__,
    "source": [describe(route) for route in source_router.routes if getattr(route, "path", None) == TARGET],
    "production": [describe(route) for route in production_entrypoint.app.router.routes if getattr(route, "path", None) == TARGET],
}
print("NATIVE_ROUTE_SNAPSHOT=" + json.dumps(payload, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    line = next(
        (item for item in reversed(completed.stdout.splitlines()) if item.startswith(marker)),
        None,
    )
    assert line is not None, completed.stdout + completed.stderr
    snapshot = json.loads(line[len(marker) :])
    assert Path(snapshot["app_file"]).resolve() == (_REPO_ROOT / "app.py").resolve()
    return snapshot


def test_native_paypal_webhook_source_and_production_routes_are_exactly_verified():
    snapshot = _fresh_production_route_snapshot()

    expected = {
        "type": "fastapi.routing.APIRoute",
        "path": "/billing/native/paypal/webhook",
        "methods": ["POST"],
        "include_in_schema": False,
        "endpoint_module": "aura_music_studio.native_commerce_api",
        "endpoint_name": "native_paypal_webhook",
    }

    # The source router owns exactly one verified provider webhook, and canonical production
    # composition must preserve that exact endpoint identity rather than shadowing it with a
    # compatibility or browser-owned handler.
    assert len(snapshot["source"]) == 1
    assert snapshot["source"][0] == {**expected, "include_in_schema": True}
    assert snapshot["production"] == [expected]


def test_native_paypal_webhook_does_not_gain_browser_get_surface():
    snapshot = _fresh_production_route_snapshot()
    assert snapshot["production"][0]["methods"] == ["POST"]
