from __future__ import annotations

import warnings
from collections import Counter, defaultdict

from fastapi.routing import APIRoute

import app as production_entrypoint


_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


def _fresh_schema_with_warnings():
    # FastAPI >=0.137 keeps include_router() composition nested/lazy. Duplicate
    # registrations can therefore be invisible to a flat app.routes walk while
    # still being detected during OpenAPI generation. Always rebuild the schema
    # here so those warnings become a release gate rather than log noise.
    production_entrypoint.app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = production_entrypoint.app.openapi()
    duplicate_operation_warnings = sorted(
        {
            str(item.message)
            for item in caught
            if "Duplicate Operation ID" in str(item.message)
        }
    )
    return schema, duplicate_operation_warnings


def _schema_operations() -> list[tuple[str, str, str]]:
    schema = production_entrypoint.app.openapi()
    operations: list[tuple[str, str, str]] = []
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.upper() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "").strip()
            assert operation_id, f"OpenAPI operation is missing operationId: {method.upper()} {path}"
            operations.append((operation_id, method.upper(), path))
    return operations


def test_openapi_generation_has_no_duplicate_operation_id_warnings():
    _schema, duplicates = _fresh_schema_with_warnings()
    assert not duplicates, (
        "Production router composition registered duplicate operations. Under current FastAPI "
        "nested-router semantics this can shadow route ownership even when the final OpenAPI "
        "dictionary collapses to one method/path. Remove redundant parent/child mounts: "
        + " | ".join(duplicates)
    )


def test_public_openapi_operation_ids_are_unique():
    operations = _schema_operations()
    counts = Counter(operation_id for operation_id, _method, _path in operations)
    duplicates = sorted(operation_id for operation_id, count in counts.items() if count > 1)
    if duplicates:
        locations = defaultdict(list)
        for operation_id, method, path in operations:
            if operation_id in duplicates:
                locations[operation_id].append(f"{method} {path}")
        detail = "; ".join(
            f"{operation_id}: {', '.join(sorted(locations[operation_id]))}"
            for operation_id in duplicates
        )
        raise AssertionError(f"Duplicate public OpenAPI operation IDs detected: {detail}")


def test_flat_public_route_entries_do_not_repeat_method_paths():
    # This remains useful for direct APIRoute registrations. It is deliberately
    # not the only duplicate gate because FastAPI now preserves included routers
    # as nested nodes rather than flattening every child into app.routes.
    mounted: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in production_entrypoint.app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        for method in sorted(route.methods or set()):
            if method not in _HTTP_METHODS:
                continue
            mounted[(method, route.path)].append(str(route.name or route.endpoint.__name__))

    duplicates = {key: names for key, names in mounted.items() if len(names) > 1}
    assert not duplicates, (
        "Duplicate direct public method/path routes are mounted; this can shadow handlers and "
        f"create ambiguous OpenAPI output: {duplicates}"
    )
