from __future__ import annotations

import warnings

from fastapi import FastAPI

from aura_music_studio.route_integrity import (
    deduplicate_http_routes,
    duplicate_http_signatures,
    ensure_unique_operation_ids,
)


def _schema_operation_ids(schema: dict) -> list[str]:
    operation_ids: list[str] = []
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and operation.get("operationId"):
                operation_ids.append(str(operation["operationId"]))
    return operation_ids


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


def test_operation_id_repair_preserves_distinct_runtime_aliases_and_removes_schema_collision():
    sample = FastAPI()

    def alias_handler():
        return {"ok": True}

    # FastAPI normalises punctuation while generating IDs. These are distinct runtime paths but
    # intentionally share the same route name, reproducing a compatibility-alias collision.
    sample.add_api_route("/compat/path-name", alias_handler, methods=["GET"], name="compat_alias")
    sample.add_api_route("/compat/path_name", alias_handler, methods=["GET"], name="compat_alias")

    assert duplicate_http_signatures(sample.router.routes) == {}
    before = [
        getattr(route, "operation_id", None) or getattr(route, "unique_id", None)
        for route in sample.router.routes
        if getattr(route, "path", "").startswith("/compat/")
    ]
    assert len(before) == 2
    assert before[0] == before[1]

    repaired = ensure_unique_operation_ids(sample.router.routes)
    sample.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = sample.openapi()

    duplicate_warnings = [warning for warning in caught if "Duplicate Operation ID" in str(warning.message)]
    assert duplicate_warnings == []
    assert len(repaired) == 1
    assert repaired[0]["path"] == "/compat/path_name"
    assert repaired[0]["old_operation_id"] != repaired[0]["new_operation_id"]
    operation_ids = _schema_operation_ids(schema)
    assert len(operation_ids) == len(set(operation_ids))
    assert "/compat/path-name" in schema["paths"]
    assert "/compat/path_name" in schema["paths"]


def test_openapi_integrity_repairs_multi_method_schema_without_renaming_stable_id():
    sample = FastAPI()

    def shared_handler():
        return {"ok": True}

    sample.add_api_route("/multi-method", shared_handler, methods=["GET", "POST"], name="shared_handler")
    route = next(route for route in sample.router.routes if getattr(route, "path", None) == "/multi-method")
    original_id = route.unique_id

    deduplicate_http_routes(sample)
    sample.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = sample.openapi()

    duplicate_warnings = [warning for warning in caught if "Duplicate Operation ID" in str(warning.message)]
    assert duplicate_warnings == []
    get_id = schema["paths"]["/multi-method"]["get"]["operationId"]
    post_id = schema["paths"]["/multi-method"]["post"]["operationId"]
    assert get_id == original_id
    assert post_id != original_id
    assert get_id != post_id
    assert sample.state.route_integrity["schema_operation_ids_repaired"] == 1
    repair = sample.state.route_integrity["schema_operation_id_repairs"][0]
    assert repair["old_operation_id"] == original_id
    assert repair["path"] == "/multi-method"
    assert repair["method"] == "POST"


def test_canonical_production_app_has_no_duplicate_http_signatures():
    from app import app

    assert duplicate_http_signatures(app.router.routes) == {}
    diagnostics = app.state.route_integrity
    assert diagnostics["duplicates_removed"] >= 0
    assert diagnostics["duplicates_removed"] == len(diagnostics["removed"])
    assert diagnostics["operation_id_collisions_repaired"] >= 0
    assert diagnostics["operation_id_collisions_repaired"] == len(diagnostics["operation_id_repairs"])


def test_canonical_production_openapi_has_unique_operation_ids_without_duplicate_warnings():
    from app import app

    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()

    duplicate_warnings = [warning for warning in caught if "Duplicate Operation ID" in str(warning.message)]
    assert duplicate_warnings == []

    operation_ids = _schema_operation_ids(schema)
    assert operation_ids
    assert len(operation_ids) == len(set(operation_ids))
    diagnostics = app.state.route_integrity
    assert diagnostics["schema_operation_ids_repaired"] == len(diagnostics["schema_operation_id_repairs"])
