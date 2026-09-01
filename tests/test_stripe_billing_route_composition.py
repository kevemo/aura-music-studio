from __future__ import annotations

import app as production_entrypoint


_CREATION_COIN_GETS = {
    "/billing/creation-coins": "hardened_creation_coin_storefront",
    "/billing/creation-coins/catalog": "hardened_creation_coin_catalog",
}


def _matching_routes(path: str, method: str = "GET") -> list:
    return [
        route
        for route in production_entrypoint.app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    ]


def test_creation_coin_get_routes_have_one_authoritative_production_handler_each():
    failures: dict[str, list[str]] = {}
    for path, expected_endpoint in _CREATION_COIN_GETS.items():
        routes = _matching_routes(path)
        names = [str(getattr(getattr(route, "endpoint", None), "__name__", "")) for route in routes]
        if len(routes) != 1 or names != [expected_endpoint]:
            failures[path] = names

    assert not failures, (
        "Creation Coin GET routes must have exactly one hardened production handler; "
        f"duplicate/shadowed route assembly detected: {failures}"
    )


def test_creation_coin_openapi_surface_remains_present_after_deduplication():
    paths = production_entrypoint.app.openapi().get("paths", {})
    for path in _CREATION_COIN_GETS:
        assert "get" in paths.get(path, {}), f"Creation Coin route disappeared from production OpenAPI: {path}"
