from __future__ import annotations

import re
from collections.abc import Iterable
from hashlib import sha256
from typing import Any


_SCHEMA_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _http_signature(route: Any) -> tuple[str, tuple[str, ...]] | None:
    """Return an exact HTTP route signature or None for mounts/websockets/helpers."""
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    normalized = tuple(sorted(str(method).upper() for method in methods))
    return path, normalized


def duplicate_http_signatures(routes: Iterable[Any]) -> dict[tuple[str, tuple[str, ...]], list[Any]]:
    grouped: dict[tuple[str, tuple[str, ...]], list[Any]] = {}
    for route in routes:
        signature = _http_signature(route)
        if signature is None:
            continue
        grouped.setdefault(signature, []).append(route)
    return {signature: items for signature, items in grouped.items() if len(items) > 1}


def _schema_operation_id(route: Any) -> str | None:
    if getattr(route, "include_in_schema", True) is False:
        return None
    value = getattr(route, "operation_id", None) or getattr(route, "unique_id", None)
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _operation_suffix(route: Any, *, ordinal: int) -> str:
    signature = _http_signature(route)
    if signature is None:
        source = f"route|{getattr(route, 'path', '')}|{ordinal}"
    else:
        source = f"{signature[0]}|{','.join(signature[1])}|{ordinal}"
    return sha256(source.encode("utf-8")).hexdigest()[:10]


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return clean or "operation"


def _final_operation_id(route: Any, *, ordinal: int) -> str | None:
    signature = _http_signature(route)
    if signature is None or getattr(route, "include_in_schema", True) is False:
        return None
    path, methods = signature
    endpoint = getattr(route, "endpoint", None)
    name = str(getattr(route, "name", None) or getattr(endpoint, "__name__", None) or "operation")
    digest_source = f"{path}|{','.join(methods)}|{ordinal}"
    digest = sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    return f"{_slug(name)}_{digest}"


def ensure_unique_operation_ids(routes: Iterable[Any]) -> list[dict[str, Any]]:
    """Repair generated OpenAPI operation-ID collisions without changing runtime dispatch.

    This compatibility helper preserves the first generated identifier and suffixes only later
    collisions. The production OpenAPI wrapper additionally assigns deterministic identities from
    the fully composed route table so schema generation remains stable even after late compatible
    route composition.
    """

    seen: set[str] = set()
    repaired: list[dict[str, Any]] = []

    for ordinal, route in enumerate(routes):
        operation_id = _schema_operation_id(route)
        if operation_id is None:
            continue
        if operation_id not in seen:
            seen.add(operation_id)
            continue

        candidate = f"{operation_id}_{_operation_suffix(route, ordinal=ordinal)}"
        bump = 1
        while candidate in seen:
            candidate = f"{operation_id}_{_operation_suffix(route, ordinal=ordinal)}_{bump}"
            bump += 1

        route.operation_id = candidate
        route.unique_id = candidate

        endpoint = getattr(route, "endpoint", None)
        signature = _http_signature(route)
        repaired.append(
            {
                "old_operation_id": operation_id,
                "new_operation_id": candidate,
                "path": signature[0] if signature else getattr(route, "path", None),
                "methods": list(signature[1]) if signature else [],
                "name": getattr(route, "name", None),
                "endpoint": getattr(endpoint, "__qualname__", None),
                "module": getattr(endpoint, "__module__", None),
            }
        )
        seen.add(candidate)

    return repaired


def _assign_final_operation_ids(routes: Iterable[Any]) -> int:
    """Assign explicit deterministic IDs from exact final route signatures."""
    seen: set[str] = set()
    changed = 0
    for ordinal, route in enumerate(routes):
        candidate = _final_operation_id(route, ordinal=ordinal)
        if candidate is None:
            continue
        base = candidate
        bump = 1
        while candidate in seen:
            candidate = f"{base}_{bump}"
            bump += 1
        seen.add(candidate)
        previous = _schema_operation_id(route)
        route.operation_id = candidate
        route.unique_id = candidate
        if previous != candidate:
            changed += 1
    return changed


def _normalize_schema_operation_ids(schema: dict[str, Any]) -> int:
    """Guarantee per-operation uniqueness in the emitted OpenAPI document."""
    seen: set[str] = set()
    changed = 0
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return changed

    for path in sorted(paths):
        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            continue
        for method in sorted(_SCHEMA_METHODS):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            current = str(operation.get("operationId") or "operation")
            candidate = current
            if candidate in seen:
                digest = sha256(f"{path}|{method}".encode("utf-8")).hexdigest()[:12]
                candidate = f"{_slug(current)}_{digest}"
                bump = 1
                while candidate in seen:
                    candidate = f"{_slug(current)}_{digest}_{bump}"
                    bump += 1
                operation["operationId"] = candidate
                changed += 1
            seen.add(candidate)
    return changed


def _install_openapi_integrity(app: Any) -> None:
    """Make final-route identity enforcement part of every uncached schema build.

    Duplicate-operation warnings are intentionally not filtered here. The build must prove that
    the final route identities are actually unique before FastAPI generates the schema rather
    than hiding a collision behind warning suppression.
    """
    if getattr(app.state, "route_integrity_openapi_installed", False):
        return

    original_openapi = app.openapi

    def integrity_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema

        route_ids_changed = _assign_final_operation_ids(app.router.routes)
        app.openapi_schema = None
        schema = original_openapi()

        schema_ids_changed = _normalize_schema_operation_ids(schema)
        app.openapi_schema = schema
        diagnostics = app.state.route_integrity
        diagnostics["final_route_operation_ids_assigned"] = route_ids_changed
        diagnostics["schema_operation_ids_repaired"] = schema_ids_changed
        return schema

    app.openapi = integrity_openapi
    app.state.route_integrity_openapi_installed = True


def deduplicate_http_routes(app: Any) -> list[dict[str, Any]]:
    """Remove unreachable exact duplicate HTTP routes and harden schema identity.

    Starlette/FastAPI dispatches routes in registration order, so when the same exact path and
    HTTP-method set is registered twice, every later copy is unreachable. Preserve the first
    authoritative route exactly as runtime dispatch already does and remove only later exact
    copies. Mounts, websocket routes, and different method sets are untouched.

    After runtime duplicates are removed, repair compatibility collisions and install final
    deterministic OpenAPI identity enforcement. Runtime paths, methods, endpoints, dependencies
    and dispatch precedence remain unchanged.
    """

    seen: set[tuple[str, tuple[str, ...]]] = set()
    kept: list[Any] = []
    removed: list[dict[str, Any]] = []

    for route in app.router.routes:
        signature = _http_signature(route)
        if signature is None or signature not in seen:
            kept.append(route)
            if signature is not None:
                seen.add(signature)
            continue

        endpoint = getattr(route, "endpoint", None)
        removed.append(
            {
                "path": signature[0],
                "methods": list(signature[1]),
                "name": getattr(route, "name", None),
                "endpoint": getattr(endpoint, "__qualname__", None),
                "module": getattr(endpoint, "__module__", None),
            }
        )

    if removed:
        app.router.routes[:] = kept

    operation_ids_repaired = ensure_unique_operation_ids(app.router.routes)
    app.openapi_schema = None
    app.state.route_integrity = {
        "duplicates_removed": len(removed),
        "removed": removed,
        "operation_id_collisions_repaired": len(operation_ids_repaired),
        "operation_id_repairs": operation_ids_repaired,
        "final_route_operation_ids_assigned": 0,
        "schema_operation_ids_repaired": 0,
    }
    _install_openapi_integrity(app)
    return removed


__all__ = [
    "deduplicate_http_routes",
    "duplicate_http_signatures",
    "ensure_unique_operation_ids",
]
