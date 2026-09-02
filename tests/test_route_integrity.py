from __future__ import annotations

import warnings

from fastapi import FastAPI

from aura_music_studio.route_integrity import deduplicate_http_routes, duplicate_http_signatures


def test_deduplicate_http_routes_preserves_first_authoritative_handler_and_other_methods():
    sample = FastAPI()

    @sample.get("/same")
    def first_get():
        return {"handler": "first"}

    @sample.get("/same")
    def unreachable_second_get():
        return {"handler": "second"}

    @sample.post("/same")
    def post_same():
        return {"handler": "post"}

    duplicates = duplicate_http_signatures(sample.router.routes)
    assert ("/same", ("GET",)) in duplicates

    removed = deduplicate_http_routes(sample)

    assert len(removed) == 1
    assert removed[0]["path"] == "/same"
    assert removed[0]["methods"] == ["GET"]
    assert duplicate_http_signatures(sample.router.routes) == {}

    matching_gets = [
        route
        for route in sample.router.routes
        if getattr(route, "path", None) == "/same" and getattr(route, "methods", None) == {"GET"}
    ]
    matching_posts = [
        route
        for route in sample.router.routes
        if getattr(route, "path", None) == "/same" and getattr(route, "methods", None) == {"POST"}
    ]
    assert len(matching_gets) == 1
    assert matching_gets[0].endpoint is first_get
    assert len(matching_posts) == 1
    assert matching_posts[0].endpoint is post_same


def test_canonical_production_app_has_no_duplicate_http_signatures():
    from app import app

    assert duplicate_http_signatures(app.router.routes) == {}
    diagnostics = app.state.route_integrity
    assert diagnostics["duplicates_removed"] >= 0
    assert diagnostics["duplicates_removed"] == len(diagnostics["removed"])


def test_canonical_production_openapi_has_unique_operation_ids_without_duplicate_warnings():
    from app import app

    # Rebuild the schema from the final post-composition route table. The production contract is
    # uniqueness itself; it must not rely on the candidate tree happening to contain a duplicate
    # just so the repair path can prove that it removed one.
    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()

    duplicate_warnings = [warning for warning in caught if "Duplicate Operation ID" in str(warning.message)]
    assert duplicate_warnings == []

    operation_ids: list[str] = []
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and operation.get("operationId"):
                operation_ids.append(str(operation["operationId"]))
    assert operation_ids
    assert len(operation_ids) == len(set(operation_ids))
