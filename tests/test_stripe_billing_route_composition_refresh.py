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


def test_production_app_has_one_authoritative_creation_coin_get_handler_per_path():
    probe = r'''
import json
import app

wanted = {
    "/billing/creation-coins": "hardened_creation_coin_storefront",
    "/billing/creation-coins/catalog": "hardened_creation_coin_catalog",
}


def walk(routes):
    for route in routes:
        yield route
        nested = getattr(route, "routes", None)
        if nested:
            yield from walk(nested)


all_routes = list(walk(app.app.routes))
result = {}
for path, expected in wanted.items():
    routes = [
        route
        for route in all_routes
        if getattr(route, "path", None) == path
        and "GET" in (getattr(route, "methods", None) or set())
    ]
    result[path] = {
        "names": [str(getattr(getattr(route, "endpoint", None), "__name__", "")) for route in routes],
        "openapi_get": "get" in app.app.openapi().get("paths", {}).get(path, {}),
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
    for path, expected_endpoint in _CREATION_COIN_GETS.items():
        observation = result[path]
        if observation["names"] != [expected_endpoint] or not observation["openapi_get"]:
            failures[path] = observation

    assert not failures, (
        "Production application must expose exactly one hardened Creation Coin GET handler per path "
        f"and retain the OpenAPI surface: {failures}"
    )
