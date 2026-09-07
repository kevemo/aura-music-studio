from __future__ import annotations

import json
import subprocess
import sys

from aura_music_studio import stripe_billing_hardening


_CREATION_COIN_GETS = {
    "/billing/creation-coins": "hardened_creation_coin_storefront",
    "/billing/creation-coins/catalog": "hardened_creation_coin_catalog",
}


def test_hardened_router_declares_one_creation_coin_get_handler_per_path():
    failures: dict[str, list[str]] = {}
    for path, expected_endpoint in _CREATION_COIN_GETS.items():
        routes = [
            route
            for route in stripe_billing_hardening.router.routes
            if getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", None) or set())
        ]
        names = [str(getattr(getattr(route, "endpoint", None), "__name__", "")) for route in routes]
        if len(routes) != 1 or names != [expected_endpoint]:
            failures[path] = names

    assert not failures, (
        "Creation Coin GET routes must have exactly one hardened handler on the hardened router; "
        f"duplicate/shadowed route assembly detected: {failures}"
    )


def test_production_app_exposes_hardened_creation_coin_get_operations():
    probe = r'''
import json
import app

wanted = {
    "/billing/creation-coins": "hardened_creation_coin_storefront",
    "/billing/creation-coins/catalog": "hardened_creation_coin_catalog",
}

schema = app.app.openapi()
result = {}
for path, expected in wanted.items():
    operation = schema.get("paths", {}).get(path, {}).get("get") or {}
    operation_id = str(operation.get("operationId") or "")
    result[path] = {
        "operation_id": operation_id,
        "matches_hardened_handler": operation_id.startswith(expected + "_"),
        "expected": expected,
    }
print(json.dumps(result, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    failures: dict[str, dict] = {}
    for path in _CREATION_COIN_GETS:
        observation = result[path]
        if not observation["matches_hardened_handler"]:
            failures[path] = observation

    assert not failures, (
        "Production OpenAPI must expose the hardened Creation Coin GET handlers as the "
        f"authoritative operations: {failures}"
    )
