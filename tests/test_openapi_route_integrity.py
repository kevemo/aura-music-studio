from __future__ import annotations

from collections import Counter, defaultdict

from fastapi.routing import APIRoute

import app as production_entrypoint


_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


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


def test_application_does_not_mount_duplicate_public_method_paths():
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
        "Duplicate public method/path routes are mounted; this can shadow handlers and create "
        f"ambiguous OpenAPI output: {duplicates}"
    )
